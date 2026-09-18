/**
 * Настройки: весь `.env` прямо из интерфейса.
 *
 * Поля не перечислены здесь руками — сервер отдаёт их вместе с разделами,
 * описаниями и типами, разобрав `.env.example` и `config.py`. Добавили
 * переменную в проект — она появилась в форме сама.
 *
 * Секреты сюда приезжают маской (`sk-6********`) и в таком виде и живут:
 * настоящего значения в браузере нет вообще. Оператор либо не трогает поле —
 * тогда оно не уходит на сервер и в файле не меняется, — либо вводит новое,
 * и тогда уезжает именно введённое.
 *
 * Кроме значений отсюда правятся две вещи: комментарий над переменной и сам
 * список переменных. Комментарий пишется в `.env` как обычный текст — файл
 * читают глазами, и «зачем это здесь» должно лежать рядом со значением.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  loadSettings,
  saveSettings,
  type Setting,
  type SettingsDoc,
} from "../api";
import { Panel } from "../ui";
import { Modal } from "../Modal";
import { Check, ChevronDown, ChevronRight, Lock, Search, X } from "../ui/icons";

/** Как значение выглядело бы в файле после сохранения: пара знаков и звёздочки. */
function maskPreview(value: string): string {
  if (!value) return "";
  const head = value.slice(0, 4);
  return head + "*".repeat(Math.max(4, Math.min(value.length - head.length, 12)));
}

function Field({
  field,
  draft,
  onChange,
}: {
  field: Setting;
  /** Введённое оператором значение; undefined — поле не трогали. */
  draft: string | undefined;
  onChange: (name: string, value: string | undefined) => void;
}) {
  const [revealed, setRevealed] = useState(false);
  const touched = draft !== undefined;
  const value = touched ? draft : field.value;
  const set = (next: string) => onChange(field.name, next);

  const label = (
    <span className={`set-name ${touched ? "changed" : ""}`} title={field.description}>
      {field.secret ? (
        <span className="lock" title="секрет: в браузер приезжает маска">
          <Lock size={13} aria-hidden="true" />
        </span>
      ) : null}
      {field.name}
      {touched ? " *" : ""}
    </span>
  );

  if (!field.editable) {
    return (
      <div className="set-field">
        {label}
        <span className="swatch" title="поле отсутствует в .env.example">
          {field.secret ? (field.filled ? field.value : "не задано") : field.value || "не задано"}
          {" · только вручную"}
        </span>
      </div>
    );
  }

  // ---- галка ----
  if (field.kind === "bool") {
    const on = ["1", "true", "yes", "on", "y"].includes(value.trim().toLowerCase());
    return (
      <div className="set-field">
        {label}
        <span>
          <button
            className={`toggle ${on ? "on" : ""}`}
            aria-pressed={on}
            onClick={() => set(on ? "0" : "1")}
          >
            {on ? <Check size={14} aria-hidden="true" /> : <X size={14} aria-hidden="true" />}
            {on ? "включено" : "выключено"}
          </button>
          <span className="swatch">
            по умолчанию {field.default || "не задано"}
          </span>
        </span>
      </div>
    );
  }

  // ---- выбор из списка ----
  if (field.kind === "enum") {
    return (
      <div className="set-field">
        {label}
        <select value={value} onChange={(e) => set(e.target.value)}>
          <option value="">— по умолчанию ({field.default || "пусто"}) —</option>
          {(field.choices ?? []).map((choice) => (
            <option key={choice} value={choice}>
              {choice}
            </option>
          ))}
        </select>
      </div>
    );
  }

  // ---- секрет ----
  if (field.secret) {
    return (
      <div className="set-field">
        {label}
        <span className="set-secret">
          {touched ? (
            <>
              <input
                type={revealed ? "text" : "password"}
                value={draft}
                autoFocus
                placeholder="новое значение"
                onChange={(e) => set(e.target.value)}
              />
              <button className="btn-ghost btn-sm" onClick={() => setRevealed((r) => !r)}>
                {revealed ? "Скрыть" : "Показать"}
              </button>
              <button className="btn-ghost btn-sm" onClick={() => onChange(field.name, undefined)}>Отмена</button>
              {draft ? <span className="swatch">{maskPreview(draft)}</span> : null}
            </>
          ) : (
            <>
              <span className={field.filled ? "mono" : "hint"}>
                {field.filled ? field.value : "не задано"}
              </span>
              <button className="btn-sm" onClick={() => set("")}>Изменить</button>
            </>
          )}
        </span>
      </div>
    );
  }

  // ---- строка, целое, дробное ----
  // Границы объявлены в схеме настроек и показываются здесь: иначе про них
  // узнают из отказа сервера уже после сохранения.
  const bounds = [
    field.minimum !== undefined ? `от ${field.minimum}` : "",
    field.maximum !== undefined ? `до ${field.maximum}` : "",
  ].filter(Boolean).join(" ");
  return (
    <div className="set-field">
      {label}
      <input
        type="text"
        inputMode={field.kind === "int" || field.kind === "float" ? "decimal" : "text"}
        value={value}
        placeholder={field.default ? `по умолчанию ${field.default}` : "не задано"}
        aria-describedby={bounds ? `${field.name}-bounds` : undefined}
        onChange={(e) => set(e.target.value)}
      />
      {bounds ? <span className="hint" id={`${field.name}-bounds`}>{bounds}</span> : null}
    </div>
  );
}

/** Комментарий над переменной: показывается всегда, правится по кнопке. */
function Comment({
  field,
  draft,
  onChange,
}: {
  field: Setting;
  draft: string | undefined;
  onChange: (name: string, value: string | undefined) => void;
}) {
  const editing = draft !== undefined;
  if (!field.editable) return null;
  if (editing) {
    return (
      <div className="set-comment">
        <textarea
          value={draft}
          autoFocus
          rows={2}
          placeholder="зачем эта переменная здесь"
          onChange={(e) => onChange(field.name, e.target.value)}
        />
        <button className="btn-ghost btn-sm" onClick={() => onChange(field.name, undefined)}>Отмена</button>
      </div>
    );
  }
  return (
    <div className="set-comment">
      {field.comment ? <span className="set-note"># {field.comment}</span> : null}
      <button onClick={() => onChange(field.name, field.comment)}>
        {field.comment ? "Править комментарий" : "Добавить комментарий"}
      </button>
    </div>
  );
}

export function SettingsOverlay({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [doc, setDoc] = useState<SettingsDoc | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [filter, setFilter] = useState("");
  const savingRef = useRef(false);
  const loadVersion = useRef(0);

  useEffect(() => {
    if (!open || savingRef.current) return;
    let live = true;
    const version = ++loadVersion.current;
    loadSettings().then((value) => {
      if (live && version === loadVersion.current) { setDoc(value); setError(null); }
    }).catch((e: Error) => { if (live && version === loadVersion.current) setError(e.message); });
    return () => { live = false; };
  }, [open]);

  const patch =
    (set: typeof setDrafts) => (name: string, value: string | undefined) =>
      set((prev) => {
        const next = { ...prev };
        if (value === undefined) delete next[name];
        else next[name] = value;
        return next;
      });
  const change = patch(setDrafts);
  const comment = patch(setNotes);

  const sections = useMemo(() => {
    if (!doc) return [];
    const all = doc.sections;
    const needle = filter.trim().toLowerCase();
    if (!needle) return all;
    return all
      .map((s) => ({
        ...s,
        fields: s.fields.filter(
          (f) =>
            f.name.toLowerCase().includes(needle) ||
            f.description.toLowerCase().includes(needle),
        ),
      }))
      .filter((s) => s.fields.length);
  }, [doc, filter]);

  const dirty = new Set([...Object.keys(drafts), ...Object.keys(notes)]).size;

  const secrets = useMemo(
    () => new Set((doc?.sections ?? []).flatMap((s) => s.fields.filter((f) => f.secret).map((f) => f.name))),
    [doc],
  );
  useEffect(() => {
    // Черновик обычной переменной переживает закрытие окна — это удобство.
    // Черновик секрета не переживает: незаписанный токен не должен лежать в
    // памяти вкладки всю сессию только потому, что окно закрыли не глядя.
    if (open) return;
    setDrafts((current) => {
      const kept = Object.fromEntries(Object.entries(current).filter(([name]) => !secrets.has(name)));
      return Object.keys(kept).length === Object.keys(current).length ? current : kept;
    });
  }, [open, secrets]);

  const save = async () => {
    if (savingRef.current || !dirty) return;
    savingRef.current = true;
    ++loadVersion.current;
    setSaving(true);
    setError(null);
    setStatus(null);
    try {
      const result = await saveSettings(drafts, notes);
      setDoc(await loadSettings());
      // Clear only submitted values; preserve edits made during the request.
      setDrafts((current) => Object.fromEntries(Object.entries(current).filter(([key, value]) => drafts[key] !== value)));
      setNotes((current) => Object.fromEntries(Object.entries(current).filter(([key, value]) => notes[key] !== value)));
      const restart = result.restart_required;
      setStatus(
        `записано ${result.saved.length} в ${result.path}` +
          (restart.length
            ? ` · перезапустите сервер, чтобы применить: ${restart.join(", ")}`
            : ""),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  };

  // Панель остаётся смонтированной, чтобы правки пережили закрытие окна; в
  // storage они не уезжают никогда, а секреты снимаются эффектом выше.
  if (!open) return null;
  return (
    <Modal label="Настройки" onClose={onClose}>
      <Panel
        title="Настройки"
        right={<span className="hint">{doc?.path ?? "…"}</span>}
        foot={
          <>
            <button className="btn-primary" onClick={save} disabled={!dirty || saving}>
              {saving ? "Запись…" : `Сохранить${dirty ? ` (${dirty})` : ""}`}
            </button>
            <button
              onClick={() => {
                setDrafts({});
                setNotes({});
              }}
              disabled={!dirty || saving}
            >
              Сбросить правки
            </button>
            <button className="btn-ghost" onClick={onClose}>Закрыть · esc</button>
            {dirty ? <span className="hint">Правки сохранятся при закрытии окна до обновления страницы.</span> : null}
            {status ? <span className="ok">{status}</span> : null}
            {error ? <span className="error">{error}</span> : null}
          </>
        }
      >
        <div className="settings-search">
          <span className="set-name">Поиск</span>
          <label className="canvas-search">
            <Search size={15} aria-hidden="true" />
            <input
              type="text"
              value={filter}
              autoFocus
              aria-label="Поиск настройки"
              placeholder="Часть имени или описания"
              onChange={(e) => setFilter(e.target.value)}
            />
          </label>
        </div>

        {!doc && !error ? (
          <div className="engine-widget">
            <span className="skeleton" style={{ width: "40%" }} />
            <span className="skeleton" style={{ width: "70%" }} />
            <span className="skeleton" style={{ width: "55%" }} />
          </div>
        ) : null}

        {/*
          Что применено прямо сейчас — не то же самое, что лежит в файле.
          Файл читается при старте процесса, а переменная из окружения сильнее
          файла и правкой файла не меняется. Пока показывали только файл, оба
          случая выглядели одинаково: «я же поменял, а оно работает по-старому».
        */}
        {doc?.applied?.length ? (
          <details className="set-applied" open={doc.restart_required}>
            <summary>
              {doc.restart_required
                ? <ChevronDown size={14} aria-hidden="true" />
                : <ChevronRight size={14} aria-hidden="true" />}
              Применено сейчас: {doc.applied.length}
              {doc.restart_required ? " · файл отличается, нужен перезапуск" : ""}
            </summary>
            {doc.note ? <p className="hint">{doc.note}</p> : null}
            <table className="set-applied-table">
              <thead>
                <tr><th>переменная</th><th>значение</th><th>откуда</th></tr>
              </thead>
              <tbody>
                {doc.applied.map((item) => (
                  <tr key={item.name} className={item.restart_required ? "warn" : undefined}>
                    <td>{item.name}</td>
                    <td>{item.value || "—"}</td>
                    <td>{item.source}{item.restart_required ? " · нужен перезапуск" : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        ) : null}

        {sections.map((section) => (
          <div className="set-section" key={section.title}>
            <h3>{section.title}</h3>
            {section.fields.map((field) => (
              <div key={field.name}>
                <Field field={field} draft={drafts[field.name]} onChange={change} />
                {field.description ? (
                  <div className="set-desc">{field.description}</div>
                ) : null}
                <Comment field={field} draft={notes[field.name]} onChange={comment} />
              </div>
            ))}
          </div>
        ))}
      </Panel>
    </Modal>
  );
}
