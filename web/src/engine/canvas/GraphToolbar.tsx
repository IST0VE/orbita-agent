/**
 * Панель над полотном: вид, поиск и масштаб.
 *
 * Три вкладки слева — это три способа смотреть на один и тот же граф, поэтому
 * они вкладки, а не кнопки действий. Поиск и масштаб справа: они не меняют
 * то, что показано, а меняют то, как в это смотрят.
 */

import type { RefObject } from "react";

import { LayoutGrid, List, Maximize2, Minus, Network, Plus, RotateCcw, Search } from "../../ui/icons";
import { ZOOM_STEP, type ZoomHandle } from "./ZoomPane";

export type ViewMode = "diagram" | "interactive" | "list";

const MODES: Array<{ id: ViewMode; label: string; icon: typeof LayoutGrid; hint: string }> = [
  { id: "diagram", label: "Схема", icon: LayoutGrid, hint: "Перетаскивание узлов и полотна" },
  { id: "interactive", label: "Мини-карта", icon: Network, hint: "Та же схема с навигацией по мини-карте" },
  { id: "list", label: "Список", icon: List, hint: "Узлы таблицей: тип, состояние, число запусков" },
];

export function GraphToolbar({
  mode,
  onMode,
  query,
  onQuery,
  zoom,
  pane,
  onArrange,
}: {
  mode: ViewMode;
  onMode: (mode: ViewMode) => void;
  query: string;
  onQuery: (value: string) => void;
  zoom: number;
  pane: RefObject<ZoomHandle | null>;
  onArrange: () => void;
}) {
  return (
    <div className="canvas-tools">
      <div className="tabs" role="group" aria-label="Вид графа">
        {MODES.map((item) => (
          <button
            key={item.id}
            className="tab"
            aria-pressed={mode === item.id}
            title={item.hint}
            onClick={() => onMode(item.id)}
          >
            <item.icon size={15} aria-hidden="true" />
            {item.label}
          </button>
        ))}
      </div>

      <div className="canvas-tools-right">
        <label className="canvas-search">
          <Search size={15} aria-hidden="true" />
          <input
            aria-label="Поиск узла"
            value={query}
            placeholder="Найти узел…"
            onChange={(event) => onQuery(event.target.value)}
          />
        </label>
        {mode !== "list" ? <button className="btn-ghost btn-sm" onClick={onArrange}
          title="Сбросить ручное расположение и автоматически расставить узлы" aria-label="Авторасстановка">
          <RotateCcw size={15} aria-hidden="true" />
        </button> : null}
        <button
          className="btn-ghost btn-sm"
          title={mode === "list" ? "Подобрать масштаб по ширине списка" : "Показать весь граф по ширине и высоте"}
          onClick={() => pane.current?.fit()}
        >
          <Maximize2 size={15} aria-hidden="true" />
          {mode === "list" ? "По ширине" : "Весь граф"}
        </button>
        <span className="canvas-zoom">
          <button
            aria-label="Отдалить"
            title="Отдалить (клавиша «−», Ctrl с колесом)"
            onClick={() => pane.current?.zoomBy(-ZOOM_STEP)}
          >
            <Minus size={15} aria-hidden="true" />
          </button>
          <output aria-label="Масштаб">{Math.round(zoom * 100)}%</output>
          <button
            aria-label="Приблизить"
            title="Приблизить (клавиша «+», Ctrl с колесом)"
            onClick={() => pane.current?.zoomBy(ZOOM_STEP)}
          >
            <Plus size={15} aria-hidden="true" />
          </button>
        </span>
      </div>
    </div>
  );
}
