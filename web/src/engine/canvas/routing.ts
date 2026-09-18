import type { NodeBox, Point } from "./layout";

const same = (a: Point, b: Point) => Math.abs(a.x - b.x) < 0.01 && Math.abs(a.y - b.y) < 0.01;

/** Strict interior intersection: an endpoint on a port is allowed. */
export function crossesNode(a: Point, b: Point, box: NodeBox): boolean {
  const left = box.x + 0.1, right = box.x + box.width - 0.1;
  const top = box.y + 0.1, bottom = box.y + box.height - 0.1;
  if (Math.abs(a.y - b.y) < 0.01) {
    return a.y > top && a.y < bottom && Math.max(a.x, b.x) > left && Math.min(a.x, b.x) < right;
  }
  if (Math.abs(a.x - b.x) < 0.01) {
    return a.x > left && a.x < right && Math.max(a.y, b.y) > top && Math.min(a.y, b.y) < bottom;
  }
  return true;
}

export function clearRoute(points: Point[], boxes: NodeBox[]) {
  return points.length > 1 && points.slice(1).every((point, i) =>
    !boxes.some((box) => crossesNode(points[i], point, box)),
  );
}

function simplify(points: Point[]): Point[] {
  const result: Point[] = [];
  for (const point of points) {
    if (result.length && same(result[result.length - 1], point)) continue;
    while (result.length > 1) {
      const a = result[result.length - 2], b = result[result.length - 1];
      if ((a.x === b.x && b.x === point.x) || (a.y === b.y && b.y === point.y)) result.pop();
      else break;
    }
    result.push(point);
  }
  return result;
}

/** Keep ELK's lanes while valid. After a manual move, route through free lanes
 * around actual node bounds. No re-layout, and no stale absolute endpoints. */
export function routeEdge(start: Point, end: Point, original: Point[], boxes: NodeBox[]): Point[] {
  if (original.length > 1 && same(start, original[0]) && same(end, original[original.length - 1])
    && clearRoute(original, boxes)) return original;
  // A fixed lead can land inside the next card when the user narrows a gap.
  // Shorten it to half the available horizontal clearance on that port's row.
  const sourceSpace = boxes.filter((b) => start.y > b.y && start.y < b.y + b.height && b.x >= start.x)
    .map((b) => (b.x - start.x) / 2);
  const targetSpace = boxes.filter((b) => end.y > b.y && end.y < b.y + b.height && b.x + b.width <= end.x)
    .map((b) => (end.x - b.x - b.width) / 2);
  const from = { x: start.x + Math.min(20, ...sourceSpace), y: start.y };
  const to = { x: end.x - Math.min(20, ...targetSpace), y: end.y };
  const middle = (from.x + to.x) / 2;
  const simple = [start, from, { x: middle, y: from.y }, { x: middle, y: to.y }, to, end];
  if (from.x <= to.x && clearRoute(simple, boxes)) return simplify(simple);
  // Most feedback edges can use a clear lane above/below both endpoints.
  // Try these before constructing a visibility grid.
  const lanes = [Math.min(start.y, end.y) - 40, Math.max(start.y, end.y) + 40,
    Math.min(...boxes.map((b) => b.y)) - 20, Math.max(...boxes.map((b) => b.y + b.height)) + 20];
  for (const y of lanes) {
    const route = [start, from, { x: from.x, y }, { x: to.x, y }, to, end];
    if (clearRoute(route, boxes)) return simplify(route);
  }

  // Rectilinear visibility grid. Lanes lie outside cards, so every adjacent
  // segment can be checked without sampling SVG pixels or measuring the DOM.
  const xs = [...new Set([from.x, to.x, ...boxes.flatMap((b) => [b.x - 20, b.x + b.width + 20])])].sort((a, b) => a - b);
  const ys = [...new Set([from.y, to.y, ...boxes.flatMap((b) => [b.y - 20, b.y + b.height + 20])])].sort((a, b) => a - b);
  const cols = xs.length;
  // Index occupied intervals per grid line once. A* then checks a segment in
  // O(log n), instead of scanning every card for every expanded neighbor.
  const intervals = (values: [number, number][]) => {
    values.sort((a, b) => a[0] - b[0]);
    const merged: [number, number][] = [];
    for (const value of values) {
      const last = merged[merged.length - 1];
      if (last && value[0] <= last[1]) last[1] = Math.max(last[1], value[1]);
      else merged.push([...value]);
    }
    return merged;
  };
  const rowsBlocked = ys.map((y) => intervals(boxes.filter((b) => y > b.y + 0.1 && y < b.y + b.height - 0.1)
    .map((b) => [b.x + 0.1, b.x + b.width - 0.1])));
  const colsBlocked = xs.map((x) => intervals(boxes.filter((b) => x > b.x + 0.1 && x < b.x + b.width - 0.1)
    .map((b) => [b.y + 0.1, b.y + b.height - 0.1])));
  const intersects = (occupied: [number, number][], a: number, b: number) => {
    const low = Math.min(a, b), high = Math.max(a, b);
    let left = 0, right = occupied.length;
    while (left < right) {
      const mid = (left + right) >> 1;
      if (occupied[mid][1] <= low) left = mid + 1;
      else right = mid;
    }
    return left < occupied.length && occupied[left][0] < high;
  };
  const first = ys.indexOf(from.y) * cols + xs.indexOf(from.x);
  const last = ys.indexOf(to.y) * cols + xs.indexOf(to.x);
  const pointOf = (index: number): Point => ({ x: xs[index % cols], y: ys[Math.floor(index / cols)] });
  const distance = (a: Point, b: Point) => Math.abs(a.x - b.x) + Math.abs(a.y - b.y);
  // A state includes arrival direction; otherwise the bend penalty can prune
  // the shortest route incorrectly. Binary heap keeps large graphs responsive.
  const heap: { key: number; cost: number; priority: number }[] = [];
  const push = (value: typeof heap[number]) => {
    heap.push(value);
    let i = heap.length - 1;
    while (i > 0) {
      const parent = (i - 1) >> 1;
      if (heap[parent].priority <= value.priority) break;
      heap[i] = heap[parent]; i = parent;
    }
    heap[i] = value;
  };
  const pop = () => {
    const first = heap[0], tail = heap.pop()!;
    if (heap.length) {
      let i = 0;
      while (i * 2 + 1 < heap.length) {
        let child = i * 2 + 1;
        if (child + 1 < heap.length && heap[child + 1].priority < heap[child].priority) child++;
        if (tail.priority <= heap[child].priority) break;
        heap[i] = heap[child]; i = child;
      }
      heap[i] = tail;
    }
    return first;
  };
  const costs = new Float64Array(cols * ys.length * 2).fill(Infinity);
  costs[first * 2] = 0;
  const previous = new Int32Array(costs.length).fill(-1);
  push({ key: first * 2, cost: 0, priority: distance(from, to) });
  while (heap.length) {
    const current = pop();
    if (current.cost !== costs[current.key]) continue;
    const index = Math.floor(current.key / 2), direction = current.key % 2;
    if (index === last) {
      const route: Point[] = [];
      let key = current.key;
      while (key !== -1) { route.push(pointOf(Math.floor(key / 2))); key = previous[key]; }
      const result = simplify([start, ...route.reverse(), end]);
      if (clearRoute(result, boxes)) return result;
      break;
    }
    const x = index % cols, y = Math.floor(index / cols);
    const neighbors = [x > 0 ? index - 1 : -1, x + 1 < cols ? index + 1 : -1,
      y > 0 ? index - cols : -1, y + 1 < ys.length ? index + cols : -1];
    for (let d = 0; d < neighbors.length; d++) {
      const next = neighbors[d];
      if (next < 0) continue;
      const a = pointOf(index), b = pointOf(next), nextDirection = d < 2 ? 0 : 1;
      if (nextDirection === 0 ? intersects(rowsBlocked[y], a.x, b.x) : intersects(colsBlocked[x], a.y, b.y)) continue;
      const cost = current.cost + distance(a, b) + (direction === nextDirection ? 0 : 24);
      const key = next * 2 + nextDirection;
      if (cost >= costs[key]) continue;
      costs[key] = cost; previous[key] = current.key;
      push({ key, cost, priority: cost + distance(b, to) });
    }
  }
  // Overlapping cards can enclose a port completely. Keep endpoints attached;
  // moving the cards apart restores obstacle routing automatically.
  const lane = Math.min(start.y, end.y, ...boxes.map((b) => b.y)) - 28;
  return simplify([start, from, { x: from.x, y: lane }, { x: to.x, y: lane }, to, end]);
}

export function pathOf(points: Point[]): string {
  return points.map((point, index) => `${index ? "L" : "M"} ${point.x} ${point.y}`).join(" ");
}
