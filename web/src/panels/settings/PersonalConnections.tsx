/**
 * «Мои подключения»: личные токены Jira и Confluence, свой проект и своё пространство.
 *
 * Адрес сервера общий — его задаёт администратор, — а токен у каждого свой:
 * с ним агент читает и пишет от имени человека и видит ровно то, что видит
 * он. Без своего токена прогон, которому нужна Jira, остановится с просьбой
 * добавить его здесь: общий из `.env` за человека не подставляется.
 *
 * Проект и пространство — не пропуск, а место: пустое поле значит «общее по
 * умолчанию», и это общее значение написано прямо в поле, а не угадывается.
 *
 * Токен вводится и больше не показывается: с сервера приходит только «задан»
 * и дата. Черновик живёт в компоненте, а компонент — пока открыта страница:
 * закрыли настройки — несохранённый токен ушёл из памяти вкладки вместе с ним.
 */

import { useEffect, useState } from "react";

import {
  checkConnection,
  forgetConnection,
  loadConnections,
  saveConnections,
  type Connection,
  type ConnectionField,
  type ConnectionsDoc,
} from "../../api";
import { StatusDot } from "../../ui";
import { KeyRound, TriangleAlert } from "../../ui/icons";

function when(iso: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
}

/** Что написать в пустом поле: общее значение, способ входа или просто «не задано». */
function placeholder(field: ConnectionField): string {
  if (field.kind === "place") return field.default ? `общее: ${field.default}` : "не задано";
  if (field.kind === "email") return "пусто — токен уйдёт как Bearer";
  return "не задано";
}

export function PersonalConnections({ service }: { service: boolean }) {
  const [doc, setDoc] = useState<ConnectionsDoc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  useEffect(() => {
    if (service) return;
    let live = true;
    loadConnections().then(
      (value) => { if (live) { setDoc(value); setError(null); } },
      (e: Error) => { if (live) setError(e.message); },
    );
    return () => { live = false; };
  }, [service]);

  if (service) {
    return (
      <p className="inspector-empty">
        Вы вошли админ-токеном, а не пользователем: личных подключений у него нет.
        Jira и Confluence он читает общими токенами из `.env` — они в разделе «Интеграции».
      </p>
    );
  }

  const draft = (name: string, value: string | undefined) =>
    setDrafts((current) => {
      const next = { ...current };
      if (value === undefined) delete next[name];
      else next[name] = value;
      return next;
    });

  const dropDrafts = (names: string[]) =>
    setDrafts((current) => Object.fromEntries(Object.entries(current).filter(([name]) => !names.includes(name))));

  const run = async (action: () => Promise<ConnectionsDoc>, done?: () => void, system?: string) => {
    setBusy(system ?? "all");
    setError(null);
    try {
      setDoc(await action());
      done?.();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  /** Тронутые поля системы; пустой токен — «не менял», стирает его «Отключить». */
  const changes = (system: Connection): Record<string, string> => {
    const out: Record<string, string> = {};
    for (const field of system.fields) {
      const value = drafts[field.name];
      if (value === undefined) continue;
      if (field.secret ? value !== "" : value.trim() !== field.value) out[field.name] = value.trim();
    }
    return out;
  };

  const save = (system: Connection) =>
    run(() => saveConnections(changes(system)), () => dropDrafts(system.fields.map((f) => f.name)), system.id);

  const forget = (system: Connection) => {
    if (!window.confirm(
      `Удалить токен и e-mail ${system.title}? Прогоны, которым нужен ${system.title}, `
      + "остановятся, пока вы не добавите новый. Проект и пространство останутся.",
    )) return;
    const access = system.fields.filter((f) => f.kind !== "place").map((f) => f.name);
    return run(() => forgetConnection(system.id), () => dropDrafts(access), system.id);
  };

  const control = (field: ConnectionField, locked: boolean) => {
    const value = drafts[field.name];
    if (!field.secret) {
      return (
        <span className="set-control">
          <input
            type="text"
            value={value ?? field.value}
            disabled={locked}
            aria-label={field.label}
            placeholder={placeholder(field)}
            onChange={(event) => draft(field.name, event.target.value)}
          />
        </span>
      );
    }
    if (value === undefined) {
      return (
        <span className="set-secret">
          <span className={field.filled ? "" : "hint"}>
            {field.filled ? `задан${field.updated_at ? ` ${when(field.updated_at)}` : ""}` : "не задан"}
          </span>
          <button className="btn-sm" disabled={locked} onClick={() => draft(field.name, "")}>
            {field.filled ? "Заменить" : "Задать"}
          </button>
        </span>
      );
    }
    return (
      <span className="set-secret">
        <input
          type="password"
          value={value}
          autoFocus
          autoComplete="off"
          aria-label={`${field.label}: новое значение`}
          placeholder="вставьте токен"
          onChange={(event) => draft(field.name, event.target.value)}
        />
        <button className="btn-ghost btn-sm" onClick={() => draft(field.name, undefined)}>Отмена</button>
      </span>
    );
  };

  return (
    <>
      {doc?.unavailable ? (
        <p className="settings-restart" role="status">
          <TriangleAlert size={15} aria-hidden="true" />
          {doc.unavailable}
        </p>
      ) : null}
      {error ? <p className="settings-restart" role="alert"><TriangleAlert size={15} aria-hidden="true" />{error}</p> : null}
      {!doc && !error ? (
        <div className="engine-widget">
          <span className="skeleton" style={{ width: "40%" }} />
          <span className="skeleton" style={{ width: "65%" }} />
        </div>
      ) : null}

      {doc?.systems.map((system) => {
        const dirty = Object.keys(changes(system)).length > 0;
        const working = busy === system.id;
        const locked = Boolean(doc.unavailable) || busy !== null;
        const access = system.fields.some((f) => f.kind !== "place" && f.filled);
        return (
          <div className="set-section" key={system.id}>
            <h3>{system.title}</h3>

            <div className="set-row">
              <div className="set-field">
                <span className="set-label"><span className="set-name">Сервер</span></span>
                <span className="set-control">
                  {system.base_url
                    ? <span className="mono">{system.base_url}</span>
                    : <span className="hint">не задан — адрес заполняет администратор ({system.id.toUpperCase()}_BASE_URL)</span>}
                </span>
              </div>
            </div>

            {system.fields.map((field) => {
              const touched = drafts[field.name] !== undefined && (field.secret || drafts[field.name].trim() !== field.value);
              return (
                <div className="set-row" key={field.name}>
                  <div className="set-field">
                    <span className="set-label">
                      <span className={`set-name ${touched ? "changed" : ""}`.trim()}>
                        {field.secret ? (
                          <span className="lock" title="токен не возвращается в браузер">
                            <KeyRound size={13} aria-hidden="true" />
                          </span>
                        ) : null}
                        {field.label}{touched ? " *" : ""}
                      </span>
                    </span>
                    {control(field, locked)}
                  </div>
                  <div className="set-desc">{field.hint}</div>
                  {field.error ? <div className="set-desc error">{field.error}</div> : null}
                </div>
              );
            })}

            <div className="set-row">
              <div className="set-field">
                <span className="set-label"><span className="set-name">Проверка</span></span>
                <span className="set-control set-connection">
                  {working ? (
                    <span className="hint">…</span>
                  ) : system.check ? (
                    <>
                      <StatusDot tone={system.check.ok ? "ok" : "bad"} />
                      <span>{system.check.detail}</span>
                    </>
                  ) : (
                    <span className="hint">{system.connected ? "ещё не проверялось" : "сначала задайте токен"}</span>
                  )}
                </span>
              </div>
              <div className="set-actions">
                <button className="btn-primary btn-sm" disabled={!dirty || locked} onClick={() => void save(system)}>
                  Сохранить
                </button>
                <button
                  className="btn-sm"
                  disabled={!system.connected || dirty || locked}
                  title={dirty ? "сначала сохраните изменения" : "спросить у сервера «кто я» этим токеном"}
                  onClick={() => void run(() => checkConnection(system.id), undefined, system.id)}
                >
                  Проверить
                </button>
                <button className="btn-ghost btn-sm" disabled={!access || locked} onClick={() => void forget(system)}>
                  Отключить
                </button>
              </div>
            </div>
          </div>
        );
      })}
    </>
  );
}
