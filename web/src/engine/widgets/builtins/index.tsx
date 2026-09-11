import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Message } from "@langchain/langgraph-sdk";

import { formatBytes, formatTokens, formatUsd, type CostSummary } from "../../../lib/orbita";
import { SafeMarkdown } from "../../security/safeMarkdown";
import { safeUrl } from "../../security/safeUrl";
import type { JsonSchema, JsonValue, WidgetDefinition, WidgetProps } from "../../manifest/types";
import { schemaDefaults, validateForm } from "../formValidation";
import { WidgetErrorBoundary } from "../ErrorBoundary";

const boundary = WidgetErrorBoundary;
const definition = (
  type: string,
  component: WidgetDefinition["component"],
  modes: WidgetDefinition["modes"] = ["view"],
  ownHeader = false,
): WidgetDefinition => ({ type, version: "1.0.0", modes, component, errorBoundary: boundary, ownHeader });

function text(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "";
  return String(value);
}

/**
 * Значение поля выбора списком имён.
 *
 * Поле хранит либо строку (выбор один — схема draw.io), либо массив (выбор
 * множественный — комплект документации). Разбирать это в каждом виджете
 * значит написать одно и то же дважды и один раз ошибиться.
 */
function names(value: unknown): string[] {
  if (typeof value === "string") return value ? [value] : [];
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string" && item !== "");
}

function messageText(message: unknown): string {
  if (!message || typeof message !== "object") return text(message);
  const content = (message as { content?: unknown }).content;
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((part) =>
      typeof part === "string"
        ? part
        : part && typeof part === "object" && (part as { type?: string }).type === "text"
          ? text((part as { text?: unknown }).text)
          : "",
    )
    .join("");
}

function TextWidget({ value }: WidgetProps) {
  return <span>{text(value)}</span>;
}

function MarkdownWidget({ value }: WidgetProps) {
  return <SafeMarkdown value={value} />;
}

function CodeWidget({ value, binding }: WidgetProps) {
  const source = text(value);
  return (
    <div>
      <button onClick={() => navigator.clipboard?.writeText(source)}>[копировать]</button>
      <pre data-language={text(binding.options?.language)}>{source}</pre>
    </div>
  );
}

function JsonWidget({ value }: WidgetProps) {
  const [open, setOpen] = useState(false);
  return (
    <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>JSON</summary>
      {open ? <pre className="json-preview">{JSON.stringify(value, null, 2)}</pre> : null}
    </details>
  );
}

function TableWidget({ value }: WidgetProps) {
  const rows = Array.isArray(value) ? value.filter((row) => row && typeof row === "object") : [];
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row as Record<string, unknown>)))].slice(0, 50);
  if (!rows.length) return <span className="hint">нет строк</span>;
  return (
    <div className="table-scroll">
      <table>
        <thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
        <tbody>{rows.slice(0, 200).map((row, index) => (
          <tr key={index}>{columns.map((column) => <td key={column}>{text((row as Record<string, unknown>)[column])}</td>)}</tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function KeyValueWidget({ value }: WidgetProps) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return <JsonWidget {...({ value } as WidgetProps)} />;
  return <dl className="key-value">{Object.entries(value).map(([key, item]) => <div key={key}><dt>{key}</dt><dd>{text(item)}</dd></div>)}</dl>;
}

function NumberWidget({ value, binding, context }: WidgetProps) {
  const number = typeof value === "number" ? value : Number(value);
  return <span>{Number.isFinite(number) ? new Intl.NumberFormat(context.locale, binding.options as Intl.NumberFormatOptions).format(number) : "—"}</span>;
}

function MoneyWidget({ value, binding, context }: WidgetProps) {
  const number = typeof value === "number" ? value : Number(value);
  const currency = text(binding.options?.currency) || "USD";
  return <span>{Number.isFinite(number) ? new Intl.NumberFormat(context.locale, { style: "currency", currency }).format(number) : "—"}</span>;
}

function ProgressWidget({ value, binding }: WidgetProps) {
  const number = typeof value === "number" ? value : Number(value);
  const max = Number(binding.options?.max ?? 100);
  return <progress value={Number.isFinite(number) ? number : 0} max={Number.isFinite(max) && max > 0 ? max : 100} />;
}

function StatusWidget({ value }: WidgetProps) {
  return <span className={`runtime-status status-${text(value).toLowerCase()}`}>{text(value) || "unknown"}</span>;
}

/** Вызовы инструментов отдельной строкой: без них не видно, что агент читал. */
function toolCalls(message: unknown): Array<{ name?: string; args?: unknown }> {
  const calls = (message as { tool_calls?: unknown }).tool_calls;
  return Array.isArray(calls) ? (calls as Array<{ name?: string; args?: unknown }>) : [];
}

const MessageRow = memo(function MessageRow({ message }: { message: Message }) {
  const role = message.type ?? "message";
  const calls = toolCalls(message);
  const body = messageText(message);
  if (!body && !calls.length) return null;
  return <article className={`msg msg-${role}`}>
    <b className="msg-role">{role}</b>
    {body ? <div className="msg-text">{body}</div> : null}
    {calls.map((call, position) => <div className="tool-call" key={position}>→ {text(call.name)}<span className="args">({JSON.stringify(call.args ?? {})})</span></div>)}
  </article>;
});

const MessagesWidget = memo(function MessagesWidget({ value }: WidgetProps) {
  const [limit, setLimit] = useState(50);
  const messages = Array.isArray(value) ? (value as Message[]) : [];
  if (!messages.length) return <div className="hint">Сообщений пока нет.</div>;
  const start = Math.max(0, messages.length - limit);
  return <div className="engine-messages">
    {start > 0 ? <button onClick={() => setLimit((count) => count + 50)}>[показать предыдущие · ещё {start}]</button> : null}
    {messages.slice(start).map((message, index) => <MessageRow message={message} key={message.id ?? start + index} />)}
  </div>;
}, (previous, next) => previous.value === next.value);

function ChatInputWidget({ readonly, onAction }: WidgetProps) {
  const [draft, setDraft] = useState("");
  // Сохраняем текст при отказе проверки действия; очищаем после старта.
  useEffect(() => { if (readonly) setDraft(""); }, [readonly]);
  const send = () => {
    const value = draft.trim();
    if (!value || readonly) return;
    onAction?.({ kind: "run.start", payload: { value } });
  };
  return <div className="engine-chat-input"><textarea value={draft} disabled={readonly} placeholder="Опишите задачу…" onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(); }
  }} /><button disabled={readonly || !draft.trim()} onClick={send}>[запустить]</button></div>;
}

function fieldValue(value: unknown, schema: JsonSchema, onChange: (value: JsonValue) => void, path: string) {
  if (schema.enum) {
    return <select aria-label={schema.title ?? path} value={JSON.stringify(value)} onChange={(event) => onChange(JSON.parse(event.target.value) as JsonValue)}>{schema.enum.map((item, index) => <option value={JSON.stringify(item)} key={index}>{text(item)}</option>)}</select>;
  }
  if (schema.type === "boolean") return <input aria-label={schema.title ?? path} type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(event.target.checked)} />;
  if (schema.type === "number" || schema.type === "integer") return <input aria-label={schema.title ?? path} type="number" value={typeof value === "number" ? value : 0} min={schema.minimum} max={schema.maximum} onChange={(event) => onChange(Number(event.target.value))} />;
  if (schema.type === "object" || schema.properties) {
    const record = value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, JsonValue> : {};
    return <fieldset>{Object.entries(schema.properties ?? {}).map(([key, child]) => <label key={key}>{child.title ?? key}{fieldValue(record[key] ?? schemaDefaults(child), child, (next) => onChange({ ...record, [key]: next }), `${path}.${key}`)}</label>)}</fieldset>;
  }
  if (schema.type === "array") return <textarea aria-label={schema.title ?? path} value={JSON.stringify(value ?? [], null, 2)} onChange={(event) => {
    try { const parsed = JSON.parse(event.target.value) as JsonValue; if (Array.isArray(parsed)) onChange(parsed); } catch { /* keep draft in DOM until valid JSON */ }
  }} />;
  return schema.format === "multiline" || schema.format === "markdown"
    ? <textarea aria-label={schema.title ?? path} value={text(value)} maxLength={schema.maxLength} onChange={(event) => onChange(event.target.value)} />
    : <input aria-label={schema.title ?? path} type={schema.format === "date" ? "date" : schema.format === "date-time" ? "datetime-local" : "text"} value={text(value)} maxLength={schema.maxLength} onChange={(event) => onChange(event.target.value)} />;
}

/**
 * Универсальная форма по JSON Schema.
 *
 * В режиме `interrupt` она — запасной вариант для остановки, под которую в
 * манифесте нет своего правила: черновик собирается из `resume_schema`, а не
 * из тела прерывания, и отправляется как `interrupt.resume`. Отправить оттуда
 * `run.start` значит не ответить на остановку вовсе.
 */
function FormWidget({ binding, value, mode, readonly, onChange, onAction }: WidgetProps) {
  const schema = (binding.options?.schema ?? {}) as JsonSchema;
  const resuming = mode === "interrupt";
  const interruptId = text(binding.options?.interruptId);
  const ruleId = text(binding.options?.ruleId);
  const [draft, setDraft] = useState<JsonValue>(
    () => (resuming ? schemaDefaults(schema) : ((value as JsonValue) ?? schemaDefaults(schema))),
  );
  const errors = useMemo(() => validateForm(draft, schema), [draft, schema]);
  const submit = () => onAction?.(
    resuming
      ? { kind: "interrupt.resume", interruptId, ruleId, payload: draft }
      : { kind: "run.start", payload: draft },
  );
  return <form onSubmit={(event) => { event.preventDefault(); if (!Object.keys(errors).length) submit(); }}>
    {resuming ? <JsonWidget {...({ value } as WidgetProps)} /> : null}
    {fieldValue(draft, schema, (next) => { setDraft(next); onChange?.(next); }, "$")}
    {Object.keys(errors).length ? <ul className="error">{Object.entries(errors).map(([path, message]) => <li key={path}>{path}: {message}</li>)}</ul> : null}
    <button disabled={readonly || !!Object.keys(errors).length}>[отправить]</button>
  </form>;
}

type Draft = {
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
};

// Что остановка сделает с объектом, словами оператора. Ключи приходят из
// `agent/drafts.py` и там же описаны.
const DRAFT_ACTIONS: Record<string, string> = {
  create: "создать",
  update: "перезаписать существующую",
  unknown: "судьба неизвестна",
  none: "цель ничего не сделает",
  form: "проверить в Jira",
};

function CopyDraftButton({ value, label }: { value: string; label: string }) {
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

function draftSummary(drafts: Draft[]): string {
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
function DraftListWidget({ value }: WidgetProps) {
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

function ApprovalWidget({ value, binding, readonly, onAction }: WidgetProps) {
  const [reason, setReason] = useState("");
  const approve = useRef<HTMLButtonElement>(null);
  const payloadOf = value && typeof value === "object" ? value as Record<string, unknown> : {};
  const [project, setProject] = useState(() => text(payloadOf.project));
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    approve.current?.focus();
    return () => previous?.focus();
  }, []);
  const interruptId = text(binding.options?.interruptId);
  const ruleId = text(binding.options?.ruleId);
  const payload = payloadOf;
  // Проект спрашивается только у остановки перед заведением задач: это
  // единственное, чего конвейер не может вывести из аналитики.
  const asksProject = payload.action === "jira";
  const known = Array.isArray(payload.projects) ? payload.projects as Array<{ key?: string; name?: string }> : [];
  const nativeDrafts = payload.action === "publish" && payload.target === "confluence";
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
    <button ref={approve} className="btn-yes" disabled={readonly || (asksProject && !project.trim())} onClick={() => decide("approved")}>[подтвердить]</button>
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
function JiraProjectWidget({ binding, context, value, onChange, readonly }: WidgetProps) {
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

/** Заведённые задачи: ключ, заголовок и ссылка в трекер. */
function IssueListWidget({ value }: WidgetProps) {
  const result = value && typeof value === "object" ? value as {
    status?: string;
    project?: string;
    reason?: string;
    created?: Array<{ key: string; url: string; summary: string; type: string }>;
    failed?: Array<{ local: string; reason: string }>;
    warnings?: string[];
    drafts?: Draft[];
  } : null;
  if (!result) return <div className="hint">Задачи ещё не заводились.</div>;
  const created = result.created ?? [];
  return <div className="issue-list">
    <div className="issue-list-head">
      <span className={`runtime-status status-${text(result.status)}`}>{text(result.status) || "unknown"}</span>
      {result.project ? <span className="issue-project">{text(result.project)}</span> : null}
    </div>
    {result.reason ? <div className="hint">{text(result.reason)}</div> : null}
    {result.drafts?.length ? <DraftListWidget {...({ value: result.drafts } as WidgetProps)} /> : null}
    {created.length ? <ul className="resource-list">{created.map((issue) => {
      // Внутренняя Jira живёт и на http: ссылка на неё показывается так же,
      // как ссылка на страницу публикации, — см. PublicationWidget.
      const href = safeUrl(issue.url, true);
      return <li key={issue.key}>
        {href ? <a href={href} target="_blank" rel="noreferrer noopener">{text(issue.key)}</a> : <b>{text(issue.key)}</b>}
        <span className="issue-summary">{text(issue.summary)}</span>
      </li>;
    })}</ul> : null}
    {(result.failed ?? []).map((issue) => <div className="error" key={issue.local}>{text(issue.local)}: {text(issue.reason)}</div>)}
    {(result.warnings ?? []).map((warning, index) => <div className="hint" key={index}>{text(warning)}</div>)}
  </div>;
}

function ArtifactListWidget({ value }: WidgetProps) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return <div className="hint">Документов пока нет.</div>;
  return <div>{Object.entries(value).map(([name, document]) => <details key={name}><summary>{name}</summary><SafeMarkdown value={document} /></details>)}</div>;
}

function DocumentPreviewWidget({ value }: WidgetProps) { return <SafeMarkdown value={value} />; }
function DocumentDiffWidget({ value }: WidgetProps) { return <pre>{JSON.stringify(value, null, 2)}</pre>; }
function FileListWidget({ value }: WidgetProps) { return <TableWidget {...({ value } as WidgetProps)} />; }

/**
 * Выбор папки задачи, а внутри неё — файла-источника.
 *
 * Папки вывода показывают содержимое всегда, а не после выбора: это готовые
 * документы, за ними приходят читать, а не только чтобы пустить по ним прогон.
 * Папки задач раскрываются по выбору — их файлы относятся к будущему ходу.
 *
 * Клик по файлу задачи ВЫБИРАЕТ его источником, а не открывает. Раньше он
 * открывал: файл показывался на главном экране, и это было единственное, что
 * с ним можно было сделать. Выбор при этом существовал в графах и раньше —
 * но задать его можно было только словами в запросе, и оператор, ткнувший в
 * нужный документ, получал прогон по всей папке. Так что клик значит теперь
 * то, ради чего в него и целятся, а просмотр уехал на соседнюю кнопку.
 *
 * Куда уезжает выбор, говорит манифест: `options.document_input` — id поля,
 * `options.document_kind` — что этому полю годится (схема или текст). Не
 * названо поле — файлы открываются по клику: выбирать их некуда.
 *
 * Файлы папки вывода выбираются наравне с файлами задачи. Раньше их можно было
 * только открыть: считалось, что готовый документ — конец пути. Но конец пути
 * одного конвейера это вход другого — написанную аналитику раскладывает
 * на задачи граф Jira-декомпозиции, — и выбор источника там тот же самый
 * `configurable.input_file`. Запрет стоял только в этом виджете: соседнее поле
 * «Документ» те же файлы предлагало, а граф их читал. Оператор, ткнувший в
 * готовый документ, получал его просмотр вместо выбора и прогон по всей папке.
 */
function TaskPickerWidget({ binding, context, value, onChange, onAction, readonly }: WidgetProps) {
  const [items, setItems] = useState<
    Array<{
      name: string;
      title?: string;
      kind?: "input" | "output";
      files?: Array<{ name: string; size: number; text?: boolean; diagram?: boolean }>;
    }>
  >([]);
  const [error, setError] = useState("");
  const [newTask, setNewTask] = useState("");
  /** Что оператор свернул или раскрыл руками; остальное — по умолчанию. */
  const [folded, setFolded] = useState<Record<string, boolean>>({});
  const resourceId = text(binding.options?.resource_id);
  const operation = text(binding.options?.operation) || "list";
  const documentInput = text(binding.options?.document_input);
  const documentKind = text(binding.options?.document_kind) || "text";
  /** Сколько файлов влезает в поле-приёмник; сказано манифестом, не виджетом. */
  const documentMultiple = binding.options?.document_multiple === true;
  const resource = context.resource;
  const mutateResource = context.mutateResource;
  const setInput = context.setInput;
  const chosen = documentInput ? names(context.inputs[documentInput]) : [];
  /** Пустое значение поля-приёмника: у списка это список, а не пустая строка. */
  const cleared = documentMultiple ? [] : "";
  const reload = useCallback(() => resource(resourceId, operation).then((response) => {
    const tasks = (
      response as {
        tasks?: Array<{
          name: string;
          title?: string;
          kind?: "input" | "output";
          files?: Array<{ name: string; size: number; text?: boolean; diagram?: boolean }>;
        }>;
      }
    )?.tasks;
    setItems(Array.isArray(tasks) ? tasks : []);
    setError("");
  }), [operation, resource, resourceId]);
  useEffect(() => {
    let live = true;
    reload().catch((reason: Error) => live && setError(reason.message));
    return () => { live = false; };
  }, [reload]);
  const selected = items.find((item) => item.name === text(value));
  // Сменили папку — снимаем выбранный в прежней файл: он в новой не лежит, а
  // оставленное имя уехало бы в прогон и обернулось отказом «файла нет».
  const choose = (name: string) => {
    const next = text(value) === name ? "" : name;
    if (documentInput && next !== text(value)) setInput(documentInput, cleared);
    onChange?.(next);
  };
  const pick = (task: string, name: string) => {
    // Файл выбирают в папке, которая для этого хода и выбрана. Клик по файлу
    // чужой папки сначала переносит выбор туда — иначе он молча ничего бы
    // не значил, — и начинает выбор заново: имена прежней папки в новой
    // не лежат.
    if (text(value) !== task) {
      onChange?.(task);
      setInput(documentInput, documentMultiple ? [name] : name);
      return;
    }
    if (!documentMultiple) {
      setInput(documentInput, chosen.includes(name) ? "" : name);
      return;
    }
    setInput(
      documentInput,
      chosen.includes(name) ? chosen.filter((item) => item !== name) : [...chosen, name],
    );
  };
  const fold = (name: string, open: boolean) =>
    setFolded((previous) => ({ ...previous, [name]: !open }));
  // Документ открывается на главном экране, а не в углу панели: читать его
  // в колонке шириной с список файлов невозможно.
  const open = (task: string, name: string) =>
    resource(resourceId, "read", { task, name })
      .then((response) => {
        const document = response as { name: string; text: string };
        onAction?.({
          kind: "publication.open",
          payload: { title: document.name, text: document.text },
        });
        setError("");
      })
      .catch((reason: Error) => setError(reason.message));
  const create = () => {
    const name = newTask.trim();
    if (!name) return;
    mutateResource(resourceId, "create", { name })
      .then(() => reload())
      .then(() => {
        onChange?.(name);
        setNewTask("");
      })
      .catch((reason: Error) => setError(reason.message));
  };
  return (
    <div className="task-picker">
      <div className="task-picker-label">
        Папка задачи
        <span className={selected ? "task-picker-current" : "hint"}>
          {selected ? selected.title || selected.name : "не выбрана"}
        </span>
      </div>
      <div className="task-picker-folders" aria-label="Папка задачи">
        {items.map((item) => {
          const active = item.name === text(value);
          const output = item.kind === "output";
          const files = item.files?.length ?? 0;
          // По умолчанию раскрыта папка вывода и выбранная задача; решение
          // оператора важнее умолчания и живёт до перезагрузки.
          const expanded = folded[item.name] === undefined ? output || active : !folded[item.name];
          return (
            <div className="task-picker-folder" key={item.name}>
              <div className="task-picker-row">
                {files ? (
                  <button
                    type="button"
                    className="task-picker-toggle"
                    aria-expanded={expanded}
                    aria-label={`${expanded ? "свернуть" : "развернуть"} ${item.title || item.name}`}
                    title={expanded ? "свернуть" : "развернуть"}
                    onClick={() => fold(item.name, !expanded)}
                  >
                    {expanded ? "▾" : "▸"}
                  </button>
                ) : (
                  <span className="task-picker-toggle" />
                )}
                <button
                  type="button"
                  aria-pressed={active}
                  disabled={readonly}
                  className={`${active ? "selected" : ""} ${output ? "output" : ""}`}
                  onClick={() => choose(item.name)}
                  title={output ? "готовая документация из папки outputs" : "выбрать папку для прогона"}
                >
                  <span className="mark">{active ? "◆" : "◇"}</span>
                  <span className="name">{item.title || item.name}</span>
                  {output ? <span className="badge">out</span> : null}
                  <span className="count">{files}</span>
                </button>
              </div>
              {expanded && item.files?.length ? (
                <ul className="resource-list task-picker-files">
                  {item.files.map((file) => {
                    const readable = file.text !== false || Boolean(file.diagram);
                    // Выбирать можно только то, что выбранному полю годится:
                    // .drawio в поле схемы, текст — в поле документа.
                    const selectable = Boolean(documentInput) && fileMatches(file, documentKind);
                    const picked =
                      selectable && item.name === text(value) && chosen.includes(file.name);
                    return (
                      <li
                        key={file.name}
                        className={`${file.diagram ? "diagram" : file.text === false ? "binary" : "text"}${picked ? " picked" : ""}`}
                      >
                        <button
                          disabled={readonly ? selectable : !readable}
                          aria-pressed={selectable ? picked : undefined}
                          title={
                            selectable
                              ? picked
                                ? documentMultiple && chosen.length > 1
                                  ? `${file.name}: убрать из выбранных`
                                  : `${file.name}: снять выбор — конвейер прочитает папку целиком`
                                : documentMultiple && chosen.length
                                  ? `${file.name}: добавить к выбранным`
                                  : `${file.name}: работать по этому файлу`
                              : file.name
                          }
                          onClick={() =>
                            selectable ? pick(item.name, file.name) : open(item.name, file.name)
                          }
                        >
                          {selectable ? <span className="mark">{picked ? "◆" : "◇"}</span> : null}
                          <span className="name">{file.name}</span>
                        </button>
                        {selectable && readable ? (
                          <button
                            className="task-picker-open"
                            title={`${file.name}: показать содержимое`}
                            aria-label={`показать ${file.name}`}
                            onClick={() => open(item.name, file.name)}
                          >
                            [?]
                          </button>
                        ) : null}
                        <span className="hint">{formatBytes(file.size)}</span>
                      </li>
                    );
                  })}
                </ul>
              ) : null}
            </div>
          );
        })}
      </div>
      <div className="resource-create">
        <input
          value={newTask}
          disabled={readonly}
          placeholder="новая папка"
          onChange={(event) => setNewTask(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter") create(); }}
        />
        <button disabled={readonly || !newTask.trim()} onClick={create}>[создать]</button>
      </div>
      {error ? <span className="error">{error}</span> : null}
    </div>
  );
}

/** Годится ли файл в источник для поля с таким `kind`. */
function fileMatches(file: { text?: boolean; diagram?: boolean }, kind: string): boolean {
  return kind === "diagram" ? Boolean(file.diagram) : file.text !== false;
}

/**
 * Что выбрано источником — и ничего больше.
 *
 * Панель показывала весь каталог папки: тот же список, что стоит деревом
 * выше, только плоский. Второй такой же список — это не выбор, а шум, в
 * котором выбранный файл ничем не выделен; выбирают в дереве, где файлы
 * лежат по папкам. Здесь остаётся ответ на единственный вопрос, который
 * задают перед запуском: что именно уедет в прогон.
 *
 * Два поля, один виджет, разница в `options.kind`: `diagram` — схема draw.io
 * для графа разбора схем, `text` — документы остальных конвейеров.
 * `options.multiple` говорит, сколько имён поле держит: схема одна, комплект
 * документации — сколько выбрали.
 *
 * Список папки всё-таки читается, но не рисуется: по нему видно, что
 * выбранного файла в папке больше нет — папку сменили, файл удалили. Молчать
 * об этом нельзя, а узнать это без списка неоткуда.
 *
 * Пустой выбор — законное значение, и у полей оно значит разное: для схемы —
 * «возьми первую и скажи, какую», для документа — «читай папку целиком».
 */
function FilePickerWidget({ binding, context, value, onChange, readonly }: WidgetProps) {
  const [available, setAvailable] = useState<string[] | null>(null);
  const [hint, setHint] = useState("");
  const resourceId = text(binding.options?.resource_id);
  const operation = text(binding.options?.operation) || "list";
  const kind = text(binding.options?.kind) || "text";
  const multiple = binding.options?.multiple === true;
  const dependsOn = text(binding.options?.depends_on);
  const task = text(dependsOn ? context.inputs[dependsOn] : "");
  const resource = context.resource;
  useEffect(() => {
    let live = true;
    if (!task) {
      setAvailable(null);
      setHint("сначала выберите папку задачи");
      return;
    }
    resource(resourceId, operation)
      .then((response) => {
        if (!live) return;
        const tasks = (response as {
          tasks?: Array<{ name: string; files?: Array<{ name: string; diagram?: boolean }> }>;
        })?.tasks;
        const folder = (Array.isArray(tasks) ? tasks : []).find((item) => item.name === task);
        setAvailable((folder?.files ?? []).filter((file) => fileMatches(file, kind)).map((file) => file.name));
        setHint("");
      })
      .catch((reason: Error) => live && setHint(reason.message));
    return () => { live = false; };
  }, [kind, operation, resource, resourceId, task]);
  const chosen = names(value);
  // Выбранное имя не сбрасывается автоматически при смене папки: молча стереть
  // выбор оператора хуже, чем показать, что в новой папке такого файла нет.
  const missing = available ? chosen.filter((name) => !available.includes(name)) : [];
  const drop = (name: string) => {
    const rest = chosen.filter((item) => item !== name);
    onChange?.(multiple ? rest : "");
  };
  return <div className="file-picker">
    {chosen.length ? (
      <ul className="resource-list">
        {chosen.map((name) => (
          <li key={name} className={missing.includes(name) ? "missing" : undefined}>
            <span className="name" title={name}>
              <span className="mark">◆</span>
              <span className="text">{name}</span>
            </span>
            <button
              type="button"
              className="file-picker-drop"
              disabled={readonly}
              title={`убрать ${name} из выбранных`}
              aria-label={`убрать ${name} из выбранных`}
              onClick={() => drop(name)}
            >
              [×]
            </button>
          </li>
        ))}
      </ul>
    ) : null}
    {!chosen.length && task ? (
      <span className="hint">
        {text(binding.options?.empty_hint) || (kind === "diagram"
          ? "не выбрана: граф возьмёт первую и скажет, какую"
          : "не выбран: конвейер прочитает папку целиком")}
      </span>
    ) : null}
    {chosen.length ? (
      <span className="hint">
        {multiple ? "выбрано в дереве файлов; клик по файлу добавляет и убирает" : "выбрано в дереве файлов"}
      </span>
    ) : null}
    {missing.map((name) => (
      <span className="error" key={name}>{name}: в папке {task} такого файла нет</span>
    ))}
    {hint ? <span className="hint">{hint}</span> : null}
  </div>;
}

function CostSummaryWidget({ value }: WidgetProps) {
  const cost = (value && typeof value === "object" ? value : {}) as CostSummary;
  return <dl className="key-value"><div><dt>стоимость</dt><dd>{formatUsd(cost.usd)}</dd></div><div><dt>вызовы</dt><dd>{formatTokens(cost.calls)}</dd></div><div><dt>вход</dt><dd>{formatTokens(cost.input)}</dd></div><div><dt>выход</dt><dd>{formatTokens(cost.output)}</dd></div><div><dt>cache hit</dt><dd>{typeof cost.hit_rate === "number" ? `${cost.hit_rate.toFixed(1)}%` : "—"}</dd></div></dl>;
}

function PublicationWidget({ value }: WidgetProps) {
  const publication = (value && typeof value === "object" ? value : {}) as { status?: string; reason?: string; pages?: Array<{ title?: string; url?: string; status?: string; reason?: string }> };
  const pages = publication.pages ?? [];
  if (!publication.status && !pages.length) return <span className="hint">Публикаций пока нет.</span>;
  return <div>{publication.status ? <StatusWidget {...({ value: publication.status === "drafts" ? "Черновики в Confluence" : publication.status } as WidgetProps)} /> : null}
    {publication.reason ? <p className="hint">{publication.reason}</p> : null}{pages.map((page, index) => {
    const href = page.url ? safeUrl(page.url, true) : null;
    return <div className="external-draft-result" key={index}>
      {href ? <a href={href} target="_blank" rel="noreferrer noopener">{page.status === "draft" ? "Открыть черновик: " : ""}{page.title ?? href}</a> : text(page.title)}
      {page.reason ? <p className={page.status === "failed" ? "error" : "hint"}>{page.reason}</p> : null}
    </div>;
  })}</div>;
}

/**
 * Опубликованные документы — как outline в редакторе: секция внизу колонки,
 * свёрнутая по умолчанию. Раньше список раскрывался сам и закрывал собой
 * выбор агента; файлы задачи важнее, поэтому они остаются наверху и всегда
 * видны, а сюда заглядывают по надобности. Состояние свёртки переживает
 * перерисовки: ключ в localStorage, как у ширины левой колонки.
 */
const PUBLISHED_OPEN_KEY = "orbita.published.open";

function PublishedListWidget({ value, binding, context, onAction }: WidgetProps) {
  const publication = (value && typeof value === "object" ? value : {}) as { status?: string; pages?: unknown[] };
  const [documents, setDocuments] = useState<Array<{ name: string; title: string; size: number }>>([]);
  const [open, setOpen] = useState(() => localStorage.getItem(PUBLISHED_OPEN_KEY) === "1");
  const [error, setError] = useState("");
  const resource = context.resource;
  useEffect(() => {
    let live = true;
    resource("orbita.publications", "list").then((response) => {
      const items = (response as { documents?: Array<{ name: string; title: string; size: number }> }).documents;
      if (live) setDocuments(Array.isArray(items) ? items : []);
    }).catch(() => undefined);
    return () => { live = false; };
  }, [resource, runtimePublicationKey(publication)]);
  const read = (name: string, title: string) =>
    resource("orbita.publications", "read", { name })
      .then((response) => {
        const document = response as { name: string; text: string };
        onAction?.({ kind: "publication.open", payload: { title: title || document.name, text: document.text } });
        setError("");
      })
      .catch((reason: Error) => setError(reason.message));
  const toggle = (next: boolean) => {
    setOpen(next);
    localStorage.setItem(PUBLISHED_OPEN_KEY, next ? "1" : "0");
  };
  return <details
    className="engine-outline"
    open={open}
    onToggle={(event) => toggle((event.currentTarget as HTMLDetailsElement).open)}
  >
    <summary>
      <span className="engine-outline-mark">{open ? "▾" : "▸"}</span>
      {binding.title ?? "Опубликованные документы"}
      <span className="hint">{documents.length}</span>
    </summary>
    {documents.length ? (
      <ul className="resource-list">{documents.map((item) => <li key={item.name}>
        <button onClick={() => read(item.name, item.title)}>{item.title || item.name}</button>
        <span className="hint">{formatBytes(item.size)}</span>
      </li>)}</ul>
    ) : <span className="hint">пока пусто</span>}
    {error ? <span className="error">{error}</span> : null}
  </details>;
}

function runtimePublicationKey(value: { status?: string; pages?: unknown[] }): string {
  return `${value.status ?? ""}:${value.pages?.length ?? 0}`;
}

function ErrorWidget({ value }: WidgetProps) { return <div className="error" role="alert">{text((value as { message?: unknown })?.message ?? value)}</div>; }
function UnknownWidget({ value, binding }: WidgetProps) { return <div className="widget-unknown"><b>Неизвестный widget: {binding.widget}</b><JsonWidget {...({ value } as WidgetProps)} /></div>; }

export const BUILTIN_WIDGETS: WidgetDefinition[] = [
  definition("text", TextWidget), definition("markdown", MarkdownWidget), definition("code", CodeWidget),
  definition("json", JsonWidget), definition("table", TableWidget), definition("key-value", KeyValueWidget),
  definition("number", NumberWidget), definition("money", MoneyWidget), definition("progress", ProgressWidget),
  definition("status", StatusWidget), definition("messages", MessagesWidget),
  definition("chat-input", ChatInputWidget, ["input"]), definition("form", FormWidget, ["input", "edit", "interrupt"]),
  definition("approval", ApprovalWidget, ["interrupt"]), definition("artifact-list", ArtifactListWidget),
  definition("draft-list", DraftListWidget, ["view"], true),
  definition("document-preview", DocumentPreviewWidget), definition("document-diff", DocumentDiffWidget),
  definition("file-list", FileListWidget), definition("task-picker", TaskPickerWidget, ["input"], true),
  definition("cost-summary", CostSummaryWidget), definition("publication", PublicationWidget),
  definition("jira-project", JiraProjectWidget, ["input"]), definition("issue-list", IssueListWidget),
  definition("file-picker", FilePickerWidget, ["input"]),
  definition("published-list", PublishedListWidget, ["view"], true),
  definition("error", ErrorWidget), definition("unknown", UnknownWidget),
];
