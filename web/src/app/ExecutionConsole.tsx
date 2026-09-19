/**
 * Консоль выполнения: нижняя панель по образцу среды разработки.
 *
 * До первого прогона её нет вовсе. Раньше журнал и лента сообщений занимали
 * нижнюю треть экрана всегда — в том числе на пустом треде, где показывать
 * было нечего, и единственным содержанием были слова «событий пока нет».
 *
 * Открывается она сама, когда прогон начался, и закрывается рукой. Высоту
 * тянут за верхнюю кромку; выбранная высота переживает перезагрузку, потому
 * что читают консоль по-разному: кому-то хватает трёх строк, кому-то нужен
 * весь список событий разом.
 *
 * Две вкладки: поток прогона (что сказали модель и инструменты) и события
 * (что сделал движок). Разные вопросы — разные списки.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent } from "react";

import { surfaceItems } from "../engine/surfaces/SurfaceRenderer";
import type { SafeWidgetContext, UiManifest, WidgetAction } from "../engine/manifest/types";
import { EVENT_LABELS } from "../engine/runtime/labels";
import type { RuntimeSnapshot } from "../engine/runtime/types";
import { filterEvents, Timeline } from "../engine/timeline/Timeline";
import { ChevronDown, Download, Search, ScrollText } from "../ui/icons";

export type ConsoleTab = "stream" | "events";

const HEIGHT_KEY = "orbita.console.height";
const MIN_HEIGHT = 160;

export function ExecutionConsole({
  manifest,
  runtime,
  context,
  inputs,
  onInput,
  onAction,
  tab,
  onTab,
  onClose,
  onSelectNode,
}: {
  manifest: UiManifest;
  runtime: RuntimeSnapshot;
  context: SafeWidgetContext;
  inputs: Record<string, unknown>;
  onInput: (id: string, value: unknown) => void;
  onAction: (action: WidgetAction) => void;
  tab: ConsoleTab;
  onTab: (tab: ConsoleTab) => void;
  onClose: () => void;
  onSelectNode: (nodeId: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [type, setType] = useState("all");
  const [height, setHeight] = useState(() => Number(localStorage.getItem(HEIGHT_KEY)) || 0);

  useEffect(() => {
    if (height) localStorage.setItem(HEIGHT_KEY, String(height));
  }, [height]);

  const startResize = useCallback((event: PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const move = (moving: globalThis.PointerEvent) => {
      const limit = Math.max(MIN_HEIGHT, window.innerHeight * 0.6);
      setHeight(Math.round(Math.min(limit, Math.max(MIN_HEIGHT, window.innerHeight - moving.clientY))));
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  }, []);

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

  const stream = surfaceItems({ surface: "main", manifest, runtime, context, inputs, onInput, onAction })
    .filter((item) => item.kind === "state");

  return (
    <section
      className="console"
      aria-label="Консоль выполнения"
      style={height ? { height: `${height}px` } : undefined}
    >
      <div
        className="console-resizer"
        role="separator"
        aria-orientation="horizontal"
        aria-label="Высота консоли выполнения"
        title="Потяните, чтобы изменить высоту"
        onPointerDown={startResize}
      />

      <div className="console-head">
        <span className="console-title">
          <ScrollText size={15} aria-hidden="true" />
          Выполнение
        </span>

        <div className="tabs console-tabs" role="group" aria-label="Содержимое консоли">
          <button className="tab" aria-pressed={tab === "stream"} onClick={() => onTab("stream")}>
            Поток
          </button>
          <button className="tab" aria-pressed={tab === "events"} onClick={() => onTab("events")}>
            События
            <span className="console-count">{runtime.events.length}</span>
          </button>
        </div>

        <div className="console-tools">
          {tab === "events" ? (
            <>
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
              <button
                className="btn-ghost btn-icon btn-sm"
                aria-label="Экспорт событий в JSON"
                title="Экспорт событий в JSON"
                onClick={exportEvents}
                disabled={!events.length}
              >
                <Download size={15} aria-hidden="true" />
              </button>
            </>
          ) : null}
          <button
            className="btn-ghost btn-icon btn-sm"
            aria-label="Свернуть консоль выполнения"
            title="Свернуть консоль выполнения"
            onClick={onClose}
          >
            <ChevronDown size={16} aria-hidden="true" />
          </button>
        </div>
      </div>

      <div className="console-body">
        {tab === "stream" ? (
          stream.length ? (
            stream.map((item) => <div className="console-stream" key={item.key}>{item.node}</div>)
          ) : (
            <p className="console-empty">Поток прогона пуст: сценарий не объявил ленты сообщений.</p>
          )
        ) : (
          <>
            {/* Лента рисуется и пустой: открытая вкладка без списка читается
                как поломка, а не как «событий ещё нет». */}
            <Timeline events={events} onSelectNode={onSelectNode} />
            {events.length ? null : (
              <p className="console-empty">
                {runtime.events.length
                  ? "Ничего не найдено: измените запрос или выберите другой тип события."
                  : "Событий пока нет — они появятся с началом прогона."}
              </p>
            )}
          </>
        )}
      </div>
    </section>
  );
}
