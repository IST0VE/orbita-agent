/**
 * Топология графа и её отрисовка знаками.
 *
 * Узлы и рёбра берутся у сервера (`GET /assistants/{id}/graph`), а не
 * переписываются сюда руками: граф живёт в `src/agent/graph.py`, и второй его
 * экземпляр в вебе рано или поздно разошёлся бы с первым. Раскладка поэтому
 * тоже считается, а не расставляется по координатам: добавили узел в Python —
 * картинка поехала следом сама.
 */

import { Canvas, boxWidth } from "./ascii";

export const START = "__start__";
export const END = "__end__";

export type GraphEdge = {
  source: string;
  target: string;
  /** Метка ветки у условного перехода — имя, которое вернул роутер. */
  data?: string;
  conditional?: boolean;
};

export type GraphTopology = {
  nodes: { id: string }[];
  edges: GraphEdge[];
};

function nodeInfo(id: string): { label: string; hint: string } {
  if (id === START) return { label: "старт", hint: "Начало хода" };
  if (id === END) return { label: "конец", hint: "Ход завершён" };
  return { label: id, hint: id };
}

export type NodeState = "idle" | "done" | "active" | "wait";

const NODE_CLASS: Record<NodeState, string> = {
  idle: "g-idle",
  done: "g-done",
  active: "g-active",
  wait: "g-wait",
};

/** Пары соседей в маршруте — по ним подсвечиваются пройденные рёбра. */
export function traversedEdges(path: string[]): Set<string> {
  const pairs = new Set<string>();
  for (let i = 0; i + 1 < path.length; i += 1) pairs.add(`${path[i]}→${path[i + 1]}`);
  return pairs;
}

/* ------------------------------------------------------------------ */
/* Раскладка по слоям                                                  */
/* ------------------------------------------------------------------ */

const BOX_H = 3;
const V_GAP = 1;
/**
 * Ширина промежутка между колонками.
 *
 * В промежутках живут все вертикальные участки рёбер, и это не украшение:
 * колонка занята рамками, и линия, проведённая по её середине, прошла бы
 * сквозь чужой узел. Промежуток свободен всегда, поэтому места в нём
 * расписаны заранее — по одному столбцу на каждый вид участка.
 */
const GAP = 8;
/** Подъём ребра на дорожку — сразу за правым краем узла-источника. */
const OFF_RISE = 2;
/** Изгибы прямых переходов между соседними колонками: два соседних столбца. */
const OFF_BEND = 4;
/** Спуск ребра с дорожки — ближе к левому краю узла-цели. */
const OFF_DROP = 5;

type Placed = {
  id: string;
  label: string;
  col: number;
  row: number;
  x: number;
  y: number;
  w: number;
};

/**
 * Слой узла — длина ДЛИННЕЙШЕГО пути от старта.
 *
 * Не кратчайшего: по кратчайшему `память` попадала бы в одну колонку с
 * `моделью` (до неё есть короткая дорога через `лимит`), и ребро между ними
 * пришлось бы рисовать внутри колонки — то есть сквозь чужие рамки. По
 * длиннейшему каждое прямое ребро гарантированно идёт слева направо.
 *
 * Считается в два прохода, потому что в графе есть цикл `инструменты →
 * модель`. Сначала обход в ширину: он цикл переживает и даёт порядок, по
 * которому видно, какие рёбра ведут назад. Затем эти рёбра выбрасываются, и
 * на оставшемся ациклическом графе слои разгоняются до максимума.
 */
function layers(topology: GraphTopology): Map<string, number> {
  const ids = topology.nodes.map((n) => n.id);
  const outgoing = new Map<string, string[]>();
  for (const id of ids) outgoing.set(id, []);
  for (const e of topology.edges) outgoing.get(e.source)?.push(e.target);

  // Проход первый: кратчайшие пути, только чтобы отличить рёбра вперёд от
  // рёбер назад.
  const bfs = new Map<string, number>();
  const queue: string[] = [];
  if (ids.includes(START)) {
    bfs.set(START, 0);
    queue.push(START);
  }
  for (let head = 0; head < queue.length; head += 1) {
    const id = queue[head];
    for (const next of outgoing.get(id) ?? []) {
      if (bfs.has(next)) continue;
      bfs.set(next, (bfs.get(id) ?? 0) + 1);
      queue.push(next);
    }
  }
  // Узел, до которого от старта не добраться, всё равно должен быть виден:
  // отправляем его в конец, а не выбрасываем из картинки.
  const maxBfs = Math.max(0, ...bfs.values());
  for (const id of ids) if (!bfs.has(id)) bfs.set(id, maxBfs + 1);

  // Проход второй: длиннейший путь по рёбрам вперёд. Повторяем, пока слои
  // двигаются; шагов не больше числа узлов — длиннее простого пути нет.
  const depth = new Map<string, number>(ids.map((id) => [id, bfs.get(id) === 0 ? 0 : 0]));
  const forward = topology.edges.filter(
    (e) => (bfs.get(e.target) ?? 0) > (bfs.get(e.source) ?? 0),
  );
  for (let pass = 0; pass < ids.length; pass += 1) {
    let moved = false;
    for (const e of forward) {
      const want = (depth.get(e.source) ?? 0) + 1;
      if (want > (depth.get(e.target) ?? 0)) {
        depth.set(e.target, want);
        moved = true;
      }
    }
    if (!moved) break;
  }
  // Недостижимые узлы рёбер вперёд не получили и остались на нуле — там им
  // не место: отправляем их за последнюю колонку.
  const maxDepth = Math.max(0, ...depth.values());
  for (const id of ids) {
    if (id !== START && (depth.get(id) ?? 0) === 0) depth.set(id, maxDepth + 1);
  }
  return depth;
}

export type RenderInput = {
  topology: GraphTopology;
  /** Состояние каждого узла: чем его красить. */
  stateOf: (id: string) => NodeState;
  /** Пройденные рёбра в виде `источник→цель`. */
  traversed: Set<string>;
  /** Сколько раз узел пройден: показывается как `×2` у циклов. */
  counts: Map<string, number>;
  /** UI semantics come from the backend manifest; technical ids are fallback. */
  infoOf?: (id: string) => { label: string; hint: string };
};

export type Rendered = { canvas: Canvas; hints: Map<string, string> };

/**
 * Схема графа на символьном полотне.
 *
 * Рёбра разведены по трём случаям: соседние колонки идут напрямую через
 * промежуток, дальние — по дорожке под схемой, обратные — по дорожке над ней.
 * У каждого ребра своя дорожка и свой отступ внутри промежутка, поэтому две
 * стрелки не сливаются в одну линию, по которой не понять, куда она ведёт.
 */
export function render({ topology, stateOf, traversed, counts, infoOf = nodeInfo }: RenderInput): Rendered {
  const depth = layers(topology);
  const columns = new Map<number, string[]>();
  for (const { id } of topology.nodes) {
    const d = depth.get(id) ?? 0;
    if (!columns.has(d)) columns.set(d, []);
    (columns.get(d) as string[]).push(id);
  }

  const colKeys = [...columns.keys()].sort((a, b) => a - b);
  const rowsInTallest = Math.max(...colKeys.map((c) => (columns.get(c) as string[]).length));

  const back = topology.edges.filter((e) => (depth.get(e.target) ?? 0) <= (depth.get(e.source) ?? 0));
  const skip = topology.edges.filter((e) => (depth.get(e.target) ?? 0) - (depth.get(e.source) ?? 0) > 1);

  const topLanes = back.length;
  const bottomLanes = skip.length;
  const bodyTop = topLanes + (topLanes ? 1 : 0);
  const bodyH = rowsInTallest * BOX_H + (rowsInTallest - 1) * V_GAP;
  const height = bodyTop + bodyH + (bottomLanes ? bottomLanes + 1 : 0);

  // Ширины колонок: по самой длинной подписи в колонке, чтобы рамки в одной
  // колонке были одинаковыми, а между колонками — по месту.
  const placed = new Map<string, Placed>();
  let x = 0;
  colKeys.forEach((key, col) => {
    const ids = columns.get(key) as string[];
    const w = Math.max(...ids.map((id) => boxWidth(infoOf(id).label)));
    const blockH = ids.length * BOX_H + (ids.length - 1) * V_GAP;
    const top = bodyTop + Math.floor((bodyH - blockH) / 2);
    ids.forEach((id, row) => {
      placed.set(id, {
        id,
        label: infoOf(id).label,
        col,
        row,
        x,
        y: top + row * (BOX_H + V_GAP),
        w,
      });
    });
    x += w + (col === colKeys.length - 1 ? 0 : GAP);
  });

  const canvas = new Canvas(x, height);
  const hints = new Map<string, string>();

  const mid = (n: Placed) => n.y + 1;
  const edgeClass = (e: GraphEdge) =>
    traversed.has(`${e.source}→${e.target}`) ? "g-flow" : "g-edge";

  // ---- рёбра рисуются первыми: рамки узлов должны лечь поверх них ----
  let backLane = 0;
  let skipLane = 0;

  topology.edges.forEach((e, index) => {
    const from = placed.get(e.source);
    const to = placed.get(e.target);
    if (!from || !to) return;
    const cls = edgeClass(e);
    const y1 = mid(from);
    const y2 = mid(to);
    const fromRight = from.x + from.w - 1;
    // Столбцы внутри промежутков слева и справа от узлов: там рамок не бывает.
    const rise = fromRight + OFF_RISE;
    const drop = Math.max(0, to.x - GAP + OFF_DROP);
    const dCol = to.col - from.col;

    if (dCol === 1) {
      // Соседние колонки: прямой переход через промежуток. Столбец изгиба
      // сдвигается по номеру ребра, чтобы два перехода в одном промежутке
      // не легли друг на друга.
      const bend = fromRight + OFF_BEND + (index % 2);
      if (y1 === y2) {
        canvas.hline(fromRight + 1, to.x - 2, y1, "─", cls);
      } else {
        canvas.hline(fromRight + 1, bend, y1, "─", cls);
        canvas.put(bend, y1, y2 > y1 ? "┐" : "┘", cls);
        canvas.vline(bend, Math.min(y1, y2) + 1, Math.max(y1, y2) - 1, "│", cls);
        canvas.put(bend, y2, y2 > y1 ? "└" : "┌", cls);
        canvas.hline(bend + 1, to.x - 2, y2, "─", cls);
      }
      canvas.put(to.x - 1, y2, "▶", cls);
      return;
    }

    // Остальные рёбра идут дорожкой: назад по слоям — над схемой, через
    // колонку и дальше — под ней. Прямая линия здесь легла бы поверх чужих
    // рамок, а дорожка свободна по построению.
    //
    // Оба вертикальных участка стоят в промежутках: подъём — сразу за правым
    // краем источника, спуск — вплотную к левому краю цели. Поэтому маршрут
    // выходит вправо и возвращается слева даже когда идёт назад: так он не
    // пересекает ни одной рамки, чей узел просто оказался по дороге.
    const backwards = dCol <= 0;
    const lane = backwards ? backLane++ : height - 1 - skipLane++;

    // Угол — это два направления, которые в нём сходятся. Выписывать четыре
    // готовых набора нельзя: у обратного ребра дорожка идёт справа налево, и
    // те же самые повороты требуют других знаков.
    const bend2 = (v: "up" | "down", h: "left" | "right") =>
      ({ "down|right": "┌", "down|left": "┐", "up|right": "└", "up|left": "┘" })[
        `${v}|${h}`
      ] as string;
    // Куда уходит вертикаль от узла к дорожке и куда идёт сама дорожка.
    const toLane = backwards ? "up" : "down";
    const fromLane = backwards ? "down" : "up";
    const along = drop < rise ? "left" : "right";
    const backAlong = drop < rise ? "right" : "left";

    const corner = {
      rise: bend2(toLane, "left"),
      laneStart: bend2(fromLane, along),
      laneEnd: bend2(fromLane, backAlong),
      drop: bend2(toLane, "right"),
    };
    // Ближняя к узлам строка дорожки: с неё начинается и на ней кончается
    // вертикальный участок, а сам угол ставится отдельно.
    const near = backwards ? lane + 1 : lane - 1;

    canvas.hline(fromRight + 1, rise - 1, y1, "─", cls);
    canvas.put(rise, y1, corner.rise, cls);
    if (backwards) canvas.vline(rise, near, y1 - 1, "│", cls);
    else canvas.vline(rise, y1 + 1, near, "│", cls);
    canvas.put(rise, lane, corner.laneStart, cls);

    // Подъём и спуск в одном столбце — горизонтали между ними нет, иначе
    // отрезок из ничего затёр бы оба угла.
    if (Math.abs(rise - drop) > 1) {
      canvas.hline(Math.min(rise, drop) + 1, Math.max(rise, drop) - 1, lane, "─", cls);
    }

    canvas.put(drop, lane, corner.laneEnd, cls);
    if (backwards) canvas.vline(drop, near, y2 - 1, "│", cls);
    else canvas.vline(drop, y2 + 1, near, "│", cls);
    canvas.put(drop, y2, corner.drop, cls);
    canvas.hline(drop + 1, to.x - 2, y2, "─", cls);
    canvas.put(to.x - 1, y2, "▶", cls);
  });

  // ---- узлы ----
  for (const node of placed.values()) {
    const state = stateOf(node.id);
    const cls = NODE_CLASS[state];
    canvas.box(node.x, node.y, node.w, BOX_H, cls);

    const pad = Math.floor((node.w - 2 - node.label.length) / 2);
    canvas.text(node.x + 1 + pad, node.y + 1, node.label, cls);

    // Счётчик проходов у циклов: `модель ×3` — это три оплаченных вызова.
    const passes = counts.get(node.id) ?? 0;
    if (passes > 1) canvas.text(node.x + node.w - 2, node.y, `×${passes}`, "g-done");

    const info = infoOf(node.id);
    hints.set(node.id, passes > 1 ? `${info.hint} Пройден ${passes} раза.` : info.hint);
  }

  return { canvas, hints };
}
