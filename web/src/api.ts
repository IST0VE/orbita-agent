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
    const error = new Error((body as { error?: string })?.error ?? `HTTP ${res.status}`);
    // Код отказа сервера — по нему отличают «чата нет» от сбоя, не разбирая текст.
    const code = (body as { error_code?: string })?.error_code;
    if (code) error.name = code;
    throw error;
  }
  return body as T;
}

/* ------------------------------------------------------------------ */
/* Кто вошёл                                                           */
/* ------------------------------------------------------------------ */

/**
 * Пользователь с точки зрения сервера. `admin` — правит настройки сервера и
 * читает его журнал; `service` — вход админ-токеном, а не человеком: у него
 * нет личных подключений, Jira он читает общим токеном из `.env`.
 */
export type Me = { subject: string; name: string; admin: boolean; service: boolean };

export const loadMe = () => json<Me>("/api/me");

/* ------------------------------------------------------------------ */
/* Мои подключения                                                     */
/* ------------------------------------------------------------------ */

export type ConnectionCheck = { ok: boolean; detail: string; checked_at?: string };

/** Одно личное поле. `token` — пропуск, `email` — часть пропуска, `place` — куда писать. */
export type ConnectionField = {
  name: string;
  kind: "token" | "email" | "place";
  label: string;
  hint: string;
  /** Сам токен с сервера не приходит никогда — только «задан» и когда. */
  secret: boolean;
  filled: boolean;
  value: string;
  /** Общее значение из `.env`: его возьмёт прогон, пока своё пусто. Только у `place`. */
  default: string;
  updated_at: string | null;
  error: string | null;
};

export type Connection = {
  id: "jira" | "confluence";
  title: string;
  /** Куда уйдёт токен: адрес задаёт администратор, и видеть его надо до вставки. */
  base_url: string;
  /** Личный токен задан. */
  connected: boolean;
  fields: ConnectionField[];
  check: ConnectionCheck | null;
};

export type ConnectionsDoc = {
  /** Почему личные подключения выключены на сервере; null — включены. */
  unavailable: string | null;
  systems: Connection[];
  check?: ConnectionCheck;
};

export const loadConnections = () => json<ConnectionsDoc>("/api/me/connections");

/** Как у настроек: только тронутые поля, пустая строка стирает значение. */
export const saveConnections = (values: Record<string, string>) =>
  json<ConnectionsDoc>("/api/me/connections", { method: "PUT", body: JSON.stringify({ values }) });

export const checkConnection = (system: string) =>
  json<ConnectionsDoc>(`/api/me/connections/${encodeURIComponent(system)}/check`, { method: "POST" });

export const forgetConnection = (system: string) =>
  json<ConnectionsDoc>(`/api/me/connections/${encodeURIComponent(system)}`, { method: "DELETE" });

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
  /** Почему поле закрыто, если его задаёт развёртывание: «задаёт docker-compose.yml». */
  locked?: string;
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
  /** Как применить сохранённое: «перезапустите сервер агента» или команда Compose. */
  apply?: string;
};

export const loadSettings = () => json<SettingsDoc>("/api/settings");

export type SaveResult = {
  saved: string[];
  path: string;
  restart_required: string[];
  apply?: string;
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

/* ------------------------------------------------------------------ */
/* Чаты и их файлы                                                     */
/* ------------------------------------------------------------------ */

/**
 * Чат — это тред LangGraph. Список, создание и название идут в API самого
 * сервера: владельца треда там проверяют правила `auth.py`, и чужих тредов
 * поиск не вернёт. Файлы чата — свои роуты `/api/chats/*`: у LangGraph
 * файлов нет, а владельца треда они сверяют так же.
 */
export type Chat = {
  thread_id: string;
  /** Пусто — чату ещё не дали названия: первый запрос его не задавал. */
  title: string;
  created_at?: string;
  updated_at?: string;
  status?: string;
};

export type ChatFile = {
  name: string;
  size: number;
  text?: boolean;
  diagram?: boolean;
  suffix?: string;
  /** Загрузка заменила файл с тем же именем. */
  replaced?: boolean;
};

export type ChatFiles = {
  thread_id: string;
  files: ChatFile[];
  limits: { max_bytes: number; max_files: number; suffixes: string[] };
};

/** Что можно добавить в чат копией: общие примеры и свои опубликованные документы. */
export type Library = {
  examples: Array<{ name: string; title: string; files: ChatFile[] }>;
  published: Array<{ name: string; title: string; size: number; modified: number }>;
};

type ThreadRow = {
  thread_id: string;
  created_at?: string;
  updated_at?: string;
  status?: string;
  metadata?: Record<string, unknown> | null;
  extracted?: { first?: unknown } | null;
};

/** Сколько знаков первого запроса хватает, чтобы узнать чат в списке. */
const TITLE_CHARS = 80;

/**
 * Название чата из его первого запроса — для чатов, заведённых до того, как
 * у чата появилось своё название. Нода контекста дописывает к запросу справку
 * и список файлов после строки `---`: в названии их быть не должно.
 */
export function titleOf(text: string): string {
  const question = text.split("\n\n---\n")[0];
  const flat = question.split(/\s+/).join(" ").trim();
  return flat.length > TITLE_CHARS ? `${flat.slice(0, TITLE_CHARS - 1)}…` : flat;
}

const chatRow = (row: ThreadRow): Chat => {
  const own = typeof row.metadata?.title === "string" ? row.metadata.title : "";
  const first = row.extracted?.first;
  return {
    thread_id: row.thread_id,
    title: own || (typeof first === "string" ? titleOf(first) : ""),
    created_at: row.created_at,
    updated_at: row.updated_at,
    status: row.status,
  };
};

/** Свои чаты этого сценария, свежие сверху. Историю сообщений не тянем: только шапки. */
export async function searchChats(graphId: string): Promise<Chat[]> {
  const rows = await json<ThreadRow[]>("/threads/search", {
    method: "POST",
    body: JSON.stringify({
      metadata: { graph_id: graphId },
      limit: 200,
      sort_by: "updated_at",
      sort_order: "desc",
      select: ["thread_id", "created_at", "updated_at", "status", "metadata"],
      // Первый запрос — название для старых чатов, у которых своего нет.
      extract: { first: "values.messages[0].content" },
    }),
  });
  return rows.map(chatRow);
}

/** Завести чат заранее — до первого прогона, чтобы было куда загрузить файлы. */
export async function createChat(graphId: string, title = ""): Promise<Chat> {
  const row = await json<ThreadRow>("/threads", {
    method: "POST",
    body: JSON.stringify({ metadata: { graph_id: graphId, title } }),
  });
  return chatRow(row);
}

export async function renameChat(threadId: string, title: string): Promise<Chat> {
  const row = await json<ThreadRow>(`/threads/${encodeURIComponent(threadId)}`, {
    method: "PATCH",
    body: JSON.stringify({ metadata: { title } }),
  });
  return chatRow(row);
}

/** Чат удаляется вместе с файлами: своим роутом, а не `DELETE /threads`. */
export const deleteChat = (threadId: string) =>
  json<{ deleted: string; files_removed: boolean }>(`/api/chats/${encodeURIComponent(threadId)}`, {
    method: "DELETE",
  });

const chatFilesPath = (threadId: string) => `/api/chats/${encodeURIComponent(threadId)}/files`;

export const loadChatFiles = (threadId: string) => json<ChatFiles>(chatFilesPath(threadId));

/**
 * Загрузить файл в чат. Тело запроса — сам файл, имя — в адресе: одному
 * файлу на запрос multipart ничего не добавляет.
 */
export async function uploadChatFile(threadId: string, file: File): Promise<ChatFiles & { file: ChatFile }> {
  const response = await authorizedFetch(
    `${API_URL}${chatFilesPath(threadId)}?name=${encodeURIComponent(file.name)}`,
    { method: "PUT", body: file, headers: { "content-type": "application/octet-stream" } },
  );
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error((body as { error?: string })?.error ?? `${file.name}: HTTP ${response.status}`);
  }
  return body as ChatFiles & { file: ChatFile };
}

export const readChatFile = (threadId: string, name: string) =>
  json<{ name: string; text: string }>(`${chatFilesPath(threadId)}/content?name=${encodeURIComponent(name)}`);

export const deleteChatFile = (threadId: string, name: string) =>
  json<ChatFiles>(`${chatFilesPath(threadId)}?name=${encodeURIComponent(name)}`, { method: "DELETE" });

export const attachToChat = (
  threadId: string,
  source: { source: "examples" | "published"; name: string; example?: string },
) =>
  json<ChatFiles & { file: ChatFile }>(`/api/chats/${encodeURIComponent(threadId)}/attach`, {
    method: "POST",
    body: JSON.stringify(source),
  });

export const loadLibrary = () => json<Library>("/api/library");
