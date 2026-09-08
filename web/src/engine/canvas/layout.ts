import type { GraphTopology } from "../../lib/graph";

export type LayoutHints = Record<string, { rank?: number; order?: number }>;
export type NodePosition = { id: string; x: number; y: number };

export function layeredLayout(topology: GraphTopology, hints: LayoutHints = {}): NodePosition[] {
  const ids = topology.nodes.map((node) => node.id);
  const rank = new Map<string, number>(ids.map((id) => [id, hints[id]?.rank ?? 0]));
  const incoming = new Map<string, number>(ids.map((id) => [id, 0]));
  topology.edges.forEach((edge) => incoming.set(edge.target, (incoming.get(edge.target) ?? 0) + 1));
  const queue = ids.filter((id) => (incoming.get(id) ?? 0) === 0).sort();
  for (let cursor = 0; cursor < queue.length; cursor += 1) {
    const current = queue[cursor];
    for (const edge of topology.edges.filter((item) => item.source === current)) {
      rank.set(edge.target, Math.max(rank.get(edge.target) ?? 0, (rank.get(current) ?? 0) + 1));
      incoming.set(edge.target, (incoming.get(edge.target) ?? 1) - 1);
      if (incoming.get(edge.target) === 0) queue.push(edge.target);
    }
  }
  const maxRank = Math.max(0, ...rank.values());
  ids.forEach((id) => { if (!queue.includes(id) && (rank.get(id) ?? 0) === 0) rank.set(id, maxRank + 1); });
  const layers = new Map<number, string[]>();
  ids.forEach((id) => {
    const layer = rank.get(id) ?? 0;
    if (!layers.has(layer)) layers.set(layer, []);
    layers.get(layer)?.push(id);
  });
  const positions: NodePosition[] = [];
  [...layers.entries()].sort(([a], [b]) => a - b).forEach(([layer, nodes]) => {
    nodes.sort((a, b) => (hints[a]?.order ?? 0) - (hints[b]?.order ?? 0) || a.localeCompare(b));
    nodes.forEach((id, index) => positions.push({ id, x: 40 + layer * 210, y: 40 + index * 100 }));
  });
  return positions;
}
