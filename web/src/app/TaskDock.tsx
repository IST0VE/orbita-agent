/**
 * Нижняя полоса: задача и ход её выполнения.
 *
 * Здесь стоит единственное поле ввода всего приложения, поэтому полоса не
 * уезжает за край ни при какой ширине окна и не сворачивается. Сообщения
 * прогона идут над полем — их порядок задан манифестом (`surfaces.main`),
 * а не этим файлом.
 */

import { SurfaceRenderer } from "../engine/surfaces/SurfaceRenderer";
import type { SafeWidgetContext, UiManifest, WidgetAction } from "../engine/manifest/types";
import type { RuntimeSnapshot } from "../engine/runtime/types";
import { Sparkles, Square } from "../ui/icons";

export function TaskDock({
  manifest,
  runtime,
  context,
  inputs,
  onInput,
  onAction,
  surfaceKey,
  running,
  canStop,
  project,
}: {
  manifest: UiManifest;
  runtime: RuntimeSnapshot;
  context: SafeWidgetContext;
  inputs: Record<string, unknown>;
  onInput: (id: string, value: unknown) => void;
  onAction: (action: WidgetAction) => void;
  surfaceKey: string;
  running: boolean;
  canStop: boolean;
  project: string;
}) {
  return (
    <section className="app-dock" aria-label="Задача и ход выполнения">
      <div className="dock-head">
        <h2>
          <Sparkles size={16} aria-hidden="true" />
          Задача
        </h2>
        <div className="dock-head-right">
          <span className="badge badge-neutral">{project}</span>
          {running && canStop ? (
            <button className="btn-danger" onClick={() => onAction({ kind: "run.stop" })}>
              <Square size={15} aria-hidden="true" />
              Остановить
            </button>
          ) : null}
        </div>
      </div>
      <div className="dock-body">
        <SurfaceRenderer
          key={surfaceKey}
          surface="main"
          manifest={manifest}
          runtime={runtime}
          context={context}
          inputs={inputs}
          onInput={onInput}
          onAction={onAction}
        />
      </div>
    </section>
  );
}
