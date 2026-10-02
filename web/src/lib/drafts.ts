/**
 * Черновики поля задачи: набранное переживает перезагрузку, уход на вход и смену чата.
 *
 * Раньше черновик жил в состоянии поля и пропадал вместе со страницей. Самый
 * обидный случай — истёкшая сессия: человек писал запрос, нажимал «Запустить»,
 * страница уходила на вход в Keycloak, а возвращалась с пустым полем. Теперь
 * текст лежит в `localStorage`, свой у каждого пользователя и чата, и
 * стирается, только когда сервер принял ход.
 *
 * `localStorage`, а не `sessionStorage`: вход мог открыться в той же вкладке,
 * а мог — после того, как вкладку закрыли и открыли ссылку заново. Старше
 * недели черновики не хранятся: забытый текст не должен всплывать через месяц.
 */

const PREFIX = "orbita.draft.";
const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;
/** Длиннее не храним: поле задачи — не место для документа, а квота у хранилища одна. */
const MAX_CHARS = 100_000;

type Stored = { text: string; at: number };

/** Ключ черновика: пользователь, сценарий и чат (или новый чат сценария). */
export function draftKey(owner: string | undefined, graphId: string, threadId: string | null): string {
  return `${PREFIX}${owner ?? "local"}.${graphId}.${threadId ?? "new"}`;
}

export function loadDraft(key: string): string {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return "";
    const stored = JSON.parse(raw) as Stored;
    if (typeof stored?.text !== "string" || Date.now() - Number(stored.at) > MAX_AGE_MS) {
      localStorage.removeItem(key);
      return "";
    }
    return stored.text;
  } catch {
    return "";
  }
}

export function saveDraft(key: string, text: string): void {
  try {
    if (!text.trim()) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify({ text: text.slice(0, MAX_CHARS), at: Date.now() }));
  } catch {
    // Приватный режим или полное хранилище: черновик — удобство, а не повод
    // ломать поле ввода.
  }
}

/** Убрать черновики старше недели — раз при загрузке страницы. */
export function pruneDrafts(): void {
  try {
    for (let index = localStorage.length - 1; index >= 0; index -= 1) {
      const key = localStorage.key(index);
      if (key?.startsWith(PREFIX)) loadDraft(key);
    }
  } catch {
    // См. saveDraft.
  }
}

/**
 * Текст цитаты для поля задачи: выделенное в ответе — строками Markdown `> `.
 *
 * Модель видит, о какой части ответа вопрос, а человеку не нужно копировать
 * абзац и вставлять его руками.
 */
export function quoteBlock(text: string): string {
  const lines = text.replace(/\r\n?/g, "\n").trim().split("\n");
  return lines.map((line) => (line.trim() ? `> ${line}` : ">")).join("\n");
}
