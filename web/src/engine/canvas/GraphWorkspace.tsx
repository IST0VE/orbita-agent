/**
 * Схема конвейера в главной рабочей области.
 *
 * Только полотно: панель инструментов уехала в шапку рабочей области, а
 * карточка выбранного узла — в инспектор. Схема занимает всё, что ей дали, и
 * не делит место ни с чем — ради неё этот экран и открывают.
 */
import { memo } from "react";

import type { GraphTopology } from "../../lib/graph";
import { OrbitWatermark } from "../../ui/icons";
import type { UiManifest } from "../manifest/types";
import type { RuntimeSnapshot } from "../runtime/types";
import { GraphDiagram } from "./GraphDiagram";
import { GraphList } from "./GraphList";
import type { GraphView } from "./useGraphView";
import { ZoomPane } from "./ZoomPane";

export const GraphWorkspace = memo(function GraphWorkspace({
  topology,
  manifest,
  runtime,
  selected,
  onSelect,
  view,
}: {
  topology: GraphTopology;
  manifest: UiManifest;
  runtime: RuntimeSnapshot;
  selected: string | null;
  onSelect: (id: string | null) => void;
  view: GraphView;
}) {
  return (
    <div className="graph-workspace">
      <OrbitWatermark />
      {/* Полотно остаётся смонтированным при переходе к списку: иначе ручные
          позиции узлов и камера теряются на каждом переключении. */}
      <div className="canvas-graph-host" hidden={view.representation === "list"}>
        <GraphDiagram
          ref={view.diagram}
          topology={topology}
          manifest={manifest}
          runtime={runtime}
          selected={selected}
          onSelect={onSelect}
          query={view.query}
          minimap={view.minimap}
          onZoom={view.onZoom}
        />
      </div>
      {view.representation === "list" ? (
        <ZoomPane ref={view.list} zoom={view.zoom} onZoom={view.onListZoom} label="Список узлов графа">
          <GraphList
            topology={topology}
            manifest={manifest}
            runtime={runtime}
            selected={selected}
            onSelect={onSelect}
            query={view.query}
          />
        </ZoomPane>
      ) : null}
    </div>
  );
});
