/**
 * Главная рабочая область: схема, результат или документ.
 *
 * Три вида на одном месте, а не три места на одном экране. Раньше схема
 * делила окно с лентой прогона, полем задачи и правой колонкой из четырёх
 * карточек — и занимала меньше половины того пространства, ради которого
 * её открывали.
 *
 * Полотно остаётся смонтированным при переходе к другому виду: у него есть
 * камера и ручные позиции узлов, и терять их при каждом взгляде на результат
 * значит заставлять расставлять граф заново.
 */

import { Fragment, type ReactNode } from "react";

import { GraphWorkspace } from "../engine/canvas/GraphWorkspace";
import type { GraphView } from "../engine/canvas/useGraphView";
import type { UiBundle } from "../engine/api/client";
import { surfaceItems } from "../engine/surfaces/SurfaceRenderer";
import type { SafeWidgetContext, UiManifest, WidgetAction } from "../engine/manifest/types";
import type { RuntimeSnapshot } from "../engine/runtime/types";
import { SafeMarkdown } from "../engine/security/safeMarkdown";
import { CircleAlert, LayoutGrid, Network } from "../ui/icons";
import { EmptyState } from "../ui";
import { isInspectorWidget } from "./result";
import type { WorkspaceView } from "./sections";
import { WorkspaceHeader } from "./WorkspaceHeader";

export type OpenDocument = { title: string; text: string };

export function Workspace({
  bundle,
  manifest,
  runtime,
  context,
  inputs,
  onInput,
  onAction,
  selected,
  onSelect,
  view,
  onView,
  graph,
  document,
  onCloseDocument,
  error,
}: {
  bundle: UiBundle | null;
  manifest: UiManifest;
  runtime: RuntimeSnapshot;
  context: SafeWidgetContext;
  inputs: Record<string, unknown>;
  onInput: (id: string, value: unknown) => void;
  onAction: (action: WidgetAction) => void;
  selected: string | null;
  onSelect: (nodeId: string | null) => void;
  view: WorkspaceView;
  onView: (view: WorkspaceView) => void;
  graph: GraphView;
  document: OpenDocument | null;
  onCloseDocument: () => void;
  error: string | null;
}) {
  // Итог прогона занимает главную область, а не колонку в триста пикселей:
  // таблица аномалий и заключение — это то, ради чего прогон запускали.
  const results = surfaceItems({ surface: "right", manifest, runtime, context, inputs, onInput, onAction })
    .filter((item) => !isInspectorWidget(item.widget) && !item.empty);

  return (
    <section className="workspace" data-view={view}>
      <WorkspaceHeader
        view={view}
        onView={onView}
        documentOpen={Boolean(document)}
        documentTitle={document?.title ?? "Документ"}
        onCloseDocument={onCloseDocument}
        resultReady={results.length > 0}
        graph={graph}
      />

      <div className="workspace-body">
        <div className="workspace-pane" hidden={view !== "graph"}>
          {bundle ? (
            <GraphWorkspace
              topology={bundle.topology}
              manifest={manifest}
              runtime={runtime}
              selected={selected}
              onSelect={onSelect}
              view={graph}
            />
          ) : error ? (
            <EmptyState
              icon={CircleAlert}
              title="Схема не загрузилась"
              hint="Проверьте подключение к серверу агента и обновите страницу."
            />
          ) : (
            <EmptyState icon={Network} title="Загрузка схемы…" hint="Читаем топологию графа у сервера." />
          )}
        </div>

        {view === "result" ? (
          <div className="workspace-pane result-view">
            {results.length ? (
              results.map((item) => <Fragment key={item.key}>{item.node as ReactNode}</Fragment>)
            ) : (
              <EmptyState
                icon={LayoutGrid}
                title="Результата пока нет"
                hint="Итог, документы и показатели появятся здесь после прогона."
              />
            )}
          </div>
        ) : null}

        {view === "document" && document ? (
          <div className="workspace-pane document-view">
            <h2 className="document-title">{document.title}</h2>
            <div className="engine-document">
              <SafeMarkdown value={document.text} />
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
