/**
 * Список узлов: та же схема, но таблицей.
 *
 * Нужен там, где схема неудобна: поиск по имени, сравнение числа запусков,
 * чтение с клавиатуры. Строка ведёт себя как кнопка — `Enter` и пробел
 * выбирают узел, — потому что это единственное, что со строкой делают.
 */

import { memo } from "react";

import { END, START, isTerminal, type GraphTopology } from "../../lib/graph";
import type { UiManifest } from "../manifest/types";
import { KIND_LABELS, NODE_LABELS, NODE_TONES, type NodeStatus } from "../runtime/labels";
import { visitCount } from "../runtime/selectors";
import type { RuntimeSnapshot } from "../runtime/types";
import { diagramStatus } from "./GraphDiagram";

export const GraphList = memo(function GraphList({
  topology,
  manifest,
  runtime,
  selected,
  onSelect,
  query = "",
}: {
  topology: GraphTopology;
  manifest: UiManifest;
  runtime: RuntimeSnapshot;
  selected: string | null;
  onSelect: (nodeId: string | null) => void;
  query?: string;
}) {
  const needle = query.trim().toLowerCase();
  const rows = topology.nodes.filter(
    (node) => !needle || `${node.id} ${manifest.nodes?.[node.id]?.title ?? ""}`.toLowerCase().includes(needle),
  );
  return (
    <div className="graph-list-wrap">
      <div className="graph-list table-scroll">
        <table>
          <thead>
            <tr>
              <th>Узел</th>
              <th>Тип</th>
              <th>Статус</th>
              <th>Запуски</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((node) => {
              const info = manifest.nodes?.[node.id];
              const status = diagramStatus(runtime, node.id) as NodeStatus;
              const title = isTerminal(node.id)
                ? node.id === START ? "Старт" : node.id === END ? "Конец" : node.id
                : info?.title ?? node.id;
              return (
                <tr
                  key={node.id}
                  tabIndex={0}
                  className={selected === node.id ? "selected" : ""}
                  onClick={() => onSelect(node.id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onSelect(node.id);
                    }
                  }}
                >
                  <td>
                    <b>{title}</b>
                    <small>{node.id}</small>
                  </td>
                  <td>
                    {isTerminal(node.id)
                      ? "Граница хода"
                      : KIND_LABELS[info?.kind ?? "task"] ?? info?.kind ?? "Этап"}
                  </td>
                  <td>
                    <span className="badge">
                      <span className={`dot dot-${NODE_TONES[status] ?? "idle"}`} aria-hidden="true" />
                      {NODE_LABELS[status] ?? status}
                    </span>
                  </td>
                  <td>{visitCount(runtime, node.id) || "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
});
