/**
 * Средняя колонка: схема графа или открытый документ.
 *
 * Документ занимает то же место, что и схема, а не отдельное окно: читают его
 * ради того же прогона, и возвращаться к схеме приходится постоянно. Кнопка
 * возврата стоит там же, где стояли вкладки вида, — чтобы её не искать.
 */

import { GraphCanvas } from "../engine/canvas/GraphCanvas";
import type { UiBundle } from "../engine/api/client";
import type { UiManifest } from "../engine/manifest/types";
import type { RuntimeSnapshot } from "../engine/runtime/types";
import { SafeMarkdown } from "../engine/security/safeMarkdown";
import { ArrowLeft, CircleAlert, Network } from "../ui/icons";
import { EmptyState } from "../ui";

export type OpenDocument = { title: string; text: string };

export function Workspace({
  bundle,
  manifest,
  runtime,
  selected,
  onSelect,
  document,
  onCloseDocument,
  error,
  canvasKey,
}: {
  bundle: UiBundle | null;
  manifest: UiManifest;
  runtime: RuntimeSnapshot;
  selected: string | null;
  onSelect: (nodeId: string | null) => void;
  document: OpenDocument | null;
  onCloseDocument: () => void;
  error: string | null;
  canvasKey: string;
}) {
  return (
    <>
      {document ? (
        <section className="workspace" data-view="document">
          <div className="engine-canvas">
            <div className="canvas-tools">
              <button className="btn-ghost btn-sm" onClick={onCloseDocument}>
                <ArrowLeft size={15} aria-hidden="true" />
                К схеме
              </button>
              <h2 className="truncate" style={{ fontSize: "var(--text-md)" }}>{document.title}</h2>
            </div>
            <div className="engine-document">
              <SafeMarkdown value={document.text} />
            </div>
          </div>
        </section>
      ) : null}
      {/* Keep graph positions and its camera while a result document is open. */}
      <section className="workspace" data-view="graph" hidden={!!document}
        style={document ? { display: "none" } : undefined}>
        {bundle ? (
          <GraphCanvas
            key={canvasKey}
            topology={bundle.topology}
            manifest={manifest}
            runtime={runtime}
            selected={selected}
            onSelect={onSelect}
          />
        ) : (
          <div className="engine-canvas">
            <div className="canvas-tools" />
            {error ? (
              <EmptyState
                icon={CircleAlert}
                title="Схема не загрузилась"
                hint="Проверьте подключение к серверу агента и обновите страницу."
              />
            ) : (
              <EmptyState icon={Network} title="Загрузка схемы…" hint="Читаем топологию графа у сервера." />
            )}
          </div>
        )}
      </section>
    </>
  );
}
