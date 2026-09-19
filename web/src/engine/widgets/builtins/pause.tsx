/**
 * Пауза оператора: карточка остановки и список того, что он уже дописал.
 *
 * От подтверждения (`approval.tsx`) отличается вопросом, на который отвечает.
 * Там оператор решает судьбу готового результата — согласовать или отклонить.
 * Здесь результата ещё нет: конвейер остановлен на границе шага, и главное на
 * экране — не кнопка, а поле, в которое дописывают недостающее. Поэтому поле
 * стоит первым и получает фокус, а «продолжить» — это просто «дальше».
 */
import { useState } from "react";

import { Check, ClipboardList, Pause, Square } from "../../../ui/icons";
import type { WidgetProps } from "../../manifest/types";
import { text } from "./shared";

/** Что приезжает в карточку из `agent/pause.py`. */
type PausePayload = {
  title?: string;
  stage?: string;
  stage_title?: string;
  done?: string[];
  pending?: string[];
  notes?: string[];
  hint?: string;
};

type Note = { stage?: string; text?: string; at?: string };

const MAX_NOTE = 4000;

function list(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

export function PauseWidget({ value, binding, readonly, onAction }: WidgetProps) {
  const payload = (value && typeof value === "object" ? value : {}) as PausePayload;
  const [note, setNote] = useState("");
  const interruptId = text(binding.options?.interruptId);
  const ruleId = text(binding.options?.ruleId);
  const done = list(payload.done);
  const pending = list(payload.pending);
  const known = list(payload.notes);
  const decide = (decision: "continue" | "stop") =>
    onAction?.({
      kind: "interrupt.resume",
      interruptId,
      ruleId,
      payload: { decision, ...(note.trim() ? { note: note.trim().slice(0, MAX_NOTE) } : {}) },
    });
  return (
    <div className="approve pause" role="dialog" aria-modal="true" aria-label="Конвейер на паузе">
      <div className="approve-head">
        <Pause size={20} aria-hidden="true" />
        <h4>Конвейер на паузе</h4>
      </div>
      {payload.title ? <b>{text(payload.title)}</b> : null}

      {/* Где встали: что уже написано и что осталось. Решение «дописать или
          остановиться» принимают по этому, а не по названию этапа. */}
      {done.length || pending.length ? (
        <dl className="pause-stages">
          {done.length ? (
            <div>
              <dt>Готово</dt>
              <dd>{done.join(", ")}</dd>
            </div>
          ) : null}
          {pending.length ? (
            <div>
              <dt>Осталось</dt>
              <dd>{pending.join(", ")}</dd>
            </div>
          ) : null}
        </dl>
      ) : null}

      {/* Прежние указания — чтобы одно и то же не просили дважды и чтобы было
          видно, что предыдущее действительно доехало. */}
      {known.length ? (
        <ul className="pause-notes">
          {known.map((item, index) => (
            <li key={index}>{item}</li>
          ))}
        </ul>
      ) : null}

      <label>
        Что добавить
        <textarea
          autoFocus
          rows={4}
          value={note}
          maxLength={MAX_NOTE}
          placeholder="Например: считать только рублёвые заказы; про ретраи писать отдельным разделом"
          onChange={(event) => setNote(event.target.value)}
        />
      </label>
      {payload.hint ? <p className="hint">{text(payload.hint)}</p> : null}

      <div className="approve-decision">
        <button className="btn-yes" disabled={readonly} onClick={() => decide("continue")}>
          <Check size={16} aria-hidden="true" />
          Продолжить
        </button>
        <button className="btn-no" disabled={readonly} onClick={() => decide("stop")}>
          <Square size={16} aria-hidden="true" />
          Остановить конвейер
        </button>
        <p className="hint">
          Остановка не отменяет прогон: готовые документы публикуются, оставшиеся этапы не
          выполняются.
        </p>
      </div>
    </div>
  );
}

/**
 * Указания, дописанные на паузах, — как список, а не как JSON.
 *
 * Показывать их обязательно. Документ этапа читают, держа в голове, о чём
 * просили; без списка единственным следом указания оставался бы текст
 * запроса, которого на экране нет.
 */
export function OperatorNotesWidget({ value }: WidgetProps) {
  const notes = Array.isArray(value) ? (value as Note[]) : [];
  if (!notes.length) return <div className="hint">Указаний не было.</div>;
  return (
    <ul className="operator-notes">
      {notes.map((note, index) => (
        <li key={index}>
          <span className="operator-notes-mark">
            <ClipboardList size={14} aria-hidden="true" />
            {text(note.stage)}
          </span>
          <span>{text(note.text)}</span>
        </li>
      ))}
    </ul>
  );
}
