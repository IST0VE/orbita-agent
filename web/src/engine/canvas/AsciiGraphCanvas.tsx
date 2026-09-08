import { memo, useMemo } from "react";
import { END, START, render, traversedEdges, type GraphTopology, type NodeState } from "../../lib/graph";
import type { UiManifest } from "../manifest/types";
import { executionPath, nodeStatus, visitCount } from "../runtime/selectors";
import type { RuntimeSnapshot } from "../runtime/types";

export const AsciiGraphCanvas = memo(function AsciiGraphCanvas({ topology, manifest, runtime }: { topology: GraphTopology; manifest: UiManifest; runtime: RuntimeSnapshot }) {
  const drawing = useMemo(() => {
    const path = executionPath(runtime);
    const traversed = traversedEdges([START, ...path]);
    if (runtime.runStatus === "completed" && path.length) traversed.add(`${path[path.length - 1]}→${END}`);
    const stateOf = (id: string): NodeState => {
      const status = nodeStatus(runtime, id);
      if (status === "interrupted") return "wait";
      if (status === "running" || status === "queued") return "active";
      if (status === "completed") return "done";
      if (id === START && runtime.runStatus !== "idle") return "done";
      if (id === END && runtime.runStatus === "completed") return "done";
      return "idle";
    };
    return render({
      topology,
      stateOf,
      traversed,
      counts: new Map(topology.nodes.map(({ id }) => [id, visitCount(runtime, id)])),
      infoOf: (id) => ({
        label: id === START ? "старт" : id === END ? "конец" : manifest.nodes?.[id]?.title ?? id,
        hint: manifest.nodes?.[id]?.description ?? id,
      }),
    });
  }, [manifest, runtime.executions, runtime.executionOrder, runtime.interrupts, runtime.runStatus, topology]);
  const rows = useMemo(() => drawing.canvas.rows(), [drawing]);
  return <pre className="graph" translate="no" aria-label="Текстовое представление графа">{rows.map((row, y) => <div key={y}>{row.map((cell, index) => cell.cls ? <span key={index} className={cell.cls}>{cell.ch}</span> : <span key={index}>{cell.ch}</span>)}</div>)}</pre>;
});
