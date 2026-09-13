/**
 * Результат прогона: итог, документы, задачи, стоимость и публикация.
 *
 * То, ради чего оператор открывает экран после прогона. Итог и вердикт
 * считает сервер (`agent/summary.py`, `agent/nt/assessment.py`) — здесь
 * только подача.
 */
import { useEffect, useState } from "react";
import { formatBytes, formatCost, formatTokens, type CostSummary } from "../../../lib/orbita";
import { text } from "./shared";
import { SafeMarkdown } from "../../security/safeMarkdown";
import { safeUrl } from "../../security/safeUrl";
import type { WidgetProps } from "../../manifest/types";
import { DraftListWidget, type Draft } from "./approval";
import { StatusWidget, TableWidget } from "./primitives";

export function ArtifactListWidget({ value }: WidgetProps) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return <div className="hint">Документов пока нет.</div>;
  return <div>{Object.entries(value).map(([name, document]) => <details key={name}><summary>{name}</summary><SafeMarkdown value={document} /></details>)}</div>;
}


export function DocumentPreviewWidget({ value }: WidgetProps) { return <SafeMarkdown value={value} />; }


export function DocumentDiffWidget({ value }: WidgetProps) { return <pre>{JSON.stringify(value, null, 2)}</pre>; }


export function FileListWidget({ value }: WidgetProps) { return <TableWidget {...({ value } as WidgetProps)} />; }


/** Заведённые задачи: ключ, заголовок и ссылка в трекер. */
export function IssueListWidget({ value }: WidgetProps) {
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


/**
 * Итог хода: что получилось, что мешает и что делать дальше.
 *
 * Три строки, ради которых оператор открывает экран. Считает их сервер
 * (`agent/summary.py`): правило одно на все интерфейсы, а собирать итог здесь
 * значило бы получать состояние целиком — вместе с историей сообщений, к
 * итогу отношения не имеющей, — и пересчитывать его на каждом кадре.
 */
export function RunSummaryWidget({ value }: WidgetProps) {
  const summary = (value && typeof value === "object" ? value : {}) as {
    outcome?: string[]; problems?: string[]; next?: string[];
  };
  const outcome = summary.outcome ?? [];
  const problems = summary.problems ?? [];
  const next = summary.next ?? [];
  if (!outcome.length && !problems.length && !next.length) {
    return <span className="hint">Итога ещё нет.</span>;
  }
  return <div className="run-summary">
    {outcome.length ? <p className="run-summary-outcome">{outcome.join(" · ")}</p> : null}
    {problems.length ? <ul className="run-summary-problems">
      {problems.map((item, index) => <li key={index}>{text(item)}</li>)}
    </ul> : null}
    {next.length ? <p className="run-summary-next"><b>Дальше:</b> {next.map(text).join(" ")}</p> : null}
  </div>;
}


/** Вердикт НТ читаемой сводкой: чем измерен и почему именно такой. */
export function VerdictWidget({ value }: WidgetProps) {
  const verdict = (value && typeof value === "object" ? value : {}) as {
    result?: string; service?: string; environment?: string; namespace?: string;
    period?: { from?: string; to?: string; seconds?: number };
    sources?: string[]; simulated_sources?: string[]; metrics?: string[];
    basis?: string; reasons?: string[];
    capacity?: { status?: string; maximum_stable_rps?: number | null; plateaus?: number };
  };
  if (!verdict.result) return <span className="hint">Вердикт ещё не вычислен.</span>;
  const period = verdict.period ?? {};
  const rows: Array<[string, string]> = [
    ["сервис", [verdict.service, verdict.environment, verdict.namespace].filter(Boolean).join(" / ") || "—"],
    ["период", `${text(period.from) || "—"} — ${text(period.to) || "—"}`],
    ["источники", (verdict.sources ?? []).join(", ") || "нет"],
    ["метрики", (verdict.metrics ?? []).join(", ") || "нет"],
    ["чем измерен", verdict.basis === "whole_run_maxima" ? "максимумы рядов за весь период, не плато" : text(verdict.basis)],
    ["устойчивая RPS", `${verdict.capacity?.maximum_stable_rps ?? "—"} (${text(verdict.capacity?.status) || "INCONCLUSIVE"})`],
  ];
  if ((verdict.simulated_sources ?? []).length) {
    rows.push(["симулированные источники", (verdict.simulated_sources ?? []).join(", ")]);
  }
  return <div className="nt-verdict">
    <StatusWidget {...({ value: verdict.result } as WidgetProps)} />
    <dl className="key-value">
      {rows.map(([label, item]) => <div key={label}><dt>{label}</dt><dd>{item}</dd></div>)}
    </dl>
    {(verdict.reasons ?? []).length ? <ul className="run-summary-problems">
      {(verdict.reasons ?? []).map((item, index) => <li key={index}>{item}</li>)}
    </ul> : null}
  </div>;
}


export function CostSummaryWidget({ value }: WidgetProps) {
  const cost = (value && typeof value === "object" ? value : {}) as CostSummary;
  const money = formatCost(cost);
  return <dl className="key-value"><div><dt>стоимость</dt><dd title={money.hint}>{money.text}</dd></div><div><dt>вызовы</dt><dd>{formatTokens(cost.calls)}</dd></div><div><dt>вход</dt><dd>{formatTokens(cost.input)}</dd></div><div><dt>выход</dt><dd>{formatTokens(cost.output)}</dd></div><div><dt>cache hit</dt><dd>{typeof cost.hit_rate === "number" ? `${cost.hit_rate.toFixed(1)}%` : "—"}</dd></div></dl>;
}


export function PublicationWidget({ value }: WidgetProps) {
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
export const PUBLISHED_OPEN_KEY = "orbita.published.open";


export function PublishedListWidget({ value, binding, context, onAction }: WidgetProps) {
  const publication = (value && typeof value === "object" ? value : {}) as { status?: string; pages?: unknown[] };
  const [documents, setDocuments] = useState<Array<{ name: string; title: string; size: number }>>([]);
  const [open, setOpen] = useState(() => localStorage.getItem(PUBLISHED_OPEN_KEY) === "1");
  const [error, setError] = useState("");
  const [listError, setListError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const resource = context.resource;
  useEffect(() => {
    let live = true;
    setLoading(true);
    setListError("");
    resource("orbita.publications", "list").then((response) => {
      const items = (response as { documents?: Array<{ name: string; title: string; size: number }> }).documents;
      if (live) setDocuments(Array.isArray(items) ? items : []);
    }).catch((reason: Error) => {
      if (live) { setDocuments([]); setListError(reason.message); }
    }).finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [resource, runtimePublicationKey(publication), revision]);
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
      <span className={listError ? "error" : "hint"}>{loading ? "загрузка…" : listError ? "ошибка загрузки" : documents.length}</span>
    </summary>
    {documents.length ? (
      <ul className="resource-list">{documents.map((item) => <li key={item.name}>
        <button onClick={() => read(item.name, item.title)}>{item.title || item.name}</button>
        <span className="hint">{formatBytes(item.size)}</span>
      </li>)}</ul>
    ) : !loading && !listError ? <span className="hint">пока пусто</span> : null}
    {listError ? <div className="error" role="alert">Не удалось загрузить публикации: {listError}</div> : null}
    <button disabled={loading} onClick={() => setRevision((current) => current + 1)}>
      [{loading ? "загрузка…" : listError ? "повторить загрузку" : "обновить список"}]
    </button>
    {error ? <span className="error">{error}</span> : null}
  </details>;
}


export function runtimePublicationKey(value: { status?: string; pages?: unknown[] }): string {
  // Ключ перезапроса списка, а не снимок отчёта: файлы на сервере меняет сама
  // публикация, а не поля внутри её результата. Полный JSON гонял бы запрос на
  // каждое вложенное изменение; за остальные случаи отвечает `[обновить список]`.
  return `${value.status ?? ""}:${value.pages?.length ?? 0}`;
}
