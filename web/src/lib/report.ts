/**
 * Отчёт об ошибке: один текст, который оператор пересылает разработчику.
 *
 * Текст, а не JSON и не снимок экрана. Его вставляют в мессенджер или письмо,
 * читают глазами и ищут по нему в коде: трассировка должна остаться
 * трассировкой, а идентификатор треда — строкой, которую можно найти в
 * журнале сервера. Отсюда и формат: шапка с тем, где и на чём это случилось,
 * затем три списка — что видел браузер, какие узлы упали, что записал сервер.
 *
 * Отчётов два размера. Короткий уходит в буфер обмена и должен влезать в
 * сообщение: только предупреждения и ошибки, свежие, с хвостом трассировки.
 * Полный уходит файлом: всё, что загружено, без обрезки.
 *
 * Секретов здесь нет не потому, что их вычищает этот модуль: сервер вычистил
 * их при записи (`agent/logbook.py`), а браузер ключей не видит вовсе.
 */

import type { ServerInfo, ServerLogRecord } from "../api";
import type { ClientEntry } from "./clientLog.ts";

export type ReportFailure = { time?: string; node: string; message: string };

export type ReportContext = {
  now: Date;
  page: string;
  userAgent: string;
  connection?: string;
  scenario?: string;
  graphId?: string;
  threadId?: string | null;
  runId?: string | null;
  runStatus?: string;
  runError?: string;
  failures?: readonly ReportFailure[];
  client: readonly ClientEntry[];
  server?: { info?: ServerInfo; startedAt?: string; records: readonly ServerLogRecord[] };
  /** Журнал сервера не получен: причина вместо записей. */
  serverError?: string;
};

const COMPACT = { client: 15, failures: 10, server: 25, detailLines: 12, traceLines: 30 };

const LEVEL_RANK: Record<string, number> = { DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40, CRITICAL: 50 };
export const levelRank = (level: string) => LEVEL_RANK[level] ?? 20;

const RUN_STATUS: Record<string, string> = {
  idle: "не запускался",
  queued: "в очереди",
  running: "идёт",
  interrupted: "ждёт решения",
  completed: "завершён",
  failed: "ошибка",
  cancelled: "остановлен",
};

const pad = (value: number) => String(value).padStart(2, "0");

function stamp(date: Date): string {
  const offset = -date.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  const zone = `UTC${sign}${pad(Math.floor(Math.abs(offset) / 60))}:${pad(Math.abs(offset) % 60)}`;
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
    + `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())} (${zone})`;
}

/** Время записи по часам оператора; дата — только если это не сегодня. */
export function clock(iso: string, now = new Date()): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const time = `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  return date.toDateString() === now.toDateString() ? time : `${pad(date.getDate())}.${pad(date.getMonth() + 1)} ${time}`;
}

/** Последние строки: у трассировки ценен конец, там исключение. */
function tail(text: string, lines: number | null): string {
  if (lines === null) return text;
  const all = text.split("\n");
  return all.length <= lines ? text : [`… (пропущено строк: ${all.length - lines})`, ...all.slice(-lines)].join("\n");
}

const indent = (text: string) => text.split("\n").map((line) => `    ${line}`).join("\n");

function repeated(repeats: number, last: string, now: Date): string {
  return repeats > 1 ? ` ×${repeats} (последний раз ${clock(last, now)})` : "";
}

/**
 * Одна запись сервера — так же, как в отчёте; кнопка «копировать» у строки журнала.
 *
 * `seen` — трассировки, уже напечатанные выше. Сервер LangGraph пишет об
 * упавшем прогоне дважды, «ошибка в графе» и «фоновый прогон упал», с одной и
 * той же трассировкой, и вторая её копия удваивала отчёт, ничего не добавляя.
 */
export function recordText(
  record: ServerLogRecord,
  now = new Date(),
  traceLines: number | null = null,
  seen?: Set<string>,
): string {
  const where = [
    record.node && `узел ${record.node}`,
    record.graph_id && `граф ${record.graph_id}`,
    record.thread_id && `тред ${record.thread_id}`,
    record.run_id && `прогон ${record.run_id}`,
  ].filter(Boolean);
  const head = `[${clock(record.time, now)}] ${record.level} ${record.logger}`
    + (where.length ? ` · ${where.join(" · ")}` : "")
    + repeated(record.repeats, record.last_time, now);
  const fields = Object.entries(record.fields ?? {}).map(([key, value]) => `${key}=${value}`).join(" ");
  // Сравнивается то, что будет напечатано, а не трассировка целиком: у двух
  // записей об одном сбое разные внешние кадры воркера и одинаковый хвост.
  const lines = record.exception.split("\n");
  const printed = (traceLines === null ? lines : lines.slice(-traceLines)).join("\n");
  const repeat = Boolean(record.exception) && Boolean(seen?.has(printed));
  if (record.exception) seen?.add(printed);
  return [
    head,
    record.message,
    fields ? indent(fields) : "",
    repeat
      ? indent("(трассировка та же, что у записи выше)")
      : record.exception ? indent(tail(record.exception, traceLines)) : "",
  ].filter(Boolean).join("\n");
}

export function clientText(entry: ClientEntry, now = new Date(), detailLines: number | null = null): string {
  const head = `[${clock(entry.time, now)}] ${entry.source} · ${entry.level === "error" ? "ошибка" : "предупреждение"}`
    + repeated(entry.repeats, entry.lastTime, now);
  return [head, entry.message, entry.detail ? indent(tail(entry.detail, detailLines)) : ""]
    .filter(Boolean)
    .join("\n");
}

/**
 * Записи сервера для короткого отчёта: предупреждения и ошибки, сначала
 * своего треда. Чужие записи тоже нужны — упавший при старте граф или
 * отказавший шлюз модели к треду не привязаны, — но свои важнее, и место
 * в сообщении они получают первыми.
 */
export function pickServerRecords(
  records: readonly ServerLogRecord[],
  threadId: string | null | undefined,
  limit: number,
): ServerLogRecord[] {
  const problems = records.filter((record) => levelRank(record.level) >= 30);
  const own = threadId ? problems.filter((record) => record.thread_id === threadId).slice(-limit) : [];
  const room = limit - own.length;
  const rest = room > 0 ? problems.filter((record) => !own.includes(record)).slice(-room) : [];
  return [...own, ...rest].sort((a, b) => a.id - b.id);
}

function section(title: string, body: string[], empty = "— нет"): string {
  return [`== ${title} ==`, ...(body.length ? body : [empty])].join("\n");
}

export function buildReport(context: ReportContext, { full = false } = {}): string {
  const { now } = context;
  const info = context.server?.info;
  const run = context.runId
    ? `${context.runId} — ${RUN_STATUS[context.runStatus ?? ""] ?? context.runStatus ?? "?"}`
    : context.runStatus && context.runStatus !== "idle" ? RUN_STATUS[context.runStatus] ?? context.runStatus : "";
  const header = [
    `ORBITA · ${full ? "полный журнал" : "отчёт об ошибке"}`,
    `Составлен: ${stamp(now)}`,
    `Страница: ${context.page}`,
    `Браузер: ${context.userAgent}`,
    context.connection && `Связь с сервером: ${context.connection}`,
    context.scenario && `Сценарий: ${context.scenario}${context.graphId ? ` (${context.graphId})` : ""}`,
    context.threadId && `Тред: ${context.threadId}`,
    run && `Прогон: ${run}`,
    context.runError && `Ошибка прогона: ${context.runError}`,
    info && `Сервер: ${[
      info.app && `orbita-agent ${info.app}`,
      info.langgraph && `langgraph ${info.langgraph}`,
      info.langgraph_api && `langgraph-api ${info.langgraph_api}`,
      info.python && `Python ${info.python}`,
      info.platform,
    ].filter(Boolean).join(" · ")}`,
    info && (info.provider || info.model) && `Модель: ${[info.provider, info.model].filter(Boolean).join(" / ")}`,
    context.server?.startedAt && `Журнал сервера ведётся с: ${context.server.startedAt}`,
  ].filter(Boolean).join("\n");

  const client = full ? context.client : context.client.slice(-COMPACT.client);
  const failures = full ? context.failures ?? [] : (context.failures ?? []).slice(-COMPACT.failures);
  const records = context.server?.records ?? [];
  const chosen = full ? [...records] : pickServerRecords(records, context.threadId, COMPACT.server);
  const problems = records.filter((record) => levelRank(record.level) >= 30).length;

  const serverTitle = full
    ? `Журнал сервера (${records.length})`
    : `Журнал сервера: предупреждения и ошибки (${chosen.length} из ${problems})`;
  const traces = new Set<string>();
  const serverBody = context.serverError
    ? [`не получен: ${context.serverError}`]
    : chosen.map((record) => recordText(record, now, full ? null : COMPACT.traceLines, traces));

  return [
    header,
    section(
      `Ошибки интерфейса (${client.length}${client.length < context.client.length ? ` из ${context.client.length}` : ""})`,
      client.map((entry) => clientText(entry, now, full ? null : COMPACT.detailLines)),
    ),
    // Раздел без сбоев не печатается: «сбоев узлов — нет» рядом с упавшим
    // прогоном читается как противоречие, хотя значит лишь, что браузер не
    // получил события об узле. Какой узел упал, скажет трассировка сервера.
    failures.length
      ? section(
        `Сбои узлов прогона (${failures.length})`,
        failures.map((item) => `${item.time ? `[${clock(item.time, now)}] ` : ""}${item.node}: ${item.message}`),
      )
      : "",
    section(serverTitle, serverBody),
  ].filter(Boolean).join("\n\n") + "\n";
}
