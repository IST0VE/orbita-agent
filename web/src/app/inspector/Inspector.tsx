/**
 * Правая колонка: подробности выбранного, а не постоянный набор карточек.
 *
 * Раньше здесь всегда стояли четыре блока — состояние, стоимость, публикация
 * и «текущий проект», — и три из них чаще всего были пустыми. Пустая карточка
 * не сообщает «данных нет», она сообщает «здесь что-то сломано».
 *
 * Теперь содержание зависит от того, что выбрано: узел — карточка узла,
 * прогон — его показатели, ничего — короткая справка о сценарии. Выбор
 * узла заодно и открывает колонку: подробности приходят к тому, кто их
 * запросил, а не ждут на экране весь день.
 */

import type { GraphTopology } from "../../lib/graph";
import { surfaceItems } from "../../engine/surfaces/SurfaceRenderer";
import type { SafeWidgetContext, UiManifest, WidgetAction } from "../../engine/manifest/types";
import type { RuntimeSnapshot } from "../../engine/runtime/types";
import { PanelRightClose } from "../../ui/icons";
import { isInspectorWidget } from "../result";
import { NodeInspector } from "./NodeInspector";
import { RunInspector } from "./RunInspector";
import { ScenarioInspector } from "./ScenarioInspector";

export function Inspector({
  manifest,
  runtime,
  context,
  inputs,
  onInput,
  onAction,
  topology,
  selectedNode,
  onClearNode,
  scenarioTitle,
  threadId,
  onClose,
}: {
  manifest: UiManifest;
  runtime: RuntimeSnapshot;
  context: SafeWidgetContext;
  inputs: Record<string, unknown>;
  onInput: (id: string, value: unknown) => void;
  onAction: (action: WidgetAction) => void;
  topology: GraphTopology | null;
  selectedNode: string | null;
  onClearNode: () => void;
  scenarioTitle: string;
  threadId: string | null;
  onClose: () => void;
}) {
  const node = selectedNode && topology?.nodes.some((item) => item.id === selectedNode)
    ? selectedNode
    : null;
  // Прогон начался, если о нём есть хоть одно событие: пустой снимок состояния
  // это ещё не прогон, и показывать по нему нечего.
  const started = runtime.events.length > 0 || runtime.runStatus !== "idle";
  const measures = surfaceItems({ surface: "right", manifest, runtime, context, inputs, onInput, onAction })
    .filter((item) => isInspectorWidget(item.widget));

  const title = node ? "Узел" : started ? "Прогон" : "Сценарий";

  return (
    <aside className="inspector" aria-label={`Инспектор: ${title.toLowerCase()}`}>
      <div className="inspector-head">
        <span className="eyebrow">{title}</span>
        {node ? (
          <button
            className="btn-ghost btn-sm inspector-clear"
            title="Снять выбор узла и вернуться к показателям прогона"
            onClick={onClearNode}
          >
            К прогону
          </button>
        ) : null}
        <button
          className="btn-ghost btn-icon btn-sm inspector-close"
          aria-label="Скрыть правую колонку"
          title="Скрыть правую колонку"
          onClick={onClose}
        >
          <PanelRightClose size={16} aria-hidden="true" />
        </button>
      </div>

      <div className="inspector-scroll">
        {node && topology ? (
          <NodeInspector nodeId={node} topology={topology} manifest={manifest} runtime={runtime} />
        ) : started ? (
          <RunInspector runtime={runtime} threadId={threadId} items={measures} />
        ) : (
          <ScenarioInspector manifest={manifest} inputs={inputs} title={scenarioTitle} />
        )}
      </div>
    </aside>
  );
}
