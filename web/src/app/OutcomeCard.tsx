/**
 * Оценка результата чата: принят ли он и сколько времени ушло на проверку.
 *
 * Стоит в конце ленты чата, под результатами: оценивают то, что только что
 * прочитали. Появляется, когда в чате был прогон и он сейчас не идёт, и
 * остаётся, когда оценка уже есть, — её можно поправить, например когда
 * правка заняла больше, чем казалось сразу.
 *
 * Без базы оценки некуда положить, и сервер отвечает 503: карточка тогда не
 * показывается вовсе. Пилот без базы не меряют, а кнопка, которая всегда
 * отвечает отказом, только учит её не нажимать.
 */

import { useEffect, useState } from "react";

import { loadOutcome, saveOutcome, type OutcomeDoc } from "../api";
import { Check } from "../ui/icons";

/** Причина нужна там, где результат не принят как есть: по ней ищут, что чинить. */
const NEEDS_REASON = new Set(["edited", "reworked", "rejected"]);

export function OutcomeCard({ threadId, finished }: { threadId: string; finished: boolean }) {
  const [doc, setDoc] = useState<OutcomeDoc | null>(null);
  const [hidden, setHidden] = useState(false);
  const [editing, setEditing] = useState(false);
  const [verdict, setVerdict] = useState("");
  const [minutes, setMinutes] = useState("");
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    setDoc(null);
    setHidden(false);
    setEditing(false);
    setError("");
    loadOutcome(threadId)
      .then((found) => {
        if (!alive) return;
        setDoc(found);
        setVerdict(found.outcome?.verdict ?? "");
        setMinutes(found.outcome?.minutes == null ? "" : String(found.outcome.minutes));
        setReason(found.outcome?.reason ?? "");
      })
      .catch(() => {
        // Нет базы, нет такого чата или сервер старее этой формы — оценки нет.
        if (alive) setHidden(true);
      });
    return () => {
      alive = false;
    };
  }, [threadId]);

  if (hidden || !doc) return null;
  const saved = doc.outcome;
  if (!saved && !finished) return null;

  if (saved && !editing) {
    return (
      <section className="outcome-card" aria-label="Оценка результата">
        <div className="outcome-saved">
          <Check size={14} aria-hidden="true" />
          <span>
            Оценка: {saved.verdict_title}
            {saved.minutes != null ? ` · ${saved.minutes} мин на проверку и правку` : ""}
          </span>
          <button type="button" className="btn-ghost btn-sm" onClick={() => setEditing(true)}>
            Изменить
          </button>
        </div>
      </section>
    );
  }

  const parsedMinutes = minutes.trim() === "" ? null : Number(minutes);
  const minutesOk = parsedMinutes === null || (Number.isInteger(parsedMinutes) && parsedMinutes >= 0 && parsedMinutes <= 10000);

  const submit = () => {
    if (!verdict || !minutesOk) return;
    setSaving(true);
    setError("");
    saveOutcome(threadId, { verdict, minutes: parsedMinutes, reason: reason.trim() })
      .then((found) => {
        setDoc(found);
        setEditing(false);
      })
      .catch((reasonError: Error) => setError(reasonError.message))
      .finally(() => setSaving(false));
  };

  return (
    <section className="outcome-card" aria-label="Оценка результата">
      <h2 className="eyebrow sidebar-section-title">Оценка результата</h2>
      <p className="hint">
        Принят ли результат командой и сколько времени ушло на проверку и правку. Нужна, чтобы
        сравнить работу с Orbita и без неё.
      </p>
      <div className="outcome-verdicts" role="group" aria-label="Вердикт">
        {Object.entries(doc.verdicts).map(([id, title]) => (
          <button
            type="button"
            key={id}
            className="outcome-verdict"
            aria-pressed={verdict === id}
            onClick={() => setVerdict(id)}
          >
            {title}
          </button>
        ))}
      </div>
      <label className="outcome-field">
        <span>Минут на проверку и правку</span>
        <input
          type="number"
          min={0}
          max={10000}
          step={1}
          inputMode="numeric"
          value={minutes}
          placeholder="например, 25"
          onChange={(event) => setMinutes(event.target.value)}
          aria-invalid={!minutesOk}
        />
      </label>
      {NEEDS_REASON.has(verdict) ? (
        <label className="outcome-field">
          <span>Что пришлось поправить или почему не подошло</span>
          <textarea
            rows={3}
            maxLength={2000}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </label>
      ) : null}
      {!minutesOk ? <span className="error">Минуты — целое число от 0 до 10000.</span> : null}
      {error ? <span className="error" role="alert">{error}</span> : null}
      <div className="outcome-actions">
        {saved ? (
          <button type="button" className="btn-ghost btn-sm" onClick={() => setEditing(false)} disabled={saving}>
            Отмена
          </button>
        ) : null}
        <button type="button" className="btn-primary btn-sm" onClick={submit} disabled={!verdict || !minutesOk || saving}>
          {saving ? "Сохраняю…" : "Сохранить оценку"}
        </button>
      </div>
    </section>
  );
}
