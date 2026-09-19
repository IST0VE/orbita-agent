/**
 * Правила очистки манифеста, применённые к тому каналу, которым приходят данные.
 *
 * `redaction` объявлен в манифесте и реализован на сервере (`redaction.py`,
 * `EventNormalizer`). Но события нормализатора в интерфейс не идут: фронтенд
 * получает `values/updates/tasks` напрямую через SDK LangGraph и строит свои
 * события сам. В этом пути правила манифеста не применялись ни разу — ни к
 * состоянию, ни к карточке узла, ни к выгрузке событий из консоли. Объявленный
 * контракт очистки просто не выполнялся.
 *
 * Здесь он выполняется на том же наборе режимов и с той же семантикой путей,
 * что и на сервере, чтобы одно правило значило одно и то же с обеих сторон.
 *
 * Это не граница безопасности. Данные уже в браузере: клиентская очистка
 * убирает их с экрана и из выгрузки, но не отменяет необходимости чистить их
 * на сервере — там, где решается, что вообще уедет в браузер.
 */

import type { RedactionRule } from "./types";

export type { RedactionRule };

export const MASK = "********";

/** Удалить ключ целиком — отличается от «положить undefined». */
const REMOVE = Symbol("remove");

/**
 * Правила, действующие всегда.
 *
 * Тот же список, что в `src/agent/ui_engine/redaction.py`: расхождение между
 * серверным и клиентским набором означало бы, что одно и то же поле видно
 * по-разному в зависимости от того, каким путём оно приехало.
 */
export const DEFAULT_RULES: readonly RedactionRule[] = [
  { path: "authorization", mode: "remove" },
  { path: "headers.authorization", mode: "remove" },
  { path: "headers.cookie", mode: "remove" },
  { path: "cookies", mode: "remove" },
  { path: "api_key", mode: "mask" },
  { path: "*.api_key", mode: "mask" },
  { path: "messages.*.response_metadata.raw_prompt", mode: "remove" },
  { path: "raw_provider_request", mode: "remove" },
  { path: "raw_provider_response", mode: "remove" },
];

/** Чем было значение, если само значение показывать нельзя. */
function metadata(value: unknown): { type: string; size?: number } {
  if (value === null) return { type: "null" };
  if (Array.isArray(value)) return { type: "array", size: value.length };
  if (typeof value === "string") return { type: "string", size: value.length };
  if (typeof value === "object") return { type: "object", size: Object.keys(value).length };
  return { type: typeof value };
}

function replacement(value: unknown, rule: RedactionRule, roles: Set<string>): unknown {
  switch (rule.mode) {
    case "remove":
      return REMOVE;
    case "mask":
      return MASK;
    case "truncate": {
      const limit = Math.max(0, Math.min(Number(rule.max_length ?? 256) || 0, 100_000));
      const source = String(value);
      return source.length <= limit ? source : `${source.slice(0, limit)}…`;
    }
    case "metadata_only":
      return metadata(value);
    case "role": {
      const allowed = new Set((rule.roles ?? []).map(String));
      return [...roles].some((role) => allowed.has(role)) ? value : REMOVE;
    }
    default:
      return value;
  }
}

/**
 * Обход по токенам пути с сохранением ссылок.
 *
 * Возвращает прежний объект, если правило ничего не нашло. Это не
 * оптимизация ради красоты: снимок состояния сверяется по ссылкам и
 * структурно на каждом кадре потока, и новая копия на каждое правило
 * означала бы лишний цикл reconcile на каждом обновлении.
 */
function walk(
  node: unknown,
  tokens: string[],
  rule: RedactionRule,
  roles: Set<string>,
): { changed: boolean; value: unknown } {
  if (node === null || typeof node !== "object" || !tokens.length) {
    return { changed: false, value: node };
  }
  const token = tokens[0];
  const rest = tokens.slice(1);

  if (Array.isArray(node)) {
    // По списку ходит только `*`: числовой индекс в правиле означал бы
    // зависимость очистки от порядка сообщений, а он меняется каждый ход.
    if (token !== "*") return { changed: false, value: node };
    let changed = false;
    const next = node.map((item) => {
      if (rest.length) {
        const result = walk(item, rest, rule, roles);
        changed = changed || result.changed;
        return result.value;
      }
      const value = replacement(item, rule, roles);
      // Удаление элемента списка — `null` на его месте: сдвиг индексов сам по
      // себе испортил бы соседние правила. Так же поступает и сервер.
      if (value === REMOVE) {
        changed = true;
        return null;
      }
      if (!Object.is(value, item)) changed = true;
      return value;
    });
    return changed ? { changed: true, value: next } : { changed: false, value: node };
  }

  const record = node as Record<string, unknown>;
  const keys = token === "*"
    ? Object.keys(record)
    : Object.prototype.hasOwnProperty.call(record, token) ? [token] : [];
  if (!keys.length) return { changed: false, value: node };

  let copy: Record<string, unknown> | null = null;
  const edit = () => (copy ??= { ...record });
  for (const key of keys) {
    if (rest.length) {
      const result = walk(record[key], rest, rule, roles);
      if (result.changed) edit()[key] = result.value;
      continue;
    }
    const value = replacement(record[key], rule, roles);
    if (value === REMOVE) {
      delete edit()[key];
      continue;
    }
    if (!Object.is(value, record[key])) edit()[key] = value;
  }
  return copy ? { changed: true, value: copy } : { changed: false, value: node };
}

/**
 * Очищенное значение. Исходное не меняется; неизменённое возвращается как есть.
 */
export function redact<T>(
  value: T,
  rules: Iterable<RedactionRule> = [],
  options: { roles?: Iterable<string>; includeDefaults?: boolean } = {},
): T {
  const active = options.includeDefaults === false
    ? [...rules]
    : [...DEFAULT_RULES, ...rules];
  const roles = new Set(options.roles ? [...options.roles].map(String) : []);
  let current: unknown = value;
  for (const rule of active) {
    if (!rule || typeof rule.path !== "string" || !rule.path) continue;
    const tokens = rule.path.split(".").filter(Boolean);
    if (!tokens.length) continue;
    current = walk(current, tokens, rule, roles).value;
  }
  return current as T;
}

/** Готовая функция очистки под конкретный манифест. */
export function redactor(rules: readonly RedactionRule[] | undefined) {
  const declared = rules ?? [];
  return <T,>(value: T): T => redact(value, declared);
}
