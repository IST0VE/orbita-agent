/**
 * Простые виджеты: значение и его подача.
 *
 * Ничего не знают ни о графе, ни о состоянии — только о том, что им передали.
 * Поэтому здесь же живут запасные `error` и `unknown`: они тоже про подачу,
 * а не про смысл.
 */
import { useState } from "react";

import { text } from "./shared";
import { SafeMarkdown } from "../../security/safeMarkdown";
import type { WidgetProps } from "../../manifest/types";
import { ChevronDown, ChevronRight, CircleAlert, Copy, Inbox, TriangleAlert } from "../../../ui/icons";
import { EmptyState } from "../../../ui";

export function TextWidget({ value }: WidgetProps) {
  return <span>{text(value)}</span>;
}


export function MarkdownWidget({ value }: WidgetProps) {
  return <SafeMarkdown value={value} />;
}


export function CodeWidget({ value, binding }: WidgetProps) {
  const source = text(value);
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard?.writeText(source).then(
      () => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1600);
      },
      () => setCopied(false),
    );
  };
  return (
    <div>
      <button className="btn-ghost btn-sm" onClick={copy}>
        <Copy size={14} aria-hidden="true" />
        {copied ? "Скопировано" : "Копировать"}
      </button>
      <pre data-language={text(binding.options?.language)}>{source}</pre>
    </div>
  );
}


export function JsonWidget({ value }: WidgetProps) {
  const [open, setOpen] = useState(false);
  return (
    <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>
        {open
          ? <ChevronDown size={14} aria-hidden="true" />
          : <ChevronRight size={14} aria-hidden="true" />}
        Подробности JSON
      </summary>
      {open ? <pre className="json-preview">{JSON.stringify(value, null, 2)}</pre> : null}
    </details>
  );
}


export function TableWidget({ value }: WidgetProps) {
  const rows = Array.isArray(value) ? value.filter((row) => row && typeof row === "object") : [];
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row as Record<string, unknown>)))].slice(0, 50);
  if (!rows.length) return <EmptyState icon={Inbox} title="Строк нет" />;
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


export function KeyValueWidget({ value }: WidgetProps) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return <JsonWidget {...({ value } as WidgetProps)} />;
  return <dl className="key-value">{Object.entries(value).map(([key, item]) => <div key={key}><dt>{key}</dt><dd>{text(item)}</dd></div>)}</dl>;
}


export function NumberWidget({ value, binding, context }: WidgetProps) {
  const number = typeof value === "number" ? value : Number(value);
  return <span>{Number.isFinite(number) ? new Intl.NumberFormat(context.locale, binding.options as Intl.NumberFormatOptions).format(number) : "—"}</span>;
}


export function MoneyWidget({ value, binding, context }: WidgetProps) {
  const number = typeof value === "number" ? value : Number(value);
  const currency = text(binding.options?.currency) || "USD";
  return <span>{Number.isFinite(number) ? new Intl.NumberFormat(context.locale, { style: "currency", currency }).format(number) : "—"}</span>;
}


export function ProgressWidget({ value, binding }: WidgetProps) {
  const number = typeof value === "number" ? value : Number(value);
  const max = Number(binding.options?.max ?? 100);
  return <progress value={Number.isFinite(number) ? number : 0} max={Number.isFinite(max) && max > 0 ? max : 100} />;
}


/**
 * Состояние словом.
 *
 * Значения приходят с сервера как есть (`ok`, `stale`, `failed`), и оттенок
 * плашки выбирает таблица стилей по классу `status-<значение>`: переводить
 * незнакомое состояние в цвет догадкой — значит однажды покрасить отказ
 * зелёным.
 */
export function StatusWidget({ value }: WidgetProps) {
  const label = text(value);
  return (
    <span className={`runtime-status status-${label.toLowerCase()}`}>
      <span className="dot" aria-hidden="true" />
      {label || "неизвестно"}
    </span>
  );
}


export function ErrorWidget({ value }: WidgetProps) {
  return (
    <div className="error" role="alert">
      <CircleAlert size={16} aria-hidden="true" />
      <span>{text((value as { message?: unknown })?.message ?? value)}</span>
    </div>
  );
}


export function UnknownWidget({ value, binding }: WidgetProps) {
  return (
    <div className="widget-unknown">
      <b>
        <TriangleAlert size={15} aria-hidden="true" /> Неизвестный виджет: {binding.widget}
      </b>
      <JsonWidget {...({ value } as WidgetProps)} />
    </div>
  );
}
