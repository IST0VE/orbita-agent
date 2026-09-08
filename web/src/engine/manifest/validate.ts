import type { UiManifest } from "./types";

export const SUPPORTED_MANIFEST_MAJOR = "1";
const ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const FORBIDDEN = new Set(["__proto__", "prototype", "constructor"]);

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
      object(node, `nodes.${nodeId}`);
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
    id(item.widget, "state.widget");
    if (typeof item.path !== "string") throw new ManifestValidationError("state.path отсутствует");
  }
  for (const binding of (manifest.input ?? []) as unknown[]) {
    const item = object(binding, "input binding");
    id(item.id, "input.id");
    id(item.widget, "input.widget");
    if (typeof item.target !== "string") throw new ManifestValidationError("input.target отсутствует");
  }
  for (const binding of (manifest.interrupts ?? []) as unknown[]) {
    const item = object(binding, "interrupt binding");
    id(item.id, "interrupt.id");
    id(item.widget, "interrupt.widget");
    object(item.resume_schema, "interrupt.resume_schema");
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
