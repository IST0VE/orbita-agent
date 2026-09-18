/** Shared presentation state; graph geometry lives independently of run updates. */
import { memo, useRef, useState } from "react";
import type { GraphTopology } from "../../lib/graph";
import { OrbitWatermark } from "../../ui/icons";
import type { UiManifest } from "../manifest/types";
import type { RuntimeSnapshot } from "../runtime/types";
import { GraphDiagram, type DiagramHandle } from "./GraphDiagram";
import { GraphList } from "./GraphList";
import { GraphToolbar, type ViewMode } from "./GraphToolbar";
import { NodeDetails } from "./NodeDetails";
import { ZoomPane, type ZoomHandle } from "./ZoomPane";

export const GraphCanvas = memo(function GraphCanvas({ topology, manifest, runtime, selected, onSelect }: {
  topology: GraphTopology; manifest: UiManifest; runtime: RuntimeSnapshot;
  selected: string | null; onSelect: (id: string | null) => void;
}) {
  const [mode, setMode] = useState<ViewMode>("diagram");
  const [query, setQuery] = useState("");
  const [zoom, setZoom] = useState(1);
  const [listZoom, setListZoom] = useState(1);
  const pane = useRef<DiagramHandle>(null);
  const listPane = useRef<ZoomHandle>(null);
  return <div className="engine-canvas">
    <GraphToolbar mode={mode} onMode={setMode} query={query} onQuery={setQuery}
      zoom={mode === "list" ? listZoom : zoom} pane={mode === "list" ? listPane : pane}
      onArrange={() => pane.current?.arrange()} />
    <OrbitWatermark />
    {/* Keep the viewport mounted across tabs: manual positions and camera survive. */}
    <div className="canvas-graph-host" hidden={mode === "list"}>
      <GraphDiagram ref={pane} topology={topology} manifest={manifest} runtime={runtime}
        selected={selected} onSelect={onSelect} query={query} minimap={mode === "interactive"} onZoom={setZoom} />
    </div>
    {mode === "list" ? <ZoomPane ref={listPane} zoom={listZoom} onZoom={setListZoom} label="Список узлов графа">
      <GraphList topology={topology} manifest={manifest} runtime={runtime}
        selected={selected} onSelect={onSelect} query={query} />
    </ZoomPane> : null}
    {selected ? <NodeDetails nodeId={selected} topology={topology} manifest={manifest} runtime={runtime}
      onClose={() => onSelect(null)} /> : null}
  </div>;
});
