/**
 * Журнал интерфейса: ошибки, которые видел браузер.
 *
 * Ошибка в интерфейсе жила ровно столько, сколько висела красная плашка:
 * следующий прогон, смена сценария или перезагрузка её стирали, и
 * пересказать разработчику, что случилось, оператор мог только по памяти.
 * Здесь каждая такая ошибка остаётся записью со временем и источником, а
 * страница «Журнал» собирает из них отчёт.
 *
 * Хранится в sessionStorage, а не только в памяти: самый частый повод
 * открыть журнал — после «Перезагрузить» на экране упавшего интерфейса, и
 * запись о падении должна пережить эту перезагрузку. Дальше вкладки журнал
 * не живёт — чужой браузер и завтрашний день его не видят.
 *
 * Модуль грузит и браузер, и `node --test` (через `auth.ts`), поэтому всё,
 * что касается `window` и хранилища, проверяется на месте, а не при импорте.
 */

export type ClientLevel = "error" | "warning";

export type ClientEntry = {
  id: number;
  /** Первое появление, ISO. */
  time: string;
  /** Последний повтор, ISO: совпадает с `time`, пока повторов не было. */
  lastTime: string;
  level: ClientLevel;
  /** Откуда запись: прогон, запрос, действие, интерфейс. */
  source: string;
  message: string;
  /** Стек, тело ответа, стек компонентов — то, что не помещается в строку. */
  detail?: string;
  repeats: number;
};

const KEY = "orbita.clientLog";
const LIMIT = 200;
const MESSAGE_LIMIT = 2000;
const DETAIL_LIMIT = 8000;

let entries: readonly ClientEntry[] = restore();
let nextId = entries.reduce((max, entry) => Math.max(max, entry.id), 0) + 1;
const listeners = new Set<() => void>();
let captured = false;

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

function restore(): readonly ClientEntry[] {
  try {
    const raw = storage()?.getItem(KEY);
    const value = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(value) ? (value as ClientEntry[]).slice(-LIMIT) : [];
  } catch {
    return [];
  }
}

function persist() {
  try {
    storage()?.setItem(KEY, JSON.stringify(entries));
  } catch {
    // Переполненное или запрещённое хранилище не должно ронять интерфейс:
    // журнал останется в памяти до перезагрузки.
  }
}

function clip(text: string, limit: number): string {
  return text.length <= limit ? text : `${text.slice(0, limit)}… (обрезано ${text.length - limit} симв.)`;
}

function publish(next: readonly ClientEntry[]) {
  entries = next;
  persist();
  for (const listener of listeners) listener();
}

/**
 * Записать ошибку.
 *
 * Тот же текст подряд не становится новой строкой, а увеличивает счётчик:
 * сервер, недоступный полминуты, иначе вытеснил бы из журнала всё остальное.
 */
export function reportClient(level: ClientLevel, source: string, message: string, detail?: string) {
  const text = clip(message.trim() || "(без текста)", MESSAGE_LIMIT);
  const now = new Date().toISOString();
  const last = entries.at(-1);
  if (last && last.level === level && last.source === source && last.message === text) {
    publish([...entries.slice(0, -1), { ...last, lastTime: now, repeats: last.repeats + 1 }]);
    return;
  }
  const entry: ClientEntry = {
    id: nextId++,
    time: now,
    lastTime: now,
    level,
    source,
    message: text,
    repeats: 1,
    ...(detail ? { detail: clip(detail, DETAIL_LIMIT) } : {}),
  };
  publish([...entries, entry].slice(-LIMIT));
}

/** Текст и стек из того, что бросили: бросают не только `Error`. */
export function describeError(error: unknown): { message: string; detail?: string } {
  if (error instanceof Error) {
    return { message: `${error.name}: ${error.message}`, detail: error.stack };
  }
  if (typeof error === "string") return { message: error };
  try {
    return { message: JSON.stringify(error) ?? String(error) };
  } catch {
    return { message: String(error) };
  }
}

/** Снимок для `useSyncExternalStore`: ссылка меняется только вместе с журналом. */
export const clientEntries = (): readonly ClientEntry[] => entries;

export function subscribeClient(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function clearClient() {
  publish([]);
}

/**
 * Ловить то, что не поймал никто: исключения вне React и отвергнутые
 * обещания без обработчика. Их раньше видела только консоль разработчика —
 * то есть у оператора их не видел никто.
 */
export function installClientCapture() {
  if (captured || typeof window === "undefined") return;
  captured = true;
  window.addEventListener("error", (event) => {
    const { message, detail } = describeError(event.error ?? event.message);
    const where = event.filename ? `\n${event.filename}:${event.lineno}:${event.colno}` : "";
    reportClient("error", "интерфейс", message, (detail ?? "") + where || undefined);
  });
  window.addEventListener("unhandledrejection", (event) => {
    const { message, detail } = describeError(event.reason);
    reportClient("error", "интерфейс", message, detail);
  });
}
