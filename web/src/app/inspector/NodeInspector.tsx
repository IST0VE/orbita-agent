/**
 * Выбранный узел: что это, что он сделал и чем закончил.
 *
 * Раньше эта карточка висела поверх полотна и закрывала собой схему ровно в
 * тот момент, когда по схеме нужно было смотреть. Теперь она в инспекторе:
 * узел на полотне остаётся изображением, подробности — сбоку.
 *
 * Вход и выход узла показываются свёрнутыми. Это единственное место в
 * интерфейсе, где видно, что именно этап получил и что вернул, — но читают
 * это раз в сто прогонов, при разборе отказа.
 */

import { END, START, isTerminal, type GraphTopology } from "../../lib/graph";
import { diagramStatus } from "../../engine/canvas/GraphDiagram";
import type { UiManifest } from "../../engine/manifest/types";
import { KIND_LABELS, NODE_LABELS, NODE_TONES, type NodeStatus } from "../../engine/runtime/labels";
import type { RuntimeSnapshot } from "../../engine/runtime/types";
import { runErrorMessage } from "../../engine/api/langgraphAdapter";
import { CircleAlert } from "../../ui/icons";

function Payload({ title, value }: { title: string; value: unknown }) {
  if (value === undefined || value === null) return null;
  return (
    <details className="node-payload">
      <summary>{title}</summary>
      <pre className="json-preview">{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

export function NodeInspector({
  nodeId,
  topology,
  manifest,
  runtime,
}: {
  nodeId: string;
  topology: GraphTopology;
  manifest: UiManifest;
  runtime: RuntimeSnapshot;
}) {
  const info = manifest.nodes?.[nodeId];
  const status = diagramStatus(runtime, nodeId) as NodeStatus;
  const executions = runtime.executionOrder
    .map((id) => runtime.executions[id])
    .filter((item) => item?.nodeId === nodeId);
  const last = executions[executions.length - 1];
  const incoming = topology.edges.filter((edge) => edge.target === nodeId).length;
  const outgoing = topology.edges.filter((edge) => edge.source === nodeId).length;
  const title = isTerminal(nodeId)
    ? nodeId === START ? "Старт" : nodeId === END ? "Конец" : nodeId
    : info?.title ?? nodeId;

  return (
    <div className="node-details" aria-label={`Узел ${title}`}>
      <div className="inspector-section">
        <h3 className="inspector-heading">{title}</h3>
        <code className="inspector-id">{nodeId}</code>
        <div className="node-details-meta">
          <span className="badge">
            <span className={`dot dot-${NODE_TONES[status] ?? "idle"}`} aria-hidden="true" />
            {NODE_LABELS[status] ?? status}
          </span>
          <span className="badge">{KIND_LABELS[info?.kind ?? "task"] ?? "Этап"}</span>
          <span className="badge" title="Входящих и исходящих связей">{incoming} вх · {outgoing} исх</span>
        </div>
        {info?.description ? <p className="inspector-note">{info.description}</p> : null}
      </div>

      {last?.error ? (
        <div className="inspector-section">
          <span className="eyebrow">Ошибка</span>
          <div className="error" role="alert">
            <CircleAlert size={15} aria-hidden="true" />
            <span>{runErrorMessage(last.error)}</span>
          </div>
        </div>
      ) : null}

      <div className="inspector-section">
        <span className="eyebrow">Запуски</span>
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
          <p className="inspector-empty">Узел ещё не выполнялся.</p>
        )}
        <Payload title="Что вернул узел" value={last?.update} />
      </div>
    </div>
  );
}
