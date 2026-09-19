/**
 * Настройки отдельной страницей: слева разделы, справа поля.
 *
 * Раньше это было модальное окно с собственной прокруткой на восемьдесят
 * переменных — список `.env`, показанный поверх работы. Работу оно закрывало,
 * а найти в нём что-то можно было только поиском по имени переменной, то есть
 * зная имя заранее.
 *
 * Логика записи не изменилась ни на строку: отправляются только тронутые
 * поля, нетронутый секрет не отправляется вообще, черновики переживают
 * закрытие страницы, а черновик секрета — не переживает. Изменилось место и
 * порядок: сначала то, что заполняют все, потом то, что трогают редко.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import {
  loadSettings,
  saveSettings,
  type ServerStatus,
  type Setting,
  type SettingsDoc,
} from "../../api";
import type { SettingsGroupId } from "../../app/sections";
import { ChevronDown, ChevronRight, Search, TriangleAlert, X } from "../../ui/icons";
import { AiSettings } from "./AiSettings";
import { AppearanceSettings } from "./AppearanceSettings";
import { groupOf, SETTINGS_GROUPS, sectionTitle, settingLabel } from "./groups";
import { SettingsField } from "./SettingsField";

export function SettingsPage({
  open,
  group: wanted,
  onClose,
  online,
  animated,
  onToggleAnimation,
  onResetLayout,
}: {
  open: boolean;
  /** С какого раздела открыть: меню профиля ведёт либо к модели, либо к оформлению. */
  group?: SettingsGroupId;
  onClose: () => void;
  online: ServerStatus | null;
  animated: boolean;
  onToggleAnimation: () => void;
  onResetLayout: () => void;
}) {
  const [doc, setDoc] = useState<SettingsDoc | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [filter, setFilter] = useState("");
  const [group, setGroup] = useState<SettingsGroupId>(wanted ?? "ai");
  const savingRef = useRef(false);
  const loadVersion = useRef(0);
  const page = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open || savingRef.current) return;
    let live = true;
    const version = ++loadVersion.current;
    loadSettings().then((value) => {
      if (live && version === loadVersion.current) { setDoc(value); setError(null); }
    }).catch((e: Error) => { if (live && version === loadVersion.current) setError(e.message); });
    return () => { live = false; };
  }, [open]);

  useEffect(() => {
    if (open && wanted) { setGroup(wanted); setFilter(""); }
  }, [open, wanted]);

  // Фокус уходит на страницу и возвращается туда, откуда её открыли: раздел
  // открывается из меню профиля, и вернуться после Escape нужно именно туда.
  useEffect(() => {
    if (!open) return;
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    page.current?.focus({ preventScroll: true });
    return () => {
      if (opener.current?.isConnected) opener.current.focus();
    };
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

  const dirty = new Set([...Object.keys(drafts), ...Object.keys(notes)]).size;

  const secrets = useMemo(
    () => new Set((doc?.sections ?? []).flatMap((s) => s.fields.filter((f) => f.secret).map((f) => f.name))),
    [doc],
  );
  useEffect(() => {
    // Черновик обычной переменной переживает закрытие страницы — это удобство.
    // Черновик секрета не переживает: незаписанный токен не должен лежать в
    // памяти вкладки всю сессию только потому, что раздел закрыли не глядя.
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

  // Страница остаётся смонтированной, чтобы правки пережили закрытие; в
  // storage они не уезжают никогда, а секреты снимаются эффектом выше.
  if (!open) return null;

  const needle = filter.trim().toLowerCase();
  const matches = (field: Setting) =>
    !needle
    || field.name.toLowerCase().includes(needle)
    || field.description.toLowerCase().includes(needle)
    || settingLabel(field).toLowerCase().includes(needle);

  const sections = (doc?.sections ?? [])
    .map((section) => ({
      ...section,
      fields: section.fields.filter(
        (field) => matches(field) && (needle || group === "advanced" || groupOf(field.name) === group),
      ),
    }))
    .filter((section) => section.fields.length);

  const counts = new Map<SettingsGroupId, number>();
  for (const section of doc?.sections ?? []) {
    for (const field of section.fields) {
      const id = groupOf(field.name);
      counts.set(id, (counts.get(id) ?? 0) + 1);
    }
  }

  const renderField = (field: Setting) => (
    <SettingsField
      key={field.name}
      field={field}
      draft={drafts[field.name]}
      onChange={change}
      note={notes[field.name]}
      onNote={comment}
    />
  );

  const current = SETTINGS_GROUPS.find((item) => item.id === group);

  return (
    <div
      className="settings"
      ref={page}
      tabIndex={-1}
      role="region"
      aria-label="Настройки приложения"
      onKeyDown={(event) => {
        if (event.key !== "Escape") return;
        event.preventDefault();
        event.stopPropagation();
        onClose();
      }}
    >
      <div className="settings-bar">
        <h1 className="settings-title">Настройки</h1>
        <span className="hint mono" title="файл, в который пишутся значения">{doc?.path ?? "…"}</span>
        <label className="canvas-search">
          <Search size={15} aria-hidden="true" />
          <input
            type="text"
            value={filter}
            aria-label="Поиск настройки"
            placeholder="Найти настройку…"
            onChange={(event) => setFilter(event.target.value)}
          />
        </label>
        <span className="settings-bar-spacer" />
        {status ? <span className="ok">{status}</span> : null}
        {error ? <span className="error">{error}</span> : null}
        <button onClick={() => { setDrafts({}); setNotes({}); }} disabled={!dirty || saving}>
          Сбросить правки
        </button>
        <button className="btn-primary" onClick={save} disabled={!dirty || saving}>
          {saving ? "Запись…" : `Сохранить${dirty ? ` (${dirty})` : ""}`}
        </button>
        <button className="btn-ghost btn-icon" aria-label="Закрыть настройки" title="Закрыть · esc" onClick={onClose}>
          <X size={17} aria-hidden="true" />
        </button>
      </div>

      <div className="settings-body">
        <nav className="settings-nav" aria-label="Разделы настроек">
          {SETTINGS_GROUPS.map((item) => (
            <button
              key={item.id}
              className="settings-nav-item"
              aria-current={!needle && group === item.id ? "page" : undefined}
              title={item.hint}
              onClick={() => { setGroup(item.id); setFilter(""); }}
            >
              <item.icon size={16} aria-hidden="true" />
              <span className="truncate">{item.title}</span>
              {item.id !== "appearance" && item.id !== "ai" ? (
                <span className="settings-nav-count">
                  {item.id === "advanced"
                    ? (doc?.sections ?? []).reduce((total, section) => total + section.fields.length, 0)
                    : counts.get(item.id) ?? 0}
                </span>
              ) : null}
            </button>
          ))}
        </nav>

        <div className="settings-content">
          {!doc && !error ? (
            <div className="engine-widget">
              <span className="skeleton" style={{ width: "40%" }} />
              <span className="skeleton" style={{ width: "70%" }} />
              <span className="skeleton" style={{ width: "55%" }} />
            </div>
          ) : null}

          {doc?.restart_required && !needle ? (
            <p className="settings-restart" role="status">
              <TriangleAlert size={15} aria-hidden="true" />
              Файл разошёлся с работающим процессом: часть значений применится
              после перезапуска сервера агента. Что именно применено сейчас —
              в разделе «Продвинутые».
            </p>
          ) : null}

          {needle ? (
            <p className="settings-found">
              Найдено полей: {sections.reduce((total, section) => total + section.fields.length, 0)}
              <button className="btn-ghost btn-sm" onClick={() => setFilter("")}>Сбросить поиск</button>
            </p>
          ) : (
            <header className="settings-section-head">
              <h2>{current?.title}</h2>
              <p className="hint">{current?.hint}</p>
            </header>
          )}

          {!needle && group === "ai" && doc ? (
            <AiSettings
              doc={doc}
              drafts={drafts}
              notes={notes}
              onChange={change}
              onNote={comment}
              online={online}
              onAdvanced={(next, value) => { setGroup(next); setFilter(value); }}
            />
          ) : null}

          {!needle && group === "appearance" ? (
            <AppearanceSettings
              animated={animated}
              onToggleAnimation={onToggleAnimation}
              onResetLayout={onResetLayout}
            />
          ) : null}

          {/*
            Что применено прямо сейчас — не то же самое, что лежит в файле.
            Файл читается при старте процесса, а переменная из окружения сильнее
            файла и правкой файла не меняется. Пока показывали только файл, оба
            случая выглядели одинаково: «я же поменял, а оно работает по-старому».
          */}
          {!needle && group === "advanced" && doc?.applied?.length ? (
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

          {needle || (group !== "ai" && group !== "appearance") ? (
            sections.length ? (
              sections.map((section) => {
                const heading = sectionTitle(section.title);
                return (
                  <div className="set-section" key={section.title}>
                    <h3>{heading.title}</h3>
                    {heading.note ? <p className="hint set-section-note">{heading.note}</p> : null}
                    {section.fields.map(renderField)}
                  </div>
                );
              })
            ) : doc ? (
              <p className="inspector-empty">
                {needle ? "Ничего не найдено." : "В этом разделе пока нет настроек."}
              </p>
            ) : null
          ) : null}
        </div>
      </div>
    </div>
  );
}
