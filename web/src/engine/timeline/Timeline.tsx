/**
 * Журнал выполнения.
 *
 * Не терминал и не отладочный вывод: строка журнала отвечает на вопрос «что
 * произошло и когда», а не «какой JSON приехал». Поэтому событие названо
 * словами, время стоит отдельной колонкой, а исходный тип события уходит в
 * `title` — он нужен, когда журнал читают рядом с логами сервера.
 *
 * Показываются последние пятьсот событий: длинный прогон набирает их тысячами,
 * и отрисовка всей ленты стоит больше, чем она даёт.
 */

import { memo, useMemo, useState } from "react";

import { runErrorMessage } from "../api/langgraphAdapter";
import { EVENT_LABELS, EVENT_TONES } from "../runtime/labels";
import type { RuntimeSnapshot } from "../runtime/types";
import { Download, Search, X } from "../../ui/icons";
import { EmptyState } from "../../ui";
import { ScrollText } from "../../ui/icons";

const timeFormat = new Intl.DateTimeFormat("ru-RU", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

const LIMIT = 500;

export const Timeline = memo(function Timeline({
  runtime,
  onSelectNode,
  onClose,
}: {
  runtime: RuntimeSnapshot;
  onSelectNode?: (nodeId: string) => void;
  onClose?: () => void;
}) {
  const [query, setQuery] = useState("");
  const [type, setType] = useState("all");

  const events = useMemo(() => runtime.events.filter((event) => {
    const data = event.data && typeof event.data === "object" ? event.data as Record<string, unknown> : {};
    const nodeId = String(data.nodeId ?? "");
    const label = EVENT_LABELS[event.type] ?? event.type;
    return (type === "all" || event.type === type)
      && (!query || `${event.type} ${label} ${nodeId}`.toLowerCase().includes(query.toLowerCase()));
  }), [runtime.events, query, type]);

  const types = useMemo(
    () => [...new Set(runtime.events.map((event) => event.type))],
    [runtime.events],
  );

  const exportEvents = () => {
    const blob = new Blob([JSON.stringify(events, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `orbita-${runtime.runId ?? "timeline"}.json`;
    link.click();
    // Отзыв ссылки на следующем тике: синхронный revoke успевает отменить
    // скачивание, которое click() только что начал.
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  };

  return (
    <section className="engine-timeline" aria-label="Журнал выполнения">
      <div className="timeline-tools">
        <h2>Журнал выполнения</h2>
        <label className="canvas-search">
          <Search size={15} aria-hidden="true" />
          <input
            aria-label="Поиск события"
            value={query}
            placeholder="Найти событие…"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <select
          aria-label="Тип события"
          value={type}
          onChange={(event) => setType(event.target.value)}
        >
          <option value="all">Все события</option>
          {types.map((item) => (
            <option key={item} value={item}>{EVENT_LABELS[item] ?? item}</option>
          ))}
        </select>
        <button className="btn-ghost btn-sm" onClick={exportEvents} disabled={!events.length}>
          <Download size={15} aria-hidden="true" />
          Экспорт JSON
        </button>
        {onClose ? (
          <button
            className="btn-ghost btn-icon btn-sm"
            aria-label="Скрыть журнал"
            title="Скрыть журнал"
            onClick={onClose}
          >
            <X size={16} aria-hidden="true" />
          </button>
        ) : null}
      </div>

      {events.length ? (
        <ol>
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
      ) : (
        <EmptyState
          icon={ScrollText}
          title={runtime.events.length ? "Ничего не найдено" : "Событий пока нет"}
          hint={
            runtime.events.length
              ? "Измените запрос или выберите другой тип события."
              : "Журнал заполнится, как только начнётся прогон."
          }
        />
      )}
    </section>
  );
}, (previous, next) =>
  previous.runtime.events === next.runtime.events
  && previous.runtime.runId === next.runtime.runId
  && previous.onSelectNode === next.onSelectNode
  && previous.onClose === next.onClose);
