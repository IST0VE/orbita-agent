/**
 * Подтверждение внешнего действия: черновики, различия и решение.
 *
 * Самая дорогая часть экрана: по ней принимают решение, после которого
 * что-то появляется в чужой системе. Поэтому она отделена от всего
 * остального — её читают и правят вместе.
 */
import { useEffect, useState } from "react";
import { text } from "./shared";
import { SafeMarkdown } from "../../security/safeMarkdown";
import { safeUrl } from "../../security/safeUrl";
import type { WidgetProps } from "../../manifest/types";
import { JsonWidget } from "./primitives";

export type Draft = {
  id?: string;
  kind?: string;
  title?: string;
  action?: string;
  where?: string;
  format?: string;
  document?: string;
  chars?: number;
  url?: string;
  note?: string;
  fields?: Array<{ label?: string; value?: string }>;
  /** Различия с тем, что лежит сейчас. Есть только у перезаписи. */
  diff?: {
    available?: boolean;
    reason?: string;
    added?: number;
    removed?: number;
    unchanged?: boolean;
    text?: string;
  };
};


// Что остановка сделает с объектом, словами оператора. Ключи приходят из
// `agent/drafts.py` и там же описаны.
export const DRAFT_ACTIONS: Record<string, string> = {
  create: "создать",
  update: "перезаписать существующую",
  unknown: "судьба неизвестна",
  none: "цель ничего не сделает",
  form: "проверить в Jira",
};


export function CopyDraftButton({ value, label }: { value: string; label: string }) {
  const [notice, setNotice] = useState("");
  return <span><button onClick={async () => {
    try {
      if (!navigator.clipboard) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(value);
      setNotice("Скопировано");
    } catch {
      setNotice("Копирование недоступно. Раскройте текст и скопируйте вручную.");
    }
  }}>[{label}]</button><span role="status" className="hint">{notice}</span></span>;
}


export function draftSummary(drafts: Draft[]): string {
  const counts = new Map<string, number>();
  for (const draft of drafts) {
    const key = text(draft.action) || "unknown";
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts].map(([action, count]) => `${DRAFT_ACTIONS[action] ?? action}: ${count}`).join(", ");
}


/**
 * Черновики: то, что появится снаружи, до того как оно появится.
 *
 * Каждая карточка раскрывается отдельно и показывает тело в том виде, в
 * каком оно уедет, — страницу после рендерера цели, описание задачи после
 * сборки из полей плана. Сводкой это не заменяется: решение «подтвердить»
 * принимают по содержанию, а не по числу страниц.
 *
 * Markdown рисуется разметкой, всё остальное — как есть, моноширинным. XHTML
 * storage format показывается текстом намеренно: оператор должен видеть то,
 * что уедет на wiki, а не браузерную интерпретацию этого.
 */
/** Что изменится в существующем объекте: числа сразу, строки — по запросу. */
export function DraftDiff({ diff }: { diff: NonNullable<Draft["diff"]> }) {
  if (!diff.available) {
    return diff.reason ? <p className="hint draft-diff-note">{text(diff.reason)}</p> : null;
  }
  if (diff.unchanged) return <p className="hint draft-diff-note">Содержимое не меняется.</p>;
  const summary = `Изменения: +${diff.added ?? 0} / −${diff.removed ?? 0} строк`;
  return <details className="draft-diff">
    <summary>{summary}</summary>
    <pre className="draft-diff-body">{text(diff.text)}</pre>
  </details>;
}


export function DraftListWidget({ value }: WidgetProps) {
  const drafts = Array.isArray(value) ? (value as Draft[]) : [];
  if (!drafts.length) return <div className="hint">Черновиков нет.</div>;
  return <div className="draft-list">
    <div className="draft-list-head">
      <b>{drafts.some((draft) => draft.action === "form") ? "Формы Jira" : "Подготовлено объектов"}: {drafts.length}</b>
      <span className="hint">{draftSummary(drafts)}</span>
    </div>
    {drafts.map((draft, index) => {
      const action = text(draft.action) || "unknown";
      // Пустой url это не ссылка: `new URL("", origin)` вернул бы адрес самой
      // страницы, и черновик получил бы ссылку сам на себя.
      const href = draft.url ? safeUrl(text(draft.url), true) : null;
      const body = text(draft.document);
      return <div key={text(draft.id) || index}><details className="draft">
        <summary>
          <span className="draft-open-label">Открыть текст ▸</span>
          <span className={`draft-action draft-action-${action}`}>{DRAFT_ACTIONS[action] ?? action}</span>
          <b>{text(draft.title) || text(draft.id)}</b>
          <span className="hint">{text(draft.where)}{draft.chars ? ` · ${draft.chars} символов` : ""}</span>
        </summary>
        {draft.note ? <div className="hint">{text(draft.note)}</div> : null}
        {href && action !== "form" ? <a href={href} target="_blank" rel="noreferrer noopener">существующий объект</a> : null}
        {(draft.fields ?? []).length ? <dl className="draft-fields">
          {(draft.fields ?? []).map((field, position) => <div key={position}>
            <dt>{text(field.label)}</dt><dd>{text(field.value)}</dd>
          </div>)}
        </dl> : null}
        {text(draft.format) === "markdown"
          ? <SafeMarkdown value={body} />
          : <pre className="draft-body">{body}</pre>}
      </details>
        {/*
          Различия — рядом с карточкой, а не внутри неё: «перезапишет
          существующую» без них предупреждение без содержания. Перезапись
          бывает уточнением абзаца и бывает потерей чужой работы, и решают
          их по-разному.
        */}
        {draft.diff ? <DraftDiff diff={draft.diff} /> : null}
        {action === "form" ? <div className="external-drafts-action">
          {href ? <a href={href} target="_blank" rel="noreferrer noopener">Открыть в Jira ↗</a> : null}
          <CopyDraftButton value={text(draft.title)} label="копировать заголовок" />
          <CopyDraftButton value={body} label="копировать описание" />
          {draft.note ? <p className="hint">{draft.note}</p> : null}
        </div> : null}
      </div>;
    })}
  </div>;
}


export function ApprovalWidget({ value, binding, readonly, onAction }: WidgetProps) {
  const [reason, setReason] = useState("");
  const payloadOf = value && typeof value === "object" ? value as Record<string, unknown> : {};
  const [project, setProject] = useState(() => text(payloadOf.project));
  const interruptId = text(binding.options?.interruptId);
  const ruleId = text(binding.options?.ruleId);
  const payload = payloadOf;
  // Проект спрашивается только у остановки перед заведением задач: это
  // единственное, чего конвейер не может вывести из аналитики.
  const asksProject = payload.action === "jira";
  const known = Array.isArray(payload.projects) ? payload.projects as Array<{ key?: string; name?: string }> : [];
  const nativeDrafts = payload.action === "publish" && payload.target === "confluence";
  // Запросы, которые составила модель: показываются целиком, без раскрытия.
  // Подтверждают то, что уйдёт в источник, а не пересказ.
  const queries = Array.isArray(payload.queries)
    ? payload.queries as Array<Record<string, unknown>>
    : [];
  const decide = (decision: "approved" | "rejected" | "drafts") => onAction?.({
    kind: "interrupt.resume",
    interruptId,
    ruleId,
    payload: {
      decision,
      ...(reason ? { reason } : {}),
      ...(asksProject && project.trim() ? { project: project.trim().toUpperCase() } : {}),
    },
  });
  return <div className="approve" role="dialog" aria-modal="true" aria-label="Требуется решение оператора">
    <h4>▲ ТРЕБУЕТСЯ ПОДТВЕРЖДЕНИЕ</h4>
    {payload.title ? <b>{text(payload.title)}</b> : null}
    {/* Черновики выше сводки: подтверждают то, что уедет, а не пересказ. */}
    {Array.isArray(payload.drafts) && payload.drafts.length
      ? <DraftListWidget {...({ value: payload.drafts } as unknown as WidgetProps)} />
      : null}
    {queries.length ? <ul className="approve-queries">{queries.map((item, index) => <li key={index}>
      <span className="hint">{text(item.tool)}{item.purpose ? ` · ${text(item.purpose)}` : ""}</span>
      <pre>{text(item.query) || JSON.stringify(item.arguments ?? {})}</pre>
    </li>)}</ul> : null}
    {(payload.warnings as string[] | undefined)?.length
      ? <ul className="draft-warnings">{(payload.warnings as string[]).map((warning, index) => <li key={index}>{text(warning)}</li>)}</ul>
      : null}
    {payload.document
      ? <details className="draft-whole"><summary>Сводка конвейера</summary><SafeMarkdown value={payload.document} /></details>
      : <JsonWidget {...({ value } as WidgetProps)} />}
    {asksProject ? <label>Проект Jira
      <input list="approve-projects" value={project} placeholder="ORB" maxLength={40} onChange={(event) => setProject(event.target.value)} />
      <datalist id="approve-projects">{known.map((item) => <option value={text(item.key)} key={text(item.key)}>{text(item.name)}</option>)}</datalist>
    </label> : null}
    <label>Причина (необязательно)<input value={reason} maxLength={4000} onChange={(event) => setReason(event.target.value)} /></label>
    {nativeDrafts ? <div className="external-drafts-action">
      <button disabled={readonly} onClick={() => decide("drafts")}>[создать черновики в Confluence]</button>
      <p className="hint">Появятся ссылки на редактор Confluence. Внесите правки и опубликуйте страницы там. Для существующих страниц создаются отдельные копии.</p>
    </div> : null}
    {asksProject ? <div className="external-drafts-action">
      <button disabled={readonly || !project.trim()} onClick={() => decide("drafts")}>[подготовить формы для проверки в Jira]</button>
      <p className="hint">Откройте формы в Jira, внесите правки и создайте задачи вручную. Возможность заполнить поля по ссылке зависит от версии Jira.</p>
    </div> : null}
    <button autoFocus className="btn-yes" disabled={readonly || (asksProject && !project.trim())} onClick={() => decide("approved")}>[подтвердить]</button>
    <button className="btn-no" disabled={readonly} onClick={() => decide("rejected")}>
      [{text(payload.reject_label) || "отклонить"}]
    </button>
    {payload.reject_hint ? <p className="hint">{text(payload.reject_hint)}</p> : null}
  </div>;
}


/**
 * Выбор проекта Jira — единственный вопрос конвейера декомпозиции.
 *
 * Список приезжает из трекера, но поле остаётся полем ввода: справочник
 * бывает недоступен (токен без прав, ненастроенная Jira), а ключ проекта
 * оператор знает наизусть. Недоступный список — подсказка, а не отказ.
 */
export function JiraProjectWidget({ binding, context, value, onChange, readonly }: WidgetProps) {
  const [items, setItems] = useState<Array<{ key: string; name: string }>>([]);
  const [hint, setHint] = useState("");
  const resourceId = text(binding.options?.resource_id);
  const operation = text(binding.options?.operation) || "projects";
  const resource = context.resource;
  useEffect(() => {
    let live = true;
    resource(resourceId, operation)
      .then((response) => {
        const data = response as { projects?: Array<{ key: string; name: string }>; reason?: string; default?: string };
        if (!live) return;
        setItems(Array.isArray(data.projects) ? data.projects : []);
        setHint(text(data.reason));
        if (!text(value) && text(data.default)) onChange?.(text(data.default));
      })
      .catch((reason: Error) => live && setHint(reason.message));
    return () => { live = false; };
    // Справочник спрашивается один раз на монтирование: проекты не меняются
    // за время прогона, а лишний поход в трекер оплачивается задержкой.
  }, [operation, resource, resourceId]);
  return <div className="jira-project">
    <input list="jira-projects" value={text(value)} disabled={readonly} placeholder="ключ проекта, например ORB"
      maxLength={40} aria-label="Проект Jira" onChange={(event) => onChange?.(event.target.value.toUpperCase())} />
    <datalist id="jira-projects">{items.map((item) => <option value={item.key} key={item.key}>{item.name}</option>)}</datalist>
    {hint ? <span className="hint">{hint}</span> : null}
  </div>;
}
