/**
 * Шапка рабочей области: вид слева, управление видом справа.
 *
 * Одна строка, а не две. Раньше под навигацией продукта стояла ещё одна
 * панель с тремя вкладками, и обе выглядели одинаково важными — хотя первая
 * отвечала на вопрос «в каком я разделе», а вторая «на что я смотрю».
 *
 * Справа стоит то, что относится к открытому виду, и ничего больше: у схемы
 * это поиск, масштаб и представление, у документа — возврат к схеме.
 */

import { GraphControls } from "../engine/canvas/GraphControls";
import type { GraphView } from "../engine/canvas/useGraphView";
import { FileText, LayoutGrid, Network, X } from "../ui/icons";
import type { WorkspaceView } from "./sections";

const VIEWS: Array<{ id: WorkspaceView; label: string; icon: typeof Network; hint: string }> = [
  { id: "graph", label: "Схема", icon: Network, hint: "Конвейер и ход его выполнения" },
  { id: "result", label: "Результат", icon: LayoutGrid, hint: "Итог прогона и его подробности" },
  { id: "document", label: "Документ", icon: FileText, hint: "Открытый документ" },
];

export function WorkspaceHeader({
  view,
  onView,
  documentOpen,
  documentTitle,
  onCloseDocument,
  resultReady,
  graph,
}: {
  view: WorkspaceView;
  onView: (view: WorkspaceView) => void;
  documentOpen: boolean;
  documentTitle: string;
  onCloseDocument: () => void;
  /** В результате есть что показать: вкладка получает отметку. */
  resultReady: boolean;
  graph: GraphView;
}) {
  const views = VIEWS.filter((item) => item.id !== "document" || documentOpen);
  return (
    <div className="canvas-tools workspace-bar">
      <div className="tabs" role="group" aria-label="Вид рабочей области">
        {views.map((item) => (
          <button
            key={item.id}
            className="tab"
            data-view={item.id}
            aria-pressed={view === item.id}
            title={item.id === "document" ? documentTitle : item.hint}
            onClick={() => onView(item.id)}
          >
            <item.icon size={15} aria-hidden="true" />
            <span className="truncate">{item.label}</span>
            {item.id === "result" && resultReady ? (
              <span className="tab-mark" aria-hidden="true" />
            ) : null}
          </button>
        ))}
      </div>

      <div className="canvas-tools-right">
        {view === "graph" ? <GraphControls view={graph} /> : null}
        {view === "document" ? (
          <button className="btn-ghost btn-sm" onClick={onCloseDocument}>
            <X size={15} aria-hidden="true" />
            Закрыть документ
          </button>
        ) : null}
      </div>
    </div>
  );
}
