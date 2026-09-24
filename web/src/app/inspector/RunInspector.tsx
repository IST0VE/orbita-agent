/**
 * Показатели прогона: сколько шло, во что обошлось и уехало ли куда-нибудь.
 *
 * Стоимость и публикация раньше стояли постоянными карточками — двумя из
 * четырёх, которые правая колонка показывала всегда, чаще всего пустыми.
 * Теперь они здесь, под своими подписями, и пустое состояние занимает одну
 * строку: «—» и «Не опубликовано». Развёрнутый вид появляется тогда, когда
 * появляются данные.
 */

import { Fragment, type ReactNode } from "react";

import type { SurfaceItem } from "../../engine/surfaces/SurfaceRenderer";
import { RUN_LABELS, RUN_NOTES, RUN_TONES } from "../../engine/runtime/labels";
import type { RuntimeSnapshot } from "../../engine/runtime/types";
import { runErrorMessage } from "../../engine/api/langgraphAdapter";
import { CircleAlert } from "../../ui/icons";
import { StatusDot } from "../../ui";

const timeFormat = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" });

/** Когда в прогоне последний раз что-то происходило. */
function lastUpdate(runtime: RuntimeSnapshot): string {
  const event = runtime.events[runtime.events.length - 1];
  if (!event) return "—";
  const stamp = new Date(event.timestamp);
  if (Number.isNaN(stamp.getTime())) return "—";
  const today = new Date();
  return stamp.toDateString() === today.toDateString()
    ? `сегодня, ${timeFormat.format(stamp)}`
    : stamp.toLocaleString("ru-RU");
}

/**
 * Сколько шёл прогон.
 *
 * Считается по событиям текущего прогона, а не по сумме длительностей узлов:
 * узлы идут и параллельно, и с паузами на решение оператора, и сумма их
 * времён отвечала бы на другой вопрос.
 */
function duration(runtime: RuntimeSnapshot): string {
  const events = runtime.runId
    ? runtime.events.filter((event) => event.runId === runtime.runId)
    : runtime.events;
  const first = events[0];
  const last = events[events.length - 1];
  if (!first || !last) return "—";
  const from = new Date(first.timestamp).getTime();
  const to = new Date(last.timestamp).getTime();
  if (!Number.isFinite(from) || !Number.isFinite(to) || to < from) return "—";
  const seconds = Math.round((to - from) / 1000);
  if (seconds < 60) return `${seconds} с`;
  return `${Math.floor(seconds / 60)} мин ${String(seconds % 60).padStart(2, "0")} с`;
}

/**
 * Подпись показателя и либо его виджет, либо прочерк вместо него.
 *
 * Виджет с заголовком из манифеста приносит его сам: своя подпись поверх
 * давала «Стоимость» дважды подряд.
 */
function Measure({ title, item, empty }: { title: string; item?: SurfaceItem; empty: string }) {
  const shown = item && !item.empty ? item : null;
  return (
    <div className="inspector-section">
      {shown?.title ? null : <span className="eyebrow">{title}</span>}
      {shown ? (shown.node as ReactNode) : <span className="inspector-empty">{empty}</span>}
    </div>
  );
}

export function RunInspector({
  runtime,
  threadId,
  items,
}: {
  runtime: RuntimeSnapshot;
  threadId: string | null;
  /** Показатели с правой поверхности: стоимость и публикация. */
  items: SurfaceItem[];
}) {
  const cost = items.find((item) => item.widget === "cost-summary");
  const publication = items.find((item) => item.widget === "publication");
  const others = items.filter((item) => item !== cost && item !== publication && !item.empty);
  const failed = runtime.executionOrder
    .map((id) => runtime.executions[id])
    .filter((execution) => execution?.status === "failed");
  const passed = runtime.executionOrder
    .map((id) => runtime.executions[id])
    .filter((execution) => execution?.status === "completed").length;

  return (
    <div className="inspector-body">
      <div className="inspector-section">
        <div className="status-headline">
          <StatusDot tone={RUN_TONES[runtime.runStatus]} />
          {RUN_LABELS[runtime.runStatus]}
        </div>
        <p className="inspector-note">{RUN_NOTES[runtime.runStatus]}</p>
      </div>

      <div className="inspector-section">
        <dl className="key-value">
          <div><dt>Время выполнения</dt><dd>{duration(runtime)}</dd></div>
          <div><dt>Этапов пройдено</dt><dd>{passed || "—"}</dd></div>
          <div><dt>Обновлено</dt><dd>{lastUpdate(runtime)}</dd></div>
          <div>
            <dt>Тред</dt>
            <dd className="mono" title={threadId ?? "новый тред"}>
              {threadId ? `${threadId.slice(0, 8)}…` : "новый"}
            </dd>
          </div>
        </dl>
      </div>

      {failed.length ? (
        <div className="inspector-section">
          <span className="eyebrow">Отказы</span>
          {failed.map((execution) => (
            <div className="error" role="alert" key={execution.executionId}>
              <CircleAlert size={15} aria-hidden="true" />
              <span>
                {execution.nodeId}
                {execution.error ? `: ${runErrorMessage(execution.error)}` : ""}
              </span>
            </div>
          ))}
        </div>
      ) : null}

      <Measure title="Стоимость" item={cost} empty="—" />
      <Measure title="Публикация" item={publication} empty="Не опубликовано" />

      {others.map((item) => (
        <div className="inspector-section" key={item.key}>
          <Fragment>{item.node as ReactNode}</Fragment>
        </div>
      ))}
    </div>
  );
}
