/**
 * Результат прогона: итог, документы, задачи, стоимость и публикация.
 *
 * То, ради чего оператор открывает экран после прогона. Итог и вердикт
 * считает сервер (`agent/summary.py`, `agent/nt/assessment.py`) — здесь
 * только подача.
 */
import { useEffect, useState } from "react";
import {
  formatBytes,
  formatCost,
  formatTokens,
  formatUsd,
  publicationLabel,
  savingRatio,
  type CostSummary,
  type Publication,
} from "../../../lib/orbita";
import { text } from "./shared";
import { SafeMarkdown } from "../../security/safeMarkdown";
import { safeUrl } from "../../security/safeUrl";
import type { WidgetProps } from "../../manifest/types";
import { DraftListWidget, type Draft } from "./approval";
import { StatusWidget, TableWidget } from "./primitives";
import {
  ChevronDown,
  ChevronRight,
  CircleAlert,
  ExternalLink,
  FileText,
  Inbox,
  RotateCcw,
} from "../../../ui/icons";
import { EmptyState, Meter } from "../../../ui";

export function ArtifactListWidget({ value }: WidgetProps) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return <span className="hint">Документов пока нет.</span>;
  }
  const entries = Object.entries(value);
  if (!entries.length) return <span className="hint">Документов пока нет.</span>;
  return <div className="artifact-list">{entries.map(([name, document]) => (
    <details key={name}>
      <summary>
        <ChevronRight size={14} aria-hidden="true" />
        <FileText size={14} aria-hidden="true" />
        {name}
      </summary>
      <SafeMarkdown value={document} />
    </details>
  ))}</div>;
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
  if (!result) return <span className="hint">Задачи ещё не заводились.</span>;
  const created = result.created ?? [];
  return <div className="issue-list">
    <div className="issue-list-head">
      <StatusWidget {...({ value: result.status } as WidgetProps)} />
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
    ["Сервис", [verdict.service, verdict.environment, verdict.namespace].filter(Boolean).join(" / ") || "—"],
    ["Период", `${text(period.from) || "—"} — ${text(period.to) || "—"}`],
    ["Источники", (verdict.sources ?? []).join(", ") || "нет"],
    ["Метрики", (verdict.metrics ?? []).join(", ") || "нет"],
    ["Чем измерен", verdict.basis === "whole_run_maxima" ? "максимумы рядов за весь период, не плато" : text(verdict.basis)],
    ["Устойчивая RPS", `${verdict.capacity?.maximum_stable_rps ?? "—"} (${text(verdict.capacity?.status) || "INCONCLUSIVE"})`],
  ];
  if ((verdict.simulated_sources ?? []).length) {
    rows.push(["Симулированные источники", (verdict.simulated_sources ?? []).join(", ")]);
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


/**
 * Стоимость треда: сумма, расшифровка и две полосы.
 *
 * Полосы отвечают на вопросы, ради которых в эту карточку и смотрят: сколько
 * осталось до лимита и работает ли кеш. Числа под ними остаются — по полосе
 * видно долю, а списывают деньги по числу.
 *
 * Бюджетная полоса появляется только при заданном лимите: `limit_usd` равный
 * нулю означает «без лимита», и полоса «ноль из нуля» врала бы о существовании
 * границы, которой нет.
 */
export function CostSummaryWidget({ value }: WidgetProps) {
  const cost = (value && typeof value === "object" ? value : {}) as CostSummary;
  const money = formatCost(cost);
  const saving = savingRatio(cost);
  const limit = Number(cost.limit_usd) || 0;
  const spent = Number(cost.usd) || 0;
  const share = limit > 0 ? spent / limit : 0;
  const hit = typeof cost.hit_rate === "number" ? cost.hit_rate : null;
  return <div className="cost-summary">
    <div className="cost-headline">
      <span className="cost-amount" title={money.hint}>{money.text}</span>
      {saving ? <span className="badge badge-teal">кеш срезал счёт в {saving.toFixed(1)}×</span> : null}
    </div>
    <dl className="key-value">
      <div><dt>Вызовы</dt><dd>{formatTokens(cost.calls)}</dd></div>
      <div><dt>Вход</dt><dd>{formatTokens(cost.input)}</dd></div>
      <div><dt>Выход</dt><dd>{formatTokens(cost.output)}</dd></div>
    </dl>
    {limit > 0 ? (
      <Meter
        label="Бюджет треда"
        value={share}
        caption={`${formatUsd(spent)} из ${formatUsd(limit)}`}
        tone={share >= 0.9 ? "bad" : share >= 0.7 ? "warn" : ""}
      />
    ) : null}
    {hit !== null ? (
      <Meter label="Попадания в кеш" value={hit / 100} caption={`${hit.toFixed(1)}%`} />
    ) : null}
  </div>;
}


export function PublicationWidget({ value }: WidgetProps) {
  const publication = (value && typeof value === "object" ? value : {}) as Publication;
  const pages = publication.pages ?? [];
  if (!publication.status && !pages.length) {
    return <span className="hint">Публикаций пока нет.</span>;
  }
  // Класс берётся из сырого статуса, а подпись — из словаря: статус это ключ
  // (`stale`, `partial`), и подставить в класс его перевод значит получить
  // `status-уехало не всё` — два класса вместо одного.
  const label = publication.status ? publicationLabel(publication) : null;
  return <div className="publication">
    {label ? (
      <span className={`runtime-status status-${publication.status}`}>
        <span className="dot" aria-hidden="true" />
        {label.text}
      </span>
    ) : null}
    {publication.reason ? <p className="hint">{publication.reason}</p> : null}
    {pages.map((page, index) => {
      const href = page.url ? safeUrl(page.url, true) : null;
      return <div className="external-draft-result" key={index}>
        {href ? (
          <a href={href} target="_blank" rel="noreferrer noopener">
            {page.status === "draft" ? "Открыть черновик: " : ""}{page.title ?? href}
            <ExternalLink size={13} aria-hidden="true" />
          </a>
        ) : text(page.title)}
        {page.reason ? <p className={page.status === "failed" ? "error" : "hint"}>{page.reason}</p> : null}
      </div>;
    })}
  </div>;
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
      <span className="engine-outline-mark">
        {open ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
      </span>
      {binding.title ?? "Опубликованные документы"}
      <span className={listError ? "error" : "hint"}>{loading ? "загрузка…" : listError ? "ошибка загрузки" : documents.length}</span>
    </summary>
    {documents.length ? (
      <ul className="resource-list">{documents.map((item) => <li key={item.name}>
        <button onClick={() => read(item.name, item.title)}>
          <span className="name">
            <span className="mark"><FileText size={14} aria-hidden="true" /></span>
            {item.title || item.name}
          </span>
        </button>
        <span className="hint">{formatBytes(item.size)}</span>
      </li>)}</ul>
    ) : !loading && !listError ? (
      <EmptyState icon={Inbox} title="Пока пусто" hint="Опубликованные документы появятся здесь после публикации." />
    ) : null}
    {listError ? (
      <div className="error" role="alert">
        <CircleAlert size={15} aria-hidden="true" />
        <span>Не удалось загрузить публикации: {listError}</span>
      </div>
    ) : null}
    <button className="btn-ghost btn-sm" disabled={loading} onClick={() => setRevision((current) => current + 1)}>
      <RotateCcw size={14} aria-hidden="true" />
      {loading ? "Загрузка…" : listError ? "Повторить загрузку" : "Обновить список"}
    </button>
    {error ? <span className="error">{error}</span> : null}
  </details>;
}


export function runtimePublicationKey(value: { status?: string; pages?: unknown[] }): string {
  // Ключ перезапроса списка, а не снимок отчёта: файлы на сервере меняет сама
  // публикация, а не поля внутри её результата. Полный JSON гонял бы запрос на
  // каждое вложенное изменение; за остальные случаи отвечает «Обновить список».
  return `${value.status ?? ""}:${value.pages?.length ?? 0}`;
}
