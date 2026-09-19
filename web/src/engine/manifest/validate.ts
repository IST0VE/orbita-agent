import { isSafeBindingPath, isUnsafeToken } from "./paths.ts";
import type { UiManifest } from "./types";

export const SUPPORTED_MANIFEST_MAJOR = "1";
const ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const FORBIDDEN = new Set(["__proto__", "prototype", "constructor"]);
/** Режимы очистки, которые понимает и сервер (`redaction.py`), и фронтенд. */
const REDACTION_MODES = new Set(["remove", "mask", "truncate", "metadata_only", "role"]);

export class ManifestValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ManifestValidationError";
  }
}

function object(value: unknown, field: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ManifestValidationError(`${field}: ожидался объект`);
  }
  return value as Record<string, unknown>;
}

function safe(value: unknown, depth = 0): void {
  if (depth > 20) throw new ManifestValidationError("манифест вложен глубже 20 уровней");
  if (!value || typeof value !== "object") return;
  if (Array.isArray(value)) {
    value.forEach((item) => safe(item, depth + 1));
    return;
  }
  for (const [key, child] of Object.entries(value)) {
    if (FORBIDDEN.has(key)) throw new ManifestValidationError(`опасный ключ объекта: ${key}`);
    safe(child, depth + 1);
  }
}

function id(value: unknown, field: string): asserts value is string {
  if (typeof value !== "string" || !ID.test(value)) {
    throw new ManifestValidationError(`${field}: некорректный идентификатор`);
  }
}

/**
 * Путь биндинга проверяется тем же разбором, которым его потом читают.
 *
 * Отклонённый манифест уходит в fallback с предупреждением в шапке. Принятый
 * и непрочитываемый — это исключение во время отрисовки поверхности, то есть
 * пустой экран: разрешение биндинга идёт до границы ошибок виджета.
 */
function path(value: unknown, field: string): asserts value is string {
  if (typeof value !== "string") throw new ManifestValidationError(`${field} отсутствует`);
  if (!isSafeBindingPath(value)) throw new ManifestValidationError(`${field}: недопустимый путь`);
}

/** Условие показа читает состояние теми же путями — и проверяется так же. */
function condition(value: unknown, field: string, depth = 0): void {
  if (value === undefined) return;
  if (depth > 20) throw new ManifestValidationError(`${field}: условие вложено глубже 20 уровней`);
  const item = object(value, field);
  if (item.path !== undefined) path(item.path, `${field}.path`);
  if (item.not !== undefined) condition(item.not, `${field}.not`, depth + 1);
  for (const key of ["all", "any"] as const) {
    const list = item[key];
    if (list === undefined) continue;
    if (!Array.isArray(list)) throw new ManifestValidationError(`${field}.${key}: ожидался массив`);
    list.forEach((child, index) => condition(child, `${field}.${key}[${index}]`, depth + 1));
  }
}

/** Виджет биндинга состояния: путь, условие показа и ничего сверх. */
function widgetBinding(value: unknown, field: string): void {
  const item = object(value, field);
  path(item.path, `${field}.path`);
  id(item.widget, `${field}.widget`);
  condition(item.visible_when, `${field}.visible_when`);
}

export function validateManifest(value: unknown, expectedGraphId?: string): UiManifest {
  const manifest = object(value, "manifest");
  safe(manifest);
  const schema = manifest.schema_version;
  if (typeof schema !== "string" || schema.split(".")[0] !== SUPPORTED_MANIFEST_MAJOR) {
    throw new ManifestValidationError(`неподдерживаемая major-версия manifest: ${String(schema)}`);
  }
  if (schema !== "1.0") {
    throw new ManifestValidationError(`неподдерживаемая версия manifest: ${schema}`);
  }
  id(manifest.graph_id, "graph_id");
  if (expectedGraphId && manifest.graph_id !== expectedGraphId) {
    throw new ManifestValidationError("graph_id манифеста не совпадает с каталогом");
  }
  if (typeof manifest.manifest_version !== "string" || !/^\d+\.\d+/.test(manifest.manifest_version)) {
    throw new ManifestValidationError("manifest_version отсутствует или некорректна");
  }
  if (
    (typeof manifest.title !== "string" || !manifest.title) &&
    (!manifest.title || typeof manifest.title !== "object" || Array.isArray(manifest.title))
  ) {
    throw new ManifestValidationError("title отсутствует");
  }
  if (manifest.nodes !== undefined) {
    const nodes = object(manifest.nodes, "nodes");
    for (const [nodeId, node] of Object.entries(nodes)) {
      id(nodeId, "node id");
      const item = object(node, `nodes.${nodeId}`);
      if (item.output !== undefined) widgetBinding(item.output, `nodes.${nodeId}.output`);
      for (const key of ["badges", "details"] as const) {
        const list = item[key];
        if (list === undefined) continue;
        if (!Array.isArray(list)) {
          throw new ManifestValidationError(`nodes.${nodeId}.${key}: ожидался массив`);
        }
        list.forEach((child, index) => widgetBinding(child, `nodes.${nodeId}.${key}[${index}]`));
      }
    }
  }
  for (const field of ["input", "state", "interrupts", "surfaces", "actions", "redaction"]) {
    const items = manifest[field];
    if (items !== undefined && !Array.isArray(items)) {
      throw new ManifestValidationError(`${field}: ожидался массив`);
    }
  }
  for (const binding of (manifest.state ?? []) as unknown[]) {
    const item = object(binding, "state binding");
    id(item.id, "state.id");
    widgetBinding(item, "state");
  }
  for (const binding of (manifest.input ?? []) as unknown[]) {
    const item = object(binding, "input binding");
    id(item.id, "input.id");
    id(item.widget, "input.widget");
    path(item.target, "input.target");
  }
  for (const binding of (manifest.interrupts ?? []) as unknown[]) {
    const item = object(binding, "interrupt binding");
    id(item.id, "interrupt.id");
    id(item.widget, "interrupt.widget");
    object(item.resume_schema, "interrupt.resume_schema");
    condition(item.match, "interrupt.match");
    if (item.bindings !== undefined) {
      if (!Array.isArray(item.bindings)) {
        throw new ManifestValidationError("interrupt.bindings: ожидался массив");
      }
      item.bindings.forEach((child, index) => widgetBinding(child, `interrupt.bindings[${index}]`));
    }
  }
  for (const action of (manifest.actions ?? []) as unknown[]) {
    condition(object(action, "action").visible_when, "action.visible_when");
  }
  // Правила очистки читаются по тем же токенам плюс `*`, и применяются они
  // уже на фронтенде (`manifest/redaction.ts`). Правило с непонятным режимом
  // молча не сработало бы — то есть обещало бы очистку, которой нет.
  for (const value of (manifest.redaction ?? []) as unknown[]) {
    const rule = object(value, "redaction rule");
    if (typeof rule.path !== "string" || !rule.path) {
      throw new ManifestValidationError("redaction.path отсутствует");
    }
    if (rule.path.split(".").some((token) => isUnsafeToken(token))) {
      throw new ManifestValidationError(`redaction.path: недопустимый путь ${rule.path}`);
    }
    if (typeof rule.mode !== "string" || !REDACTION_MODES.has(rule.mode)) {
      throw new ManifestValidationError(`redaction.mode: неподдерживаемый режим ${String(rule.mode)}`);
    }
  }
  return manifest as unknown as UiManifest;
}

export function fallbackManifest(graphId: string, title = graphId): UiManifest {
  return {
    schema_version: "1.0",
    manifest_version: "0.0.fallback",
    graph_id: graphId,
    title,
    description: "Манифест отсутствует: включён безопасный универсальный режим.",
    capabilities: { new_thread: true, stop_run: true, resume_interrupt: true },
    input: [{ id: "input", target: "messages", widget: "chat-input", required: true }],
    state: [
      { id: "state", path: "", widget: "json", title: "State", surface: "right", empty: "show" },
    ],
    surfaces: [
      { id: "main", widgets: [] },
      { id: "right", widgets: ["state"] },
      { id: "bottom", widgets: ["timeline"] },
      { id: "modal", widgets: ["interrupt"] },
    ],
    // Без списка действий диспетчер отклонил бы любое из них, и незнакомый
    // граф открывался бы только на чтение. Fallback обещает обратное: чат,
    // остановку хода и универсальный ответ на прерывание.
    actions: [
      { id: "new-thread", kind: "thread.create", label: "Новый тред" },
      { id: "start-run", kind: "run.start", label: "Запустить" },
      { id: "stop-run", kind: "run.stop", label: "Остановить" },
      { id: "resume-interrupt", kind: "interrupt.resume", label: "Продолжить" },
    ],
  };
}

export function localized(value: UiManifest["title"] | undefined, locale = "ru"): string {
  if (typeof value === "string") return value;
  if (!value) return "";
  return value[locale] ?? value.ru ?? Object.values(value)[0] ?? "";
}
