import type { GraphLayout, NodeBox, Point } from "./layout";
import { routeEdge } from "./routing";

export type EdgeRoutes = Record<string, Point[]>;
export type RoutingRequest =
  | { type: "init"; layout: GraphLayout }
  | { type: "route"; requestId: number; boxes: NodeBox[] };
export type RoutingReply =
  | { type: "routes"; requestId: number; routes: EdgeRoutes }
  | { type: "error"; requestId: number; message: string };

// This module runs only inside a dedicated worker. Keep its surface explicit
// without adding WebWorker globals to the application's DOM TypeScript lib.
declare const self: {
  onmessage: ((event: MessageEvent<RoutingRequest>) => void) | null;
  postMessage: (message: RoutingReply) => void;
};

let layout: GraphLayout | null = null;
let previous: EdgeRoutes = Object.create(null);

self.onmessage = ({ data }) => {
  if (data.type === "init") {
    layout = data.layout;
    previous = Object.create(null);
    return;
  }
  try {
    if (!layout) throw new Error("Routing worker has no layout");
    const boxes = new Map(data.boxes.map((box) => [box.id, box]));
    const nodes = new Map(layout.nodes.map((node) => [node.id, node]));
    const routes: EdgeRoutes = Object.create(null);
    for (const edge of layout.edges) {
      const from = boxes.get(edge.source), to = boxes.get(edge.target);
      if (!from || !to) continue;
      const sourcePort = nodes.get(edge.source)?.ports.find((port) => port.id === edge.sourceHandle);
      const targetPort = nodes.get(edge.target)?.ports.find((port) => port.id === edge.targetHandle);
      if (!sourcePort || !targetPort) throw new Error(`Missing port for ${edge.id}`);
      routes[edge.id] = routeEdge(
        { x: from.x + from.width, y: from.y + sourcePort.y },
        { x: to.x, y: to.y + targetPort.y },
        previous[edge.id] ?? edge.points,
        data.boxes,
      );
    }
    previous = routes;
    self.postMessage({ type: "routes", requestId: data.requestId, routes });
  } catch (cause) {
    self.postMessage({ type: "error", requestId: data.requestId,
      message: cause instanceof Error ? cause.message : String(cause) });
  }
};
