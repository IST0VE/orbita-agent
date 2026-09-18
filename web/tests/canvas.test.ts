import assert from "node:assert/strict";
import test from "node:test";
import ELK from "elkjs/lib/elk.bundled.js";
import { layeredLayout, layoutInput, type NodeBox } from "../src/engine/canvas/layout.ts";
import { clearRoute, pathOf, routeEdge } from "../src/engine/canvas/routing.ts";

const topology = {
  nodes: ["start", "a", "tools", "gate", "b", "budget", "end", "isolated"].map((id) => ({ id })),
  edges: [["start", "a"], ["a", "tools"], ["tools", "a"], ["a", "gate"],
    ["gate", "b"], ["gate", "budget"], ["start", "budget"], ["b", "end"],
    ["budget", "end"], ["tools", "tools"], ["a", "gate"]].map(([source, target]) => ({ source, target })),
};

test("ELK routes cycles, self loops and parallel edges without crossing cards", async () => {
  const result = await layeredLayout(topology, {}, new ELK());
  assert.equal(result.nodes.length, topology.nodes.length);
  assert.equal(result.edges.length, topology.edges.length);
  for (const a of result.nodes) for (const b of result.nodes) {
    if (a.id === b.id) continue;
    assert.ok(a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y,
      `${a.id} overlaps ${b.id}`);
  }
  for (const edge of result.edges) {
    const from = result.nodes.find((n) => n.id === edge.source)!;
    const to = result.nodes.find((n) => n.id === edge.target)!;
    assert.ok(Math.abs(edge.points[0].x - from.x - from.width) < 0.01);
    assert.ok(Math.abs(edge.points[0].y - from.y - from.ports.find((p) => p.id === edge.sourceHandle)!.y) < 0.01);
    assert.ok(Math.abs(edge.points.at(-1)!.x - to.x) < 0.01);
    assert.ok(Math.abs(edge.points.at(-1)!.y - to.y - to.ports.find((p) => p.id === edge.targetHandle)!.y) < 0.01);
    assert.ok(clearRoute(edge.points, result.nodes), `${edge.id} crosses a card`);
  }
});

test("empty and dangling topology is safe and port ids are unique", async () => {
  assert.deepEqual(await layeredLayout({ nodes: [], edges: [] }, {}, new ELK()), { nodes: [], edges: [] });
  const input = layoutInput({ ...topology, edges: [...topology.edges, { source: "missing", target: "a" }] });
  assert.equal(input.edges.length, topology.edges.length);
  for (const node of input.nodes) assert.equal(new Set(node.ports.map((p) => p.id)).size, node.ports.length);
});

test("manual move invalidates stale routes and avoids an unrelated blocking node", () => {
  const boxes: NodeBox[] = [
    { id: "a", x: 0, y: 0, width: 200, height: 60 },
    { id: "b", x: 600, y: 100, width: 200, height: 60 },
    { id: "obstacle", x: 280, y: -30, width: 220, height: 210 },
  ];
  const start = { x: 200, y: 30 }, end = { x: 600, y: 130 };
  const result = routeEdge(start, end, [start, { x: 600, y: 30 }], boxes);
  assert.deepEqual(result[0], start);
  assert.deepEqual(result.at(-1), end);
  assert.ok(clearRoute(result, boxes));
  assert.ok(!pathOf(result).includes("NaN"));
});

test("backward edges, self loops and negative coordinates remain attached", () => {
  const boxes = [{ id: "a", x: -300, y: -100, width: 216, height: 68 },
    { id: "b", x: 50, y: 20, width: 216, height: 68 }];
  for (const [start, end] of [
    [{ x: 266, y: 54 }, { x: -300, y: -66 }],
    [{ x: -84, y: -66 }, { x: -300, y: -66 }],
  ]) {
    const route = routeEdge(start, end, [], boxes);
    assert.deepEqual(route[0], start); assert.deepEqual(route.at(-1), end);
    assert.ok(clearRoute(route, boxes));
  }
});

test("all edges still avoid nodes after arbitrary manual moves", async () => {
  const result = await layeredLayout(topology, {}, new ELK());
  const moved = result.nodes.map((node, i) => ({ ...node, x: (i % 4) * 360 - 500, y: Math.floor(i / 4) * 260 - 100 }));
  for (const edge of result.edges) {
    const from = moved.find((n) => n.id === edge.source)!;
    const to = moved.find((n) => n.id === edge.target)!;
    const start = { x: from.x + from.width, y: from.y + from.ports.find((p) => p.id === edge.sourceHandle)!.y };
    const end = { x: to.x, y: to.y + to.ports.find((p) => p.id === edge.targetHandle)!.y };
    assert.ok(clearRoute(routeEdge(start, end, edge.points, moved), moved), edge.id);
  }
});

test("narrow gaps between manually placed cards do not swallow edge leads", () => {
  const boxes = [{ id: "a", x: 0, y: 0, width: 216, height: 68 },
    { id: "b", x: 230, y: 0, width: 216, height: 68 }];
  for (const endY of [34, 50]) {
    const route = routeEdge({ x: 216, y: 34 }, { x: 230, y: endY }, [], boxes);
    assert.ok(clearRoute(route, boxes));
  }
});
