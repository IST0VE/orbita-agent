/**
 * Управление схемой в шапке рабочей области.
 *
 * Постоянно нужны три вещи: найти узел, вернуть масштаб и переключиться между
 * схемой и списком. Всё остальное — мини-карта, авторасстановка, обзор
 * целиком — уехало в меню: это действия, к которым обращаются раз в сеанс, и
 * держать их на виду значит требовать чтения всей панели ради поиска одного.
 */

import { Ellipsis, List, Map, Maximize2, Minus, Network, Plus, Search } from "../../ui/icons";
import { Menu } from "../../ui/Menu";
import { ZOOM_STEP } from "./ZoomPane";
import type { GraphView } from "./useGraphView";

const REPRESENTATIONS = [
  { id: "diagram", label: "Схема", icon: Network, hint: "Узлы и связи на полотне" },
  { id: "list", label: "Список", icon: List, hint: "Узлы таблицей: тип, состояние, запуски" },
] as const;

export function GraphControls({ view }: { view: GraphView }) {
  return (
    <>
      <label className="canvas-search">
        <Search size={15} aria-hidden="true" />
        <input
          aria-label="Поиск узла"
          value={view.query}
          placeholder="Найти узел…"
          onChange={(event) => view.setQuery(event.target.value)}
        />
      </label>

      <div className="segmented" role="group" aria-label="Представление графа">
        {REPRESENTATIONS.map((item) => (
          <button
            key={item.id}
            type="button"
            className="segmented-option"
            data-representation={item.id}
            aria-pressed={view.representation === item.id}
            aria-label={item.label}
            title={`${item.label}: ${item.hint}`}
            onClick={() => view.setRepresentation(item.id)}
          >
            <item.icon size={15} aria-hidden="true" />
          </button>
        ))}
      </div>

      <span className="canvas-zoom">
        <button
          className="btn-ghost btn-icon btn-sm"
          aria-label="Отдалить"
          title="Отдалить (клавиша «−», Ctrl с колесом)"
          onClick={() => view.pane.current?.zoomBy(-ZOOM_STEP)}
        >
          <Minus size={15} aria-hidden="true" />
        </button>
        <output aria-label="Масштаб">{Math.round(view.zoom * 100)}%</output>
        <button
          className="btn-ghost btn-icon btn-sm"
          aria-label="Приблизить"
          title="Приблизить (клавиша «+», Ctrl с колесом)"
          onClick={() => view.pane.current?.zoomBy(ZOOM_STEP)}
        >
          <Plus size={15} aria-hidden="true" />
        </button>
      </span>

      <button
        className="btn-ghost btn-icon"
        aria-label="Вписать граф"
        title={view.representation === "list" ? "Подобрать масштаб по ширине списка" : "Вписать граф целиком"}
        onClick={() => view.pane.current?.fit()}
      >
        <Maximize2 size={16} aria-hidden="true" />
      </button>

      <Menu
        label="Ещё действия со схемой"
        trigger={<Ellipsis size={16} aria-hidden="true" />}
        items={[
          {
            id: "minimap",
            label: "Мини-карта",
            icon: Map,
            checked: view.minimap,
            disabled: view.representation === "list",
            hint: "Навигатор по графу в углу полотна",
            onSelect: view.toggleMinimap,
          },
          {
            id: "arrange",
            label: "Авторасстановка",
            icon: Network,
            disabled: view.representation === "list",
            hint: "Сбросить ручное расположение узлов",
            onSelect: view.arrange,
          },
          {
            id: "reset-zoom",
            label: "Масштаб 100%",
            icon: Maximize2,
            onSelect: () => view.pane.current?.reset(),
          },
        ]}
      />
    </>
  );
}
