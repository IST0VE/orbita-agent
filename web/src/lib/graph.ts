/**
 * Топология графа: то, что приезжает с сервера, и мелочи вокруг неё.
 *
 * Узлы и рёбра берутся у сервера (`GET /assistants/{id}/graph`), а не
 * переписываются сюда руками: граф живёт в `src/agent/graph.py`, и второй его
 * экземпляр в вебе рано или поздно разошёлся бы с первым. Раскладка поэтому
 * тоже считается (`engine/canvas/layout.ts`), а не расставляется по
 * координатам: добавили узел в Python — картинка поехала следом сама.
 *
 * Отрисовка отсюда ушла. Раньше здесь же лежал рисовальщик схемы знаками:
 * рамки из `┌─┐`, стрелки из `│` и `└`, раскладка по колонкам в символах.
 * Схему рисует `engine/canvas/GraphDiagram.tsx` через React Flow, а ELK
 * рассчитывает начальную раскладку. Топология не хранит ручные позиции узлов.
 */

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

/** Пары соседей в маршруте — по ним подсвечиваются пройденные рёбра. */
export function traversedEdges(path: string[]): Set<string> {
  const pairs = new Set<string>();
  for (let i = 0; i + 1 < path.length; i += 1) pairs.add(`${path[i]}→${path[i + 1]}`);
  return pairs;
}

/** Ключ ребра в наборе пройденных: одна форма записи на весь фронтенд. */
export function edgeKey(source: string, target: string): string {
  return `${source}→${target}`;
}

/** Границы хода рисуются таблеткой, а не карточкой задачи. */
export function isTerminal(nodeId: string): boolean {
  return nodeId === START || nodeId === END;
}
