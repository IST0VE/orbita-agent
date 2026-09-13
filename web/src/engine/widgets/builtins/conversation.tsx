/**
 * Разговор и ввод: сообщения хода, поле запроса и формы.
 *
 * Объединены не по типу виджета, а по тому, с чем работает человек: это
 * единственная часть экрана, где он пишет, а не читает.
 */
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Message } from "@langchain/langgraph-sdk";
import { messageText, text } from "./shared";
import type { JsonSchema, JsonValue, WidgetProps } from "../../manifest/types";
import { schemaDefaults, validateForm } from "../formValidation";
import { JsonWidget } from "./primitives";

/** Вызовы инструментов отдельной строкой: без них не видно, что агент читал. */
export function toolCalls(message: unknown): Array<{ name?: string; args?: unknown }> {
  const calls = (message as { tool_calls?: unknown }).tool_calls;
  return Array.isArray(calls) ? (calls as Array<{ name?: string; args?: unknown }>) : [];
}


export const MessageRow = memo(function MessageRow({ message }: { message: Message }) {
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


export const MessagesWidget = memo(function MessagesWidget({ value }: WidgetProps) {
  const [limit, setLimit] = useState(50);
  const messages = Array.isArray(value) ? (value as Message[]) : [];
  if (!messages.length) return <div className="hint">Сообщений пока нет.</div>;
  const start = Math.max(0, messages.length - limit);
  return <div className="engine-messages">
    {start > 0 ? <button onClick={() => setLimit((count) => count + 50)}>[показать предыдущие · ещё {start}]</button> : null}
    {messages.slice(start).map((message, index) => <MessageRow message={message} key={message.id ?? start + index} />)}
  </div>;
}, (previous, next) => previous.value === next.value);


export function ChatInputWidget({ readonly, onAction }: WidgetProps) {
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


export function ArrayField({ value, schema, onChange, path, onError }: {
  value: unknown;
  schema: JsonSchema;
  onChange: (value: JsonValue) => void;
  path: string;
  onError: (path: string, error: string) => void;
}) {
  const serialized = JSON.stringify(value ?? [], null, 2);
  const [draft, setDraft] = useState(serialized);
  const lastValue = useRef(serialized);
  useEffect(() => {
    if (serialized !== lastValue.current) {
      lastValue.current = serialized;
      setDraft(serialized);
      onError(path, "");
    }
  }, [serialized, path, onError]);
  useEffect(() => () => onError(path, ""), [path, onError]);
  return <textarea aria-label={schema.title ?? path} value={draft} onChange={(event) => {
    const source = event.target.value;
    setDraft(source);
    try {
      const parsed = JSON.parse(source) as JsonValue;
      if (!Array.isArray(parsed)) throw new Error("Ожидался JSON-массив");
      lastValue.current = JSON.stringify(parsed, null, 2);
      onError(path, "");
      onChange(parsed);
    } catch {
      onError(path, "Введите корректный JSON-массив");
    }
  }} />;
}


export function fieldValue(value: unknown, schema: JsonSchema, onChange: (value: JsonValue) => void, path: string, onError: (path: string, error: string) => void) {
  if (schema.enum) {
    return <select aria-label={schema.title ?? path} value={JSON.stringify(value)} onChange={(event) => onChange(JSON.parse(event.target.value) as JsonValue)}>{schema.enum.map((item, index) => <option value={JSON.stringify(item)} key={index}>{text(item)}</option>)}</select>;
  }
  if (schema.type === "boolean") return <input aria-label={schema.title ?? path} type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(event.target.checked)} />;
  if (schema.type === "number" || schema.type === "integer") return <input aria-label={schema.title ?? path} type="number" value={typeof value === "number" ? value : 0} min={schema.minimum} max={schema.maximum} onChange={(event) => onChange(Number(event.target.value))} />;
  if (schema.type === "object" || schema.properties) {
    const record = value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, JsonValue> : {};
    return <fieldset>{Object.entries(schema.properties ?? {}).map(([key, child]) => <label key={key}>{child.title ?? key}{fieldValue(record[key] ?? schemaDefaults(child), child, (next) => onChange({ ...record, [key]: next }), `${path}.${key}`, onError)}</label>)}</fieldset>;
  }
  if (schema.type === "array") return <ArrayField value={value} schema={schema} onChange={onChange} path={path} onError={onError} />;
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
export function FormWidget({ binding, value, mode, readonly, onChange, onAction }: WidgetProps) {
  const schema = (binding.options?.schema ?? {}) as JsonSchema;
  const resuming = mode === "interrupt";
  const interruptId = text(binding.options?.interruptId);
  const ruleId = text(binding.options?.ruleId);
  const [draft, setDraft] = useState<JsonValue>(
    () => (resuming ? schemaDefaults(schema) : ((value as JsonValue) ?? schemaDefaults(schema))),
  );
  const [parseErrors, setParseErrors] = useState<Record<string, string>>({});
  const onParseError = useCallback((path: string, error: string) => {
    setParseErrors((previous) => {
      if ((previous[path] ?? "") === error) return previous;
      const next = { ...previous };
      if (error) next[path] = error;
      else delete next[path];
      return next;
    });
  }, []);
  const errors = useMemo(() => ({ ...validateForm(draft, schema), ...parseErrors }), [draft, schema, parseErrors]);
  const submit = () => onAction?.(
    resuming
      ? { kind: "interrupt.resume", interruptId, ruleId, payload: draft }
      : { kind: "run.start", payload: draft },
  );
  return <form onSubmit={(event) => { event.preventDefault(); if (!readonly && !Object.keys(errors).length) submit(); }}>
    {resuming ? <JsonWidget {...({ value } as WidgetProps)} /> : null}
    <fieldset className="form-fields" disabled={readonly}>
      {fieldValue(draft, schema, (next) => { setDraft(next); onChange?.(next); }, "$", onParseError)}
    </fieldset>
    {Object.keys(errors).length ? <ul className="error">{Object.entries(errors).map(([path, message]) => <li key={path}>{path}: {message}</li>)}</ul> : null}
    <button disabled={readonly || !!Object.keys(errors).length}>[отправить]</button>
  </form>;
}
