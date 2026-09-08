import { memo, useMemo, useState } from "react";
import type { RuntimeSnapshot } from "../runtime/types";
import { runErrorMessage } from "../api/langgraphAdapter";

const timeFormat = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

export const Timeline = memo(function Timeline({ runtime, onSelectNode }: { runtime: RuntimeSnapshot; onSelectNode?: (nodeId: string) => void }) {
  const [query, setQuery] = useState("");
  const [type, setType] = useState("all");
  const events = useMemo(() => runtime.events.filter((event) => {
    const data = event.data && typeof event.data === "object" ? event.data as Record<string, unknown> : {};
    const nodeId = String(data.nodeId ?? "");
    return (type === "all" || event.type === type) && (!query || `${event.type} ${nodeId}`.toLowerCase().includes(query.toLowerCase()));
  }), [runtime.events, query, type]);
  const types = useMemo(() => [...new Set(runtime.events.map((event) => event.type))], [runtime.events]);
  const exportEvents = () => {
    const blob = new Blob([JSON.stringify(events, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url; link.download = `orbita-${runtime.runId ?? "timeline"}.json`; link.click();
    // Отзыв ссылки на следующем тике: синхронный revoke успевает отменить
    // скачивание, которое click() только что начал.
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  };
  return <section className="engine-timeline" aria-label="Timeline выполнения">
    <div className="timeline-tools"><input aria-label="Поиск события" value={query} placeholder="найти событие" onChange={(event) => setQuery(event.target.value)} /><select aria-label="Тип события" value={type} onChange={(event) => setType(event.target.value)}><option value="all">все события</option>{types.map((item) => <option key={item}>{item}</option>)}</select><button onClick={exportEvents}>[экспорт JSON]</button></div>
    <ol>{events.slice(-500).map((event) => {
      const data = event.data && typeof event.data === "object" ? event.data as Record<string, unknown> : {};
      const nodeId = typeof data.nodeId === "string" ? data.nodeId : "";
      const timestamp = new Date(event.timestamp);
      return <li key={event.eventId}><time>{Number.isNaN(timestamp.getTime()) ? "—" : timeFormat.format(timestamp)}</time> <b>{event.type}</b>{nodeId ? <button onClick={() => onSelectNode?.(nodeId)}>{nodeId}</button> : null}<span className="hint">#{event.sequence}</span>{data.error ? <div className="error">{runErrorMessage(data.error)}</div> : null}</li>;
    })}</ol>
  </section>;
}, (previous, next) => previous.runtime.events === next.runtime.events && previous.runtime.runId === next.runtime.runId && previous.onSelectNode === next.onSelectNode);
