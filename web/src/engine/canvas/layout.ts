import type { GraphTopology } from "../../lib/graph";

import type { ElkNode, ELK } from "elkjs/lib/elk-api";

// Shared geometry: coordinates describe the actual card, including terminals.
export const NODE_WIDTH = 216;
export const NODE_HEIGHT = 68;
export const TERMINAL_WIDTH = 132;
export const TERMINAL_HEIGHT = 40;
export const CANVAS_PADDING = 40;
export type LayoutHints = Record<string, { rank?: number; order?: number }>;
export type Point = { x: number; y: number };
export type NodePosition = Point & { id: string };
export type NodeBox = NodePosition & { width: number; height: number };
export type Port = { id: string; type: "source" | "target"; y: number };
export type LayoutNode = NodeBox & { ports: Port[] };
export type LayoutEdge = { id: string; source: string; target: string; sourceHandle: string; targetHandle: string; points: Point[] };
export type GraphLayout = { nodes: LayoutNode[]; edges: LayoutEdge[] };

export function nodeSize(id: string) {
  const terminal = id === "__start__" || id === "__end__";
  return { width: terminal ? TERMINAL_WIDTH : NODE_WIDTH, height: terminal ? TERMINAL_HEIGHT : NODE_HEIGHT };
}

/** Stable port identities distinguish parallel edges and self loops. */
export function layoutInput(topology: GraphTopology, hints: LayoutHints = {}) {
  const ids = new Set(topology.nodes.map((node) => node.id));
  const edges = topology.edges.flatMap((edge, index) => ids.has(edge.source) && ids.has(edge.target)
    ? [{ ...edge, id: `edge-${index}`, sourceHandle: `out-${index}`, targetHandle: `in-${index}` }] : []);
  const orderedIds = [...ids].sort((a, b) =>
    (hints[a]?.rank ?? 0) - (hints[b]?.rank ?? 0)
    || (hints[a]?.order ?? 0) - (hints[b]?.order ?? 0)
    || a.localeCompare(b),
  );
  const order = new Map(orderedIds.map((id, index) => [id, index]));
  const nodes: LayoutNode[] = orderedIds.map((id) => {
    const size = nodeSize(id);
    const ports: Port[] = [];
    for (const type of ["source", "target"] as const) {
      const opposite = type === "source" ? "target" : "source";
      const connected = edges.filter((edge) => edge[type] === id)
        .sort((a, b) => order.get(a[opposite])! - order.get(b[opposite])!);
      connected.forEach((edge, index) => ports.push({
        id: type === "source" ? edge.sourceHandle : edge.targetHandle,
        type, y: size.height * (index + 1) / (connected.length + 1),
      }));
    }
    return { id, ...size, x: 0, y: 0, ports };
  });
  return { nodes, edges };
}

/** The caller supplies ELK: a native worker in the browser, bundled in tests. */
export async function layeredLayout(topology: GraphTopology, hints: LayoutHints, elk: Pick<ELK, "layout">): Promise<GraphLayout> {
  const input = layoutInput(topology, hints);
  if (!input.nodes.length) return { nodes: [], edges: [] };
  const result = await elk.layout<ElkNode>({
    id: "graph",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.padding": "[top=40,left=40,bottom=40,right=40]",
      "elk.spacing.nodeNode": "52",
      "elk.layered.spacing.nodeNodeBetweenLayers": "88",
      "elk.layered.spacing.edgeNodeBetweenLayers": "24",
      "elk.spacing.edgeEdge": "14",
      "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
      "elk.layered.crossingMinimization.forceNodeModelOrder": "true",
      "elk.randomSeed": "1",
    },
    children: input.nodes.map((node) => ({
      id: node.id, width: node.width, height: node.height,
      layoutOptions: { "elk.portConstraints": "FIXED_POS" },
      ports: node.ports.map((port) => ({
        id: `${node.id}:${port.id}`, width: 0, height: 0,
        x: port.type === "source" ? node.width : 0, y: port.y,
        layoutOptions: { "elk.port.side": port.type === "source" ? "EAST" : "WEST" },
      })),
    })),
    edges: input.edges.map((edge) => ({
      id: edge.id,
      sources: [`${edge.source}:${edge.sourceHandle}`],
      targets: [`${edge.target}:${edge.targetHandle}`],
    })),
  });
  const placed = new Map(result.children?.map((node) => [node.id, node]));
  const routed = new Map(result.edges?.map((edge) => [edge.id, edge]));
  return {
    nodes: input.nodes.map((node) => ({ ...node, x: placed.get(node.id)?.x ?? 0, y: placed.get(node.id)?.y ?? 0 })),
    edges: input.edges.map((edge) => {
      const section = routed.get(edge.id)?.sections?.[0];
      return { ...edge, points: section ? [section.startPoint, ...section.bendPoints ?? [], section.endPoint] : [] };
    }),
  };
}
