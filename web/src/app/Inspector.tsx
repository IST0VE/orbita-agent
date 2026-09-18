/**
 * Правая колонка: состояние прогона и его итоги.
 *
 * Сверху — то, что верно всегда (связь, режим, что происходит сейчас), ниже —
 * то, что кладёт в состояние сам конвейер: итог, стоимость, публикация.
 * Порядок между ними задан манифестом, а не этим файлом.
 *
 * Внизу карточка проекта: какой конвейер открыт и когда он последний раз
 * что-то делал. Это единственное место, где видно время, поэтому оно здесь,
 * а не в шапке.
 */

import { SurfaceRenderer } from "../engine/surfaces/SurfaceRenderer";
import type { SafeWidgetContext, UiManifest, WidgetAction } from "../engine/manifest/types";
import { RUN_LABELS, RUN_NOTES, RUN_TONES } from "../engine/runtime/labels";
import type { RuntimeSnapshot } from "../engine/runtime/types";
import { StatusDot } from "../ui";

const timeFormat = new Intl.DateTimeFormat("ru-RU", {
  hour: "2-digit",
  minute: "2-digit",
});

/** Когда в прогоне последний раз что-то происходило. */
function lastUpdate(runtime: RuntimeSnapshot): string {
  const event = runtime.events[runtime.events.length - 1];
  if (!event) return "Событий ещё не было";
  const stamp = new Date(event.timestamp);
  if (Number.isNaN(stamp.getTime())) return "—";
  const today = new Date();
  const sameDay = stamp.toDateString() === today.toDateString();
  return sameDay ? `Сегодня, ${timeFormat.format(stamp)}` : stamp.toLocaleString("ru-RU");
}

export function Inspector({
  manifest,
  runtime,
  context,
  inputs,
  onInput,
  onAction,
  surfaceKey,
  projectTitle,
  threadId,
}: {
  manifest: UiManifest;
  runtime: RuntimeSnapshot;
  context: SafeWidgetContext;
  inputs: Record<string, unknown>;
  onInput: (id: string, value: unknown) => void;
  onAction: (action: WidgetAction) => void;
  surfaceKey: string;
  projectTitle: string;
  threadId: string | null;
}) {
  return (
    <aside className="inspector" aria-label="Состояние и итоги">
      <div className="inspector-card">
        <span className="eyebrow">Состояние</span>
        <div className="status-headline">
          <StatusDot tone={RUN_TONES[runtime.runStatus]} />
          {RUN_LABELS[runtime.runStatus]}
        </div>
        <p className="status-note">{RUN_NOTES[runtime.runStatus]}</p>
      </div>

      <SurfaceRenderer
        key={surfaceKey}
        surface="right"
        manifest={manifest}
        runtime={runtime}
        context={context}
        inputs={inputs}
        onInput={onInput}
        onAction={onAction}
      />

      <div className="inspector-card">
        <span className="eyebrow">Текущий проект</span>
        <dl className="key-value">
          <div>
            <dt>Конвейер</dt>
            <dd>{projectTitle}</dd>
          </div>
          <div>
            <dt>Режим</dt>
            <dd>{RUN_LABELS[runtime.runStatus]}</dd>
          </div>
          <div>
            <dt>Тред</dt>
            <dd className="mono">{threadId ? `${threadId.slice(0, 8)}…` : "новый"}</dd>
          </div>
          <div>
            <dt>Последнее обновление</dt>
            <dd>{lastUpdate(runtime)}</dd>
          </div>
        </dl>
      </div>
    </aside>
  );
}
