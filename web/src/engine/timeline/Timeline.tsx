/**
 * Лента событий прогона.
 *
 * Не терминал и не отладочный вывод: строка ленты отвечает на вопрос «что
 * произошло и когда», а не «какой JSON приехал». Поэтому событие названо
 * словами, время стоит отдельной колонкой, а исходный тип события уходит в
 * `title` — он нужен, когда ленту читают рядом с логами сервера.
 *
 * Поиск, фильтр и выгрузка живут не здесь, а в шапке консоли выполнения:
 * это её панель инструментов, и в ней же стоят вкладки и свёртка. Ленте
 * остаётся показать то, что ей передали, — отсюда и `filterEvents` рядом:
 * консоль отбирает события, а рисует их этот файл.
 *
 * Показываются последние пятьсот событий: длинный прогон набирает их тысячами,
 * и отрисовка всей ленты стоит больше, чем она даёт.
 */

import { memo } from "react";

import { runErrorMessage } from "../api/langgraphAdapter";
import { EVENT_LABELS, EVENT_TONES } from "../runtime/labels";
import type { RuntimeEvent } from "../runtime/types";

const timeFormat = new Intl.DateTimeFormat("ru-RU", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

const LIMIT = 500;

/** Отбор ленты: подстрока по названию и типу события плюс выбранный тип. */
export function filterEvents(
  events: RuntimeEvent[],
  query: string,
  type: string,
): RuntimeEvent[] {
  const needle = query.trim().toLowerCase();
  return events.filter((event) => {
    const data = event.data && typeof event.data === "object" ? event.data as Record<string, unknown> : {};
    const nodeId = String(data.nodeId ?? "");
    const label = EVENT_LABELS[event.type] ?? event.type;
    return (type === "all" || event.type === type)
      && (!needle || `${event.type} ${label} ${nodeId}`.toLowerCase().includes(needle));
  });
}

export const Timeline = memo(function Timeline({
  events,
  onSelectNode,
}: {
  events: RuntimeEvent[];
  onSelectNode?: (nodeId: string) => void;
}) {
  return (
    <ol className="engine-timeline" aria-label="Журнал выполнения">
      {events.slice(-LIMIT).map((event) => {
        const data = event.data && typeof event.data === "object"
          ? event.data as Record<string, unknown>
          : {};
        const nodeId = typeof data.nodeId === "string" ? data.nodeId : "";
        const timestamp = new Date(event.timestamp);
        return (
          <li key={event.eventId}>
            <span className={`dot dot-${EVENT_TONES[event.type] ?? "idle"}`} aria-hidden="true" />
            <time dateTime={event.timestamp}>
              {Number.isNaN(timestamp.getTime()) ? "—" : timeFormat.format(timestamp)}
            </time>
            <b className="timeline-kind" title={event.type}>
              {EVENT_LABELS[event.type] ?? event.type}
            </b>
            <span className="timeline-detail">
              {nodeId ? (
                <button
                  title={`Показать узел ${nodeId} на схеме`}
                  onClick={() => onSelectNode?.(nodeId)}
                >
                  {nodeId}
                </button>
              ) : null}
              <span className="timeline-seq">#{event.sequence}</span>
            </span>
            {data.error ? <div className="error">{runErrorMessage(data.error)}</div> : null}
          </li>
        );
      })}
    </ol>
  );
}, (previous, next) =>
  previous.events === next.events && previous.onSelectNode === next.onSelectNode);
