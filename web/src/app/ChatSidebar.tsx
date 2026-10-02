/**
 * Левая колонка: все чаты оператора.
 *
 * Раньше здесь стояли общие папки задач, одни на всех вошедших: материалы
 * одного человека лежали на виду у остальных, а «история» сводилась к одному
 * треду на сценарий, который помнил браузер. Теперь чат — это тред LangGraph:
 * его заводит пользователь, в нём живут запросы, результаты и загруженные
 * файлы, и чужих чатов сервер не отдаёт вовсе (`auth.py`).
 *
 * Список один на все сценарии. Когда у каждого сценария был свой, чат искали
 * в два шага — сначала вспоминали, каким сценарием его вели, потом листали
 * его список, — а вчерашний разбор прятался, стоило переключить сценарий.
 * Сценарий теперь подписан у самого чата, и открыть чат значит открыть и
 * его сценарий.
 *
 * Файлы и ход прогона показывает правая колонка: они относятся к открытому
 * чату, а не к списку.
 *
 * Своих кнопок «Скрыть» и «Новый чат» у колонки нет: обе стоят в строке
 * контекста, и копии здесь только дублировали их.
 */

import { useEffect, useMemo, useState, type PointerEvent } from "react";

import type { Chat } from "../api";
import { Search, Trash2 } from "../ui/icons";
import { StatusDot } from "../ui";
import { scenarioIcon } from "./scenarios";

/** С какого числа чатов нужен поиск: короткий список читается глазами. */
const FILTER_FROM = 8;

/** Название, которое видит человек: своё или по дате. */
export function chatTitle(chat: Pick<Chat, "title" | "created_at">): string {
  if (chat.title.trim()) return chat.title.trim();
  const created = chat.created_at ? new Date(chat.created_at) : null;
  return created && !Number.isNaN(created.getTime())
    ? `Чат от ${created.toLocaleDateString("ru-RU", { day: "numeric", month: "long" })}`
    : "Новый чат";
}

/** Когда чат трогали: время сегодня, дата — раньше. */
function when(value?: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const today = new Date();
  return date.toDateString() === today.toDateString()
    ? date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
}

export function ChatSidebar({
  chats,
  loading,
  error,
  activeId,
  draftScenario,
  draftGraph,
  scenarioName,
  locked,
  onOpen,
  onDelete,
  onRetry,
  startResize,
  resetWidth,
  drawer,
}: {
  chats: Chat[];
  loading: boolean;
  error: string;
  activeId: string | null;
  /** Сценарий черновика — того, что откроется с первым запросом. */
  draftScenario: string;
  draftGraph: string;
  /** Название сценария по графу: подпись под названием чата. */
  scenarioName: (graphId: string) => string;
  /** Идёт прогон: переключать чат под ним нельзя. */
  locked: boolean;
  onOpen: (chat: Chat) => void;
  onDelete: (threadId: string) => Promise<void>;
  onRetry: () => void;
  startResize: (event: PointerEvent<HTMLDivElement>) => void;
  resetWidth: () => void;
  drawer: boolean;
}) {
  const [query, setQuery] = useState("");
  /** Чат, у которого уже нажали корзину: второе нажатие удаляет. */
  const [armed, setArmed] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState("");

  useEffect(() => {
    if (!armed) return;
    const timer = window.setTimeout(() => setArmed(null), 4000);
    return () => window.clearTimeout(timer);
  }, [armed]);

  const needle = query.trim().toLowerCase();
  // Ищут и по названию, и по сценарию: «где был тот разбор схем» — это
  // вопрос о сценарии, а не о словах в названии.
  const shown = useMemo(
    () => (needle
      ? chats.filter((chat) => `${chatTitle(chat)} ${scenarioName(chat.graph_id)}`.toLowerCase().includes(needle))
      : chats),
    [chats, needle, scenarioName],
  );
  // Открытый чат ещё не заведён на сервере (нет ни файла, ни прогона) —
  // показываем его строкой сверху, чтобы было видно, где ты.
  const draft = activeId === null;
  const DraftIcon = scenarioIcon(draftGraph);

  const remove = (threadId: string) => {
    if (armed !== threadId) {
      setArmed(threadId);
      return;
    }
    setArmed(null);
    setDeleting(threadId);
    setDeleteError("");
    onDelete(threadId)
      .catch((reason: Error) => setDeleteError(reason.message))
      .finally(() => setDeleting(null));
  };

  return (
    <aside className="sidebar chat-sidebar" aria-label="Чаты">
      <div className="sidebar-head">
        <span className="sidebar-title">Чаты</span>
      </div>

      {drawer ? null : (
        <div
          className="col-resizer"
          role="separator"
          aria-orientation="vertical"
          aria-label="Ширина левой колонки"
          title="Потяните, чтобы изменить ширину. Двойной щелчок — сбросить."
          onPointerDown={startResize}
          onDoubleClick={resetWidth}
        />
      )}

      <div className="sidebar-scroll chat-sidebar-scroll">
        {chats.length >= FILTER_FROM ? (
          <label className="canvas-search">
            <Search size={15} aria-hidden="true" />
            <input
              aria-label="Найти чат"
              value={query}
              placeholder="Найти чат или сценарий…"
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
        ) : null}

        <nav className="chat-list" aria-label="Все чаты">
          {draft ? (
            <div className="chat-item active draft" aria-current="page">
              <span className="chat-item-open">
                <DraftIcon size={15} aria-hidden="true" />
                <span className="chat-item-text">
                  <span className="chat-item-title">Новый чат</span>
                  <span className="chat-item-scenario">{draftScenario}</span>
                </span>
                <span className="hint">черновик</span>
              </span>
            </div>
          ) : null}
          {shown.map((chat) => {
            const active = chat.thread_id === activeId;
            const running = chat.status === "busy";
            const waiting = chat.status === "interrupted";
            const scenario = scenarioName(chat.graph_id);
            const Icon = scenarioIcon(chat.graph_id);
            return (
              <div key={chat.thread_id} className={`chat-item${active ? " active" : ""}`} data-graph={chat.graph_id}>
                <button
                  type="button"
                  className="chat-item-open"
                  aria-current={active ? "page" : undefined}
                  disabled={locked && !active}
                  title={scenario ? `${chatTitle(chat)} · ${scenario}` : chatTitle(chat)}
                  onClick={() => onOpen(chat)}
                >
                  {running || waiting ? (
                    <StatusDot tone={running ? "run" : "warn"} />
                  ) : (
                    <Icon size={15} aria-hidden="true" />
                  )}
                  <span className="chat-item-text">
                    <span className="chat-item-title">{chatTitle(chat)}</span>
                    {scenario ? <span className="chat-item-scenario">{scenario}</span> : null}
                  </span>
                  <span className="hint">{when(chat.updated_at ?? chat.created_at)}</span>
                </button>
                <button
                  type="button"
                  className={`chat-item-remove${armed === chat.thread_id ? " armed" : ""}`}
                  disabled={(locked && active) || deleting === chat.thread_id}
                  aria-label={armed === chat.thread_id ? `подтвердить удаление чата ${chatTitle(chat)}` : `удалить чат ${chatTitle(chat)}`}
                  title={armed === chat.thread_id ? "Нажмите ещё раз: чат удалится вместе с файлами" : "Удалить чат вместе с файлами"}
                  onClick={() => remove(chat.thread_id)}
                >
                  {armed === chat.thread_id ? "Удалить?" : <Trash2 size={14} aria-hidden="true" />}
                </button>
              </div>
            );
          })}
        </nav>

        {loading && !chats.length ? (
          <div className="engine-widget">
            <span className="skeleton" style={{ width: "70%" }} />
            <span className="skeleton" style={{ width: "55%" }} />
          </div>
        ) : null}
        {!loading && !error && !chats.length ? (
          <p className="hint chat-empty">
            Здесь появятся ваши чаты всех сценариев. Напишите задачу внизу или загрузите файлы — чат заведётся сам.
          </p>
        ) : null}
        {needle && chats.length && !shown.length ? <p className="hint">Ничего не нашлось.</p> : null}
        {error ? (
          <div className="error" role="alert">
            <span>{error}</span>
            <button className="btn-ghost btn-sm" onClick={onRetry}>Повторить</button>
          </div>
        ) : null}
        {deleteError ? <span className="error" role="alert">{deleteError}</span> : null}
      </div>
    </aside>
  );
}
