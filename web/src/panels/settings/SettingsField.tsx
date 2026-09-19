/**
 * Одна настройка: имя по-человечески, значение и комментарий к нему.
 *
 * Тип поля приходит с сервера (`kind`), поэтому галка, список и число
 * рисуются здесь, а не перечисляются в коде по именам переменных. Добавили
 * переменную в проект — она появилась в форме сама, с тем же оформлением.
 *
 * Секрет не показывается никогда. С сервера приезжает маска, и в браузере
 * настоящего значения нет вообще: оператор либо не трогает поле — тогда оно
 * не уходит на сервер, — либо вводит новое, и тогда уезжает именно введённое.
 */

import { useState } from "react";

import type { Setting } from "../../api";
import { Check, KeyRound, X } from "../../ui/icons";
import { settingLabel } from "./groups";

/** Как значение выглядело бы в файле после сохранения: пара знаков и звёздочки. */
function maskPreview(value: string): string {
  if (!value) return "";
  const head = value.slice(0, 4);
  return head + "*".repeat(Math.max(4, Math.min(value.length - head.length, 12)));
}

function Label({ field, touched }: { field: Setting; touched: boolean }) {
  const label = settingLabel(field);
  return (
    <span className="set-label">
      <span className={`set-name ${touched ? "changed" : ""}`.trim()}>
        {field.secret ? (
          <span className="lock" title="секрет: в браузер приезжает маска">
            <KeyRound size={13} aria-hidden="true" />
          </span>
        ) : null}
        {label}
        {touched ? " *" : ""}
      </span>
      {/* Имя в окружении — второй строкой: по нему переменную ищут в файле,
          в документации и в сообщении об ошибке, но не по нему её узнают. */}
      {label === field.name ? null : <code className="set-env">ENV: {field.name}</code>}
    </span>
  );
}

/**
 * Секрет: маска, кнопка «Изменить» и ввод нового значения.
 *
 * Отдельным компонентом, потому что у него своё состояние показа и свои две
 * кнопки — и потому что перепутать его с обычным полем нельзя ни разу.
 */
function SecretField({
  field,
  draft,
  onChange,
}: {
  field: Setting;
  draft: string | undefined;
  onChange: (name: string, value: string | undefined) => void;
}) {
  const [revealed, setRevealed] = useState(false);
  if (draft === undefined) {
    return (
      <span className="set-secret">
        <span className={field.filled ? "mono" : "hint"}>
          {field.filled ? field.value : "не задано"}
        </span>
        <button className="btn-sm" onClick={() => onChange(field.name, "")}>Изменить</button>
      </span>
    );
  }
  return (
    <span className="set-secret">
      <input
        type={revealed ? "text" : "password"}
        value={draft}
        autoFocus
        aria-label={`${settingLabel(field)}: новое значение`}
        placeholder="новое значение"
        onChange={(event) => onChange(field.name, event.target.value)}
      />
      <button className="btn-ghost btn-sm" onClick={() => setRevealed((value) => !value)}>
        {revealed ? "Скрыть" : "Показать"}
      </button>
      <button className="btn-ghost btn-sm" onClick={() => onChange(field.name, undefined)}>Отмена</button>
      {draft ? <span className="swatch">{maskPreview(draft)}</span> : null}
    </span>
  );
}

export function SettingsField({
  field,
  draft,
  onChange,
  note,
  onNote,
}: {
  field: Setting;
  /** Введённое оператором значение; undefined — поле не трогали. */
  draft: string | undefined;
  onChange: (name: string, value: string | undefined) => void;
  note: string | undefined;
  onNote: (name: string, value: string | undefined) => void;
}) {
  const touched = draft !== undefined;
  const value = touched ? draft : field.value;
  const set = (next: string) => onChange(field.name, next);

  const control = () => {
    if (!field.editable) {
      return (
        <span className="swatch" title="поле отсутствует в .env.example">
          {field.secret ? (field.filled ? field.value : "не задано") : field.value || "не задано"}
          {" · только вручную"}
        </span>
      );
    }
    if (field.kind === "bool") {
      const on = ["1", "true", "yes", "on", "y"].includes(value.trim().toLowerCase());
      return (
        <span className="set-control">
          <button
            className={`toggle ${on ? "on" : ""}`.trim()}
            aria-pressed={on}
            onClick={() => set(on ? "0" : "1")}
          >
            {on ? <Check size={14} aria-hidden="true" /> : <X size={14} aria-hidden="true" />}
            {on ? "включено" : "выключено"}
          </button>
          <span className="swatch">по умолчанию {field.default || "не задано"}</span>
        </span>
      );
    }
    if (field.kind === "enum") {
      return (
        <select
          value={value}
          aria-label={settingLabel(field)}
          onChange={(event) => set(event.target.value)}
        >
          <option value="">— по умолчанию ({field.default || "пусто"}) —</option>
          {(field.choices ?? []).map((choice) => (
            <option key={choice} value={choice}>{choice}</option>
          ))}
        </select>
      );
    }
    if (field.secret) return <SecretField field={field} draft={draft} onChange={onChange} />;
    // Границы объявлены в схеме настроек и показываются здесь: иначе про них
    // узнают из отказа сервера уже после сохранения.
    const bounds = [
      field.minimum !== undefined ? `от ${field.minimum}` : "",
      field.maximum !== undefined ? `до ${field.maximum}` : "",
    ].filter(Boolean).join(" ");
    return (
      <span className="set-control">
        <input
          type="text"
          inputMode={field.kind === "int" || field.kind === "float" ? "decimal" : "text"}
          value={value}
          aria-label={settingLabel(field)}
          placeholder={field.default ? `по умолчанию ${field.default}` : "не задано"}
          aria-describedby={bounds ? `${field.name}-bounds` : undefined}
          onChange={(event) => set(event.target.value)}
        />
        {bounds ? <span className="hint" id={`${field.name}-bounds`}>{bounds}</span> : null}
      </span>
    );
  };

  return (
    <div className="set-row">
      <div className="set-field">
        <Label field={field} touched={touched} />
        {control()}
      </div>
      {field.description ? <div className="set-desc">{field.description}</div> : null}
      <Comment field={field} draft={note} onChange={onNote} />
    </div>
  );
}

/** Комментарий над переменной в самом `.env`: файл читают и глазами тоже. */
function Comment({
  field,
  draft,
  onChange,
}: {
  field: Setting;
  draft: string | undefined;
  onChange: (name: string, value: string | undefined) => void;
}) {
  if (!field.editable) return null;
  if (draft !== undefined) {
    return (
      <div className="set-comment">
        <textarea
          value={draft}
          autoFocus
          rows={2}
          aria-label={`Комментарий к ${field.name}`}
          placeholder="зачем эта переменная здесь"
          onChange={(event) => onChange(field.name, event.target.value)}
        />
        <button className="btn-ghost btn-sm" onClick={() => onChange(field.name, undefined)}>Отмена</button>
      </div>
    );
  }
  return (
    <div className="set-comment">
      {field.comment ? <span className="set-note"># {field.comment}</span> : null}
      <button className="btn-ghost btn-sm" onClick={() => onChange(field.name, field.comment)}>
        {field.comment ? "Править комментарий" : "Добавить комментарий"}
      </button>
    </div>
  );
}
