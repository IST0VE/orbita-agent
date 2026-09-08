import { memo, useEffect, useMemo, useState } from "react";
import type { GraphTopology } from "../../lib/graph";
import type { UiManifest } from "../manifest/types";
import { nodeStatus, visitCount } from "../runtime/selectors";
import type { RuntimeSnapshot } from "../runtime/types";
import { AsciiGraphCanvas } from "./AsciiGraphCanvas";
import { layeredLayout, type NodePosition } from "./layout";

type ViewMode = "ascii" | "visual" | "list";

export const GraphCanvas = memo(function GraphCanvas({ topology, manifest, runtime, selected, onSelect }: {
  topology: GraphTopology;
  manifest: UiManifest;
  runtime: RuntimeSnapshot;
  selected: string | null;
  onSelect: (nodeId: string | null) => void;
}) {
  // ASCII — основной режим: он читается в терминальном интерфейсе без
  // масштаба и панорамы и показывает пройденный путь одной картинкой.
  const [mode, setMode] = useState<ViewMode>("ascii");
  const [query, setQuery] = useState("");
  const [zoom, setZoom] = useState(1);
  const hints = useMemo(() => Object.fromEntries(Object.entries(manifest.nodes ?? {}).map(([id, node]) => [id, node.layout ?? {}])), [manifest.nodes]);
  const [positions, setPositions] = useState<NodePosition[]>([]);
  useEffect(() => {
    if (mode !== "visual") return;
    if (topology.nodes.length <= 50 || typeof Worker === "undefined") {
      setPositions(layeredLayout(topology, hints));
      return;
    }
    const worker = new Worker(new URL("./layout.worker.ts", import.meta.url), { type: "module" });
    const timeout = window.setTimeout(() => { worker.terminate(); setPositions(layeredLayout(topology, hints)); }, 1500);
    worker.onmessage = (event: MessageEvent<NodePosition[]>) => { window.clearTimeout(timeout); setPositions(event.data); worker.terminate(); };
    worker.postMessage([topology, hints]);
    return () => { window.clearTimeout(timeout); worker.terminate(); };
  }, [hints, topology, mode]);

  const byId = new Map(positions.map((position) => [position.id, position]));
  const width = Math.max(600, ...positions.map((position) => position.x + 190));
  const height = Math.max(260, ...positions.map((position) => position.y + 90));
  const visible = (id: string) => !query || `${id} ${manifest.nodes?.[id]?.title ?? ""}`.toLowerCase().includes(query.toLowerCase());
  const selectedManifest = selected ? manifest.nodes?.[selected] : undefined;
  const selectedExecutions = selected ? runtime.executionOrder.map((id) => runtime.executions[id]).filter((item) => item.nodeId === selected) : [];

  return <div className="engine-canvas">
    <div className="canvas-tools"><input aria-label="Поиск узла" value={query} placeholder="найти узел" onChange={(event) => { setQuery(event.target.value); if (mode === "ascii" && event.target.value) setMode("list"); }} />{(["ascii", "visual", "list"] as ViewMode[]).map((item) => <button key={item} className={mode === item ? "on" : ""} aria-pressed={mode === item} onClick={() => { setMode(item); if (item === "ascii") setQuery(""); }}>[{{ascii: "схема", visual: "интерактивно", list: "список"}[item]}]</button>)}{mode === "visual" ? <><button onClick={() => setZoom((value) => Math.max(.5, value - .1))}>[−]</button><output>{Math.round(zoom * 100)}%</output><button onClick={() => setZoom((value) => Math.min(2, value + .1))}>[+]</button><button onClick={() => setZoom(1)}>[100%]</button></> : null}</div>
    {mode === "ascii" ? <AsciiGraphCanvas topology={topology} manifest={manifest} runtime={runtime} /> : null}
    {mode === "list" ? <table className="graph-list"><thead><tr><th>Узел</th><th>Тип</th><th>Статус</th><th>Запуски</th></tr></thead><tbody>{topology.nodes.filter((node) => visible(node.id)).map((node) => <tr key={node.id} tabIndex={0} onClick={() => onSelect(node.id)}><td>{manifest.nodes?.[node.id]?.title ?? node.id}<small>{node.id}</small></td><td>{manifest.nodes?.[node.id]?.kind ?? "task"}</td><td>{nodeStatus(runtime, node.id)}</td><td>{visitCount(runtime, node.id)}</td></tr>)}</tbody></table> : null}
    {mode === "visual" ? <div className="canvas-viewport"><svg width={width * zoom} height={height * zoom} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Интерактивная схема графа"><defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" /></marker></defs>{topology.edges.map((edge, index) => {
      const from = byId.get(edge.source); const to = byId.get(edge.target); if (!from || !to) return null;
      const backwards = to.x <= from.x; const path = backwards ? `M ${from.x + 160} ${from.y + 28} C ${from.x + 210} ${from.y - 50}, ${to.x - 50} ${to.y - 50}, ${to.x} ${to.y + 28}` : `M ${from.x + 160} ${from.y + 28} C ${from.x + 180} ${from.y + 28}, ${to.x - 20} ${to.y + 28}, ${to.x} ${to.y + 28}`;
      return <path key={`${edge.source}:${edge.target}:${index}`} d={path} className={edge.conditional ? "canvas-edge conditional" : "canvas-edge"} markerEnd="url(#arrow)" />;
    })}{topology.nodes.filter((node) => visible(node.id)).map((node) => {
      const position = byId.get(node.id); if (!position) return null; const status = nodeStatus(runtime, node.id); const count = visitCount(runtime, node.id); const info = manifest.nodes?.[node.id];
      return <g key={node.id} transform={`translate(${position.x} ${position.y})`} className={`canvas-node node-${status} kind-${info?.kind ?? "task"} ${selected === node.id ? "selected" : ""}`} role="button" tabIndex={0} aria-label={`${info?.title ?? node.id}: ${status}`} onClick={() => onSelect(node.id)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") onSelect(node.id); }}><rect width="160" height="56" rx="4" /><text x="80" y="23" textAnchor="middle">{(info?.title ?? node.id).slice(0, 24)}</text><text x="80" y="42" textAnchor="middle" className="node-meta">{status}{count > 1 ? ` ×${count}` : ""}</text></g>;
    })}</svg><div className="canvas-minimap" aria-hidden="true">{topology.nodes.length} nodes · {topology.edges.length} edges</div></div> : null}
    {selected ? <aside className="node-details"><button onClick={() => onSelect(null)}>[закрыть]</button><h3>{selectedManifest?.title ?? selected}</h3><code>{selected}</code><p>{selectedManifest?.description}</p><div>тип: {selectedManifest?.kind ?? "task"}</div><div>входящих: {topology.edges.filter((edge) => edge.target === selected).length} · исходящих: {topology.edges.filter((edge) => edge.source === selected).length}</div><h4>Executions</h4>{selectedExecutions.length ? selectedExecutions.map((execution) => <div key={execution.executionId}>{execution.attempt}. {execution.status}{execution.durationMs !== undefined ? ` · ${execution.durationMs} ms` : ""}</div>) : <span className="hint">ещё не выполнялся</span>}</aside> : null}
  </div>;
}, (previous, next) => previous.topology === next.topology && previous.manifest === next.manifest && previous.selected === next.selected && previous.onSelect === next.onSelect && previous.runtime.executions === next.runtime.executions && previous.runtime.executionOrder === next.runtime.executionOrder && previous.runtime.interrupts === next.runtime.interrupts && previous.runtime.runStatus === next.runtime.runStatus);
