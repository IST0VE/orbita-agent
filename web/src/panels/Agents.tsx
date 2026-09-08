/**
 * Выбор графа: список в правой колонке и та же развилка в шапке.
 *
 * Список приходит из `/assistants/search`, то есть из `langgraph.json`.
 * Дописали туда новый граф — он появится здесь сам, без правки фронта.
 *
 * Развилка продублирована в шапку не для красоты: правая колонка уезжает на
 * узком экране, а выбрать конвейер надо в любом окне — это первое решение
 * оператора, до всякого вопроса.
 */

import type { Assistant } from "../api";
import { Panel } from "../ui";

export function sortAssistants(list: Assistant[]): Assistant[] {
  return [...list].sort((a, b) => a.graph_id.localeCompare(b.graph_id));
}

export function graphInfo(
  a: Assistant | undefined,
  fallback = "",
  info: Record<string, { label: string; hint: string }> = {},
) {
  if (!a) return { label: fallback, hint: "" };
  return info[a.graph_id] ?? {
    label: a.name || a.graph_id,
    hint: a.description || `граф ${a.graph_id}`,
  };
}

/** Развилка в шапке: какой конвейер пойдёт со следующего вопроса. */
export function AgentPicker({
  assistants,
  selected,
  onSelect,
  disabled,
  info = {},
}: {
  assistants: Assistant[];
  selected: string | null;
  onSelect: (id: string) => void;
  /** Во время хода граф не переключается: тред принадлежит своему конвейеру. */
  disabled: boolean;
  info?: Record<string, { label: string; hint: string }>;
}) {
  if (!assistants.length) return null;
  return (
    <label className="pick">
      <span className="hint">агент:</span>
      <select aria-label="Агент" value={selected ?? ""} disabled={disabled} onChange={(event) => onSelect(event.target.value)}>
      {assistants.map((a) => {
        const { label, hint } = graphInfo(a, a.graph_id, info);
        return (
          <option
            key={a.assistant_id}
            value={a.assistant_id}
            title={hint}
          >
            {label}
          </option>
        );
      })}
      </select>
    </label>
  );
}

export function AgentsPanel({
  assistants,
  selected,
  onSelect,
  error,
  info = {},
}: {
  assistants: Assistant[];
  selected: string | null;
  onSelect: (id: string) => void;
  error: string | null;
  info?: Record<string, { label: string; hint: string }>;
}) {
  return (
    <Panel
      title="АГЕНТЫ"
      right={<span className="hint">{assistants.length}</span>}
    >
      {error ? <div className="error">{error}</div> : null}
      {!assistants.length && !error ? (
        <div className="hint">сервер не отдал ни одного графа</div>
      ) : null}

      {assistants.map((a) => {
        const id = a.assistant_id;
        const active = selected === id;
        const { label, hint } = graphInfo(a, a.graph_id, info);
        return (
          <button
            key={id}
            className={`agent ${active ? "selected" : ""}`}
            onClick={() => onSelect(id)}
            title={hint}
          >
            <span className="mark">{active ? "◆" : "◇"}</span> {label}
            <span className="meta">{a.graph_id}</span>
          </button>
        );
      })}
    </Panel>
  );
}
