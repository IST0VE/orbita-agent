/**
 * Правая колонка: открытый чат и подробности выбранного.
 *
 * Две вкладки. «Чат» — файлы, загруженные в этот чат, параметры прогона и то,
 * что прогон создал (`ChatMaterials`). Раньше это была левая колонка с общими
 * папками задач; её место заняли сами чаты, а файлы встали сюда, рядом с
 * результатами того же чата.
 *
 * «Подробности» — то, чем колонка была раньше, и содержание у неё зависит от
 * выбора: узел — карточка узла, прогон — его показатели, ничего — короткая
 * справка о сценарии. Выбор узла сам переключает на эту вкладку: подробности
 * приходят к тому, кто их запросил.
 *
 * Своей кнопки скрытия у колонки нет: колонки прячут переключатели в строке
 * контекста, и вторая такая же кнопка в шапке колонки только дублировала их.
 */

import type { PointerEvent, ReactNode } from "react";

import type { GraphTopology } from "../../lib/graph";
import { surfaceItems } from "../../engine/surfaces/SurfaceRenderer";
import type { SafeWidgetContext, UiManifest, WidgetAction } from "../../engine/manifest/types";
import type { RuntimeSnapshot } from "../../engine/runtime/types";
import { isInspectorWidget } from "../result";
import { NodeInspector } from "./NodeInspector";
import { RunInspector } from "./RunInspector";
import { ScenarioInspector } from "./ScenarioInspector";

export type RightTab = "chat" | "details";

export function Inspector({
  tab,
  onTab,
  materials,
  fileCount,
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
  startResize,
  resetWidth,
  drawer,
}: {
  tab: RightTab;
  onTab: (tab: RightTab) => void;
  /** Вкладка «Чат»: файлы, параметры и результаты открытого чата. */
  materials: ReactNode;
  /** Сколько файлов в чате; null — ещё не известно. */
  fileCount: number | null;
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
  startResize: (event: PointerEvent<HTMLDivElement>) => void;
  resetWidth: () => void;
  /** Колонка выдвинута поверх рабочей области: ширину задаёт вёрстка. */
  drawer: boolean;
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
  const details = tab === "details";

  return (
    <aside className="inspector" aria-label={details ? `Подробности: ${title.toLowerCase()}` : "Открытый чат"}>
      <div className="inspector-head">
        <div className="inspector-tabs" role="tablist" aria-label="Правая колонка">
          <button
            type="button"
            role="tab"
            aria-selected={!details}
            className={`inspector-tab${details ? "" : " active"}`}
            onClick={() => onTab("chat")}
          >
            Чат
            {fileCount ? <span className="inspector-tab-count">{fileCount}</span> : null}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={details}
            className={`inspector-tab${details ? " active" : ""}`}
            onClick={() => onTab("details")}
          >
            {title}
          </button>
        </div>
        {details && node ? (
          <button
            className="btn-ghost btn-sm inspector-clear"
            title="Снять выбор узла и вернуться к показателям прогона"
            onClick={onClearNode}
          >
            К прогону
          </button>
        ) : null}
      </div>

      {drawer ? null : (
        <div
          className="col-resizer"
          role="separator"
          aria-orientation="vertical"
          aria-label="Ширина правой колонки"
          title="Потяните, чтобы изменить ширину. Двойной щелчок — сбросить."
          onPointerDown={startResize}
          onDoubleClick={resetWidth}
        />
      )}

      {details ? (
        <div className="inspector-scroll">
          {node && topology ? (
            <NodeInspector nodeId={node} topology={topology} manifest={manifest} runtime={runtime} />
          ) : started ? (
            <RunInspector runtime={runtime} threadId={threadId} items={measures} />
          ) : (
            <ScenarioInspector manifest={manifest} inputs={inputs} title={scenarioTitle} />
          )}
        </div>
      ) : (
        materials
      )}
    </aside>
  );
}
