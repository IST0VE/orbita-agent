/**
 * Карточка выбранного узла.
 *
 * Отвечает на три вопроса, которые задают, ткнув в узел: что это, с чем он
 * связан и сколько раз он уже отработал. Всё остальное про узел живёт в
 * журнале выполнения — здесь нет места для истории, и притворяться, что
 * есть, не нужно.
 */

import { END, START, isTerminal, type GraphTopology } from "../../lib/graph";
import { X } from "../../ui/icons";
import type { UiManifest } from "../manifest/types";
import { KIND_LABELS, NODE_LABELS, NODE_TONES, type NodeStatus } from "../runtime/labels";
import type { RuntimeSnapshot } from "../runtime/types";
import { diagramStatus } from "./GraphDiagram";

export function NodeDetails({
  nodeId,
  topology,
  manifest,
  runtime,
  onClose,
}: {
  nodeId: string;
  topology: GraphTopology;
  manifest: UiManifest;
  runtime: RuntimeSnapshot;
  onClose: () => void;
}) {
  const info = manifest.nodes?.[nodeId];
  const status = diagramStatus(runtime, nodeId) as NodeStatus;
  const executions = runtime.executionOrder
    .map((id) => runtime.executions[id])
    .filter((item) => item?.nodeId === nodeId);
  const incoming = topology.edges.filter((edge) => edge.target === nodeId).length;
  const outgoing = topology.edges.filter((edge) => edge.source === nodeId).length;
  const title = isTerminal(nodeId)
    ? nodeId === START ? "Старт" : nodeId === END ? "Конец" : nodeId
    : info?.title ?? nodeId;

  return (
    <aside className="node-details" aria-label={`Узел ${title}`}>
      <div className="node-details-head">
        <h3>{title}</h3>
        <button className="btn-ghost btn-icon btn-sm" aria-label="Закрыть карточку узла" title="Закрыть" onClick={onClose}>
          <X size={16} aria-hidden="true" />
        </button>
      </div>
      <code>{nodeId}</code>
      <div className="node-details-meta">
        <span className="badge">
          <span className={`dot dot-${NODE_TONES[status] ?? "idle"}`} aria-hidden="true" />
          {NODE_LABELS[status] ?? status}
        </span>
        <span className="badge">{KIND_LABELS[info?.kind ?? "task"] ?? "Этап"}</span>
        <span className="badge">{incoming} вх · {outgoing} исх</span>
      </div>
      {info?.description ? <p>{info.description}</p> : null}
      <h4>Запуски</h4>
      {executions.length ? (
        <div className="node-details-runs">
          {executions.map((execution) => (
            <div className="node-details-run" key={execution.executionId}>
              <span className={`dot dot-${NODE_TONES[execution.status] ?? "idle"}`} aria-hidden="true" />
              <b>№{execution.attempt}</b>
              <span>{NODE_LABELS[execution.status] ?? execution.status}</span>
              {execution.durationMs !== undefined ? (
                <span className="hint">{execution.durationMs} мс</span>
              ) : null}
            </div>
          ))}
        </div>
      ) : (
        <span className="hint">Узел ещё не выполнялся.</span>
      )}
    </aside>
  );
}
