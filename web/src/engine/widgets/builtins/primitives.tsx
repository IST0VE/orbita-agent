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

export function TextWidget({ value }: WidgetProps) {
  return <span>{text(value)}</span>;
}


export function MarkdownWidget({ value }: WidgetProps) {
  return <SafeMarkdown value={value} />;
}


export function CodeWidget({ value, binding }: WidgetProps) {
  const source = text(value);
  return (
    <div>
      <button onClick={() => navigator.clipboard?.writeText(source)}>[копировать]</button>
      <pre data-language={text(binding.options?.language)}>{source}</pre>
    </div>
  );
}


export function JsonWidget({ value }: WidgetProps) {
  const [open, setOpen] = useState(false);
  return (
    <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>JSON</summary>
      {open ? <pre className="json-preview">{JSON.stringify(value, null, 2)}</pre> : null}
    </details>
  );
}


export function TableWidget({ value }: WidgetProps) {
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


export function StatusWidget({ value }: WidgetProps) {
  return <span className={`runtime-status status-${text(value).toLowerCase()}`}>{text(value) || "unknown"}</span>;
}


export function ErrorWidget({ value }: WidgetProps) { return <div className="error" role="alert">{text((value as { message?: unknown })?.message ?? value)}</div>; }


export function UnknownWidget({ value, binding }: WidgetProps) { return <div className="widget-unknown"><b>Неизвестный widget: {binding.widget}</b><JsonWidget {...({ value } as WidgetProps)} /></div>; }
