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
};

export type SettingsSection = { title: string; fields: Setting[] };
export type SettingsDoc = {
  path: string;
  sections: SettingsSection[];
  /** Раздел, в который попадёт переменная, заведённая из интерфейса. */
  new_section: string;
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
