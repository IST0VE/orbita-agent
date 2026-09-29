/**
 * События прогона во вкладке «Прогон»: что сделал движок и когда.
 *
 * Раньше лента стояла второй вкладкой нижней консоли «Выполнение», рядом с
 * разговором. Разговор переехал в колонку чата, а события остались
 * подробностью прогона — тем, что читают, разбираясь, почему узел упал, а не
 * следя за ходом. Поэтому они здесь, под показателями, и свёрнуты, пока их
 * не попросили: колокольчик в шапке открывает их сам.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { EVENT_LABELS } from "../../engine/runtime/labels";
import type { RuntimeSnapshot } from "../../engine/runtime/types";
import { filterEvents, Timeline } from "../../engine/timeline/Timeline";
import { Download, Search } from "../../ui/icons";

export function RunEvents({
  runtime,
  onSelectNode,
  focus,
}: {
  runtime: RuntimeSnapshot;
  onSelectNode: (nodeId: string) => void;
  /** Растёт, когда события попросили показать: раскрыть и подвести взгляд. */
  focus: number;
}) {
  const [query, setQuery] = useState("");
  const [type, setType] = useState("all");
  const box = useRef<HTMLDetailsElement>(null);

  useEffect(() => {
    const node = box.current;
    if (!node || !focus) return;
    node.open = true;
    node.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [focus]);

  const events = useMemo(
    () => filterEvents(runtime.events, query, type),
    [runtime.events, query, type],
  );
  const types = useMemo(
    () => [...new Set(runtime.events.map((event) => event.type))],
    [runtime.events],
  );

  const exported = useRef(events);
  exported.current = events;
  const exportEvents = () => {
    const blob = new Blob([JSON.stringify(exported.current, null, 2)], { type: "application/json" });
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
    <details className="inspector-section run-events" ref={box}>
      <summary className="eyebrow">
        События
        <span className="run-events-count">{runtime.events.length}</span>
      </summary>
      <div className="run-events-tools">
        <label className="canvas-search">
          <Search size={15} aria-hidden="true" />
          <input
            aria-label="Поиск события"
            value={query}
            placeholder="Найти событие…"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <select aria-label="Тип события" value={type} onChange={(event) => setType(event.target.value)}>
          <option value="all">Все события</option>
          {types.map((item) => (
            <option key={item} value={item}>{EVENT_LABELS[item] ?? item}</option>
          ))}
        </select>
        <button
          className="btn-ghost btn-icon btn-sm"
          aria-label="Экспорт событий в JSON"
          title="Экспорт событий в JSON"
          onClick={exportEvents}
          disabled={!events.length}
        >
          <Download size={15} aria-hidden="true" />
        </button>
      </div>
      {/* Лента рисуется и пустой: открытый раздел без списка читается как
          поломка, а не как «событий ещё нет». */}
      <Timeline events={events} onSelectNode={onSelectNode} />
      {events.length ? null : (
        <p className="inspector-empty">
          {runtime.events.length
            ? "Ничего не найдено: измените запрос или выберите другой тип события."
            : "Событий пока нет — они появятся с началом прогона."}
        </p>
      )}
    </details>
  );
}
