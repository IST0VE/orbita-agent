/**
 * Клиент к серверу агента.
 *
 * Адрес относительный: dev-сервер Vite проксирует и `/api/*`, и роуты
 * LangGraph на один и тот же процесс, поэтому браузер видит один источник.
 * Переопределяется через VITE_API_URL, если фронт отдаётся отдельно.
 */

export const API_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? "";
import { authorizedFetch } from "./auth";

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("content-type", "application/json");
  const res = await authorizedFetch(API_URL + path, { ...init, headers });
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error((body as { error?: string })?.error ?? `HTTP ${res.status}`);
  }
  return body as T;
}

/* ------------------------------------------------------------------ */
/* Настройки                                                           */
/* ------------------------------------------------------------------ */

export type SettingKind = "text" | "int" | "float" | "bool" | "enum";

export type Setting = {
  name: string;
  description: string;
  default: string;
  kind: SettingKind;
  /** Значение секретное: с сервера приезжает маска, а не оно само. */
  secret: boolean;
  /** false для имён, которые правятся только на сервере (PYTHONPATH и такие). */
  editable: boolean;
  /** Комментарий над переменной в самом `.env`: его пишет оператор. */
  comment: string;
  /** В `.env` что-то лежит. Для секрета это единственный способ узнать. */
  filled: boolean;
  /** Для секрета — фиксированная маска `********`. */
  value: string;
  choices?: string[];
  /**
   * Ограничения из объявления настройки (`agent/settings_schema.py`).
   * Показываются подсказкой: отказ сервера «значение должно быть не меньше 0»
   * приходит после сохранения, а знать об этом нужно до.
   */
  minimum?: number;
  maximum?: number;
};

export type SettingsSection = { title: string; fields: Setting[] };

/** Одна применённая настройка: значение процесса, а не файла. */
export type AppliedSetting = {
  name: string;
  /** Для секрета — фиксированная маска. */
  value: string;
  secret: boolean;
  /** `окружение` сильнее файла, `файл` читается при старте, `умолчание` — из кода. */
  source: string;
  /** Файл разошёлся с процессом: нужен перезапуск. */
  restart_required: boolean;
};
export type SettingsDoc = {
  path: string;
  sections: SettingsSection[];
  /** Раздел, в который попадёт переменная, заведённая из интерфейса. */
  new_section: string;
  /** Что применено прямо сейчас; значения секретов замаскированы. */
  applied?: AppliedSetting[];
  /** Хоть одна настройка в файле отличается от применённой. */
  restart_required?: boolean;
  note?: string;
};

export const loadSettings = () => json<SettingsDoc>("/api/settings");

export type SaveResult = {
  saved: string[];
  path: string;
  restart_required: string[];
};

/**
 * Отправляются только изменённые поля.
 *
 * Нетронутый секрет не присылается вообще — в браузере от него одна маска, и
 * отправить её назад значило бы затереть настоящий ключ звёздочками. Пустая
 * строка поэтому однозначно означает «стереть значение».
 */
export const saveSettings = (
  values: Record<string, string>,
  comments: Record<string, string> = {},
) =>
  json<SaveResult>("/api/settings", {
    method: "PUT",
    body: JSON.stringify({ values, comments }),
  });

/* ------------------------------------------------------------------ */
/* Папки задач                                                         */
/* ------------------------------------------------------------------ */

export type TaskFile = {
  name: string;
  size: number;
  /** Текстовый файл: только такие агент умеет читать. */
  text: boolean;
  /** Схема draw.io: не текст для роли, но материал для графа схем. */
  diagram: boolean;
  suffix: string;
};

export type Task = {
  name: string;
  title: string;
  /** `output` — папка публикации: готовые документы вместо сырья. */
  kind?: "input" | "output";
  files: TaskFile[];
};

export const loadTasks = () => json<{ tasks: Task[] }>("/api/inputs");

export const createTask = (name: string) =>
  json<Task>("/api/inputs", { method: "POST", body: JSON.stringify({ name }) });

export const readTaskFile = (task: string, name: string) =>
  json<{ task: string; name: string; text: string }>(
    `/api/inputs/${encodeURIComponent(task)}/file?name=${encodeURIComponent(name)}`,
  );

/* ------------------------------------------------------------------ */
/* Опубликованные документы                                            */
/* ------------------------------------------------------------------ */

export type PublishedDoc = {
  /** Имя файла в папке публикации — по нему же читается содержимое. */
  name: string;
  /** Заголовок страницы: первая строка документа, а не имя файла. */
  title: string;
  size: number;
  /** Время записи, секунды эпохи. */
  modified: number;
};

export type Published = {
  /** Цель публикации на сейчас: file, confluence, none. */
  target: string;
  /** Папка файловой цели — показывается в подвале панели. */
  dir: string;
  documents: PublishedDoc[];
};

/**
 * Список берётся у сервера, а не собирается из состояния треда: документы
 * копятся от прогона к прогону, и прошлые ходы в состоянии текущего не лежат.
 */
export const loadPublished = () => json<Published>("/api/published");

export const readPublishedFile = (name: string) =>
  json<{ name: string; text: string }>(
    `/api/published/file?name=${encodeURIComponent(name)}`,
  );

/* ------------------------------------------------------------------ */
/* Агенты                                                              */
/* ------------------------------------------------------------------ */

export type Assistant = {
  assistant_id: string;
  graph_id: string;
  name?: string;
  description?: string;
  created_at?: string;
};

/**
 * Список агентов берётся у сервера, а не перечисляется во фронте: графы
 * заводятся в `langgraph.json`, и вторая их копия здесь разошлась бы с первой
 * ровно в тот день, когда появится второй агент.
 */
export async function loadAssistants(): Promise<Assistant[]> {
  const res = await authorizedFetch(API_URL + "/assistants/search", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ limit: 100, offset: 0 }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as Assistant[];
}

/**
 * Состояние связи. Отказ по токену — не то же самое, что упавший сервер:
 * фоновая проверка не спрашивает токен, и без отдельного значения оператор
 * читал бы просроченный токен как поломку сервера и чинил бы не то.
 */
export type ServerStatus = "ok" | "offline" | "unauthorized";

export const checkServer = (signal?: AbortSignal): Promise<ServerStatus> =>
  authorizedFetch(API_URL + "/info", {
    signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(5000)]) : AbortSignal.timeout(5000),
  }, false)
    .then((r): ServerStatus => (r.ok ? "ok" : r.status === 401 || r.status === 403 ? "unauthorized" : "offline"))
    .catch((): ServerStatus => "offline");

/* ------------------------------------------------------------------ */
/* Журнал сервера                                                      */
/* ------------------------------------------------------------------ */

/** Запись `logging` сервера; секреты вычищены ещё при записи (`agent/logbook.py`). */
export type ServerLogRecord = {
  /** Место записи в журнале — по нему записи сводятся при опросе. */
  id: number;
  /** Номер последнего изменения: повтор той же записи его увеличивает. */
  seq: number;
  time: string;
  last_time: string;
  repeats: number;
  level: "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL" | string;
  logger: string;
  message: string;
  /** Трассировка, если запись сделана вместе с исключением. */
  exception: string;
  thread_id: string;
  run_id: string;
  graph_id: string;
  node: string;
  fields: Record<string, string>;
};

/** Сведения о процессе для отчёта: версии и модель, без адресов и ключей. */
export type ServerInfo = {
  app: string;
  langgraph: string;
  langgraph_api: string;
  python: string;
  platform: string;
  provider: string;
  model: string;
};

export type ServerLog = {
  records: ServerLogRecord[];
  /** Передаётся следующим `after`: опрос забирает только новое. */
  next: number;
  truncated: boolean;
  evicted: number;
  started_at: string;
  server: ServerInfo;
};

export type ServerLogQuery = {
  after?: number;
  level?: "debug" | "info" | "warning" | "error";
  threadId?: string;
  limit?: number;
};

export const loadServerLog = ({ after = 0, level = "info", threadId, limit }: ServerLogQuery = {}) => {
  const query = new URLSearchParams({ after: String(after), level });
  if (threadId) query.set("thread_id", threadId);
  if (limit) query.set("limit", String(limit));
  return json<ServerLog>(`/api/logs?${query}`);
};
