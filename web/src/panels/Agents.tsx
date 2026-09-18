/**
 * Выбор конвейера: развилка в шапке приложения.
 *
 * Список приходит из `/assistants/search`, то есть из `langgraph.json`.
 * Дописали туда новый граф — он появится здесь сам, без правки фронта.
 *
 * Развилка стоит рядом с логотипом, а не в колонке: это первое решение
 * оператора, до всякого вопроса, и оно должно быть видно в любом окне —
 * в том числе в том, где боковые колонки уже свёрнуты.
 *
 * Здесь не `select`, хотя выбор один из списка. Нативный список рисует
 * операционная система: своим шрифтом, своим синим выделением и без места
 * для пояснения — на тёплом фоне он выглядит чужой деталью ровно в том
 * месте, куда смотрят первым. Поэтому список свой: он берёт цвета из
 * дизайн-системы и показывает под названием конвейера, что тот делает.
 * Цена — клавиатура и закрытие по щелчку мимо, которые нативный список даёт
 * даром; они собраны ниже вручную, по роли `listbox`.
 */

import { useEffect, useRef, useState } from "react";

import type { Assistant } from "../api";
import { Check, ChevronDown } from "../ui/icons";

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
  const [open, setOpen] = useState(false);
  /** Строка под клавишами — она же `aria-activedescendant` списка. */
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);

  const index = assistants.findIndex((item) => item.assistant_id === selected);
  const current = index >= 0 ? assistants[index] : undefined;
  const chosen = graphInfo(current, "", info);

  // Прогон начался — выбор закрыт: открытое меню, которое ничего не меняет,
  // обещает больше, чем может.
  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled]);

  // Щелчок мимо закрывает список. Именно `pointerdown`, а не `click`: щелчок
  // по кнопке в шапке должен и закрыть меню, и сработать, а не потратиться
  // на закрытие.
  useEffect(() => {
    if (!open) return;
    const away = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [open]);

  // Фокус уходит в список: иначе стрелки листали бы страницу, а не варианты.
  useEffect(() => {
    if (open) list.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    list.current
      ?.querySelector<HTMLElement>('[data-active="true"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [open, active]);

  if (!assistants.length) return null;

  const show = () => {
    setActive(index >= 0 ? index : 0);
    setOpen(true);
  };

  const hide = () => {
    setOpen(false);
    trigger.current?.focus();
  };

  const choose = (position: number) => {
    const item = assistants[position];
    if (item) onSelect(item.assistant_id);
    hide();
  };

  const step = (delta: number) =>
    setActive((value) => Math.min(assistants.length - 1, Math.max(0, value + delta)));

  const onListKeys = (event: React.KeyboardEvent) => {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        step(1);
        break;
      case "ArrowUp":
        event.preventDefault();
        step(-1);
        break;
      case "Home":
        event.preventDefault();
        setActive(0);
        break;
      case "End":
        event.preventDefault();
        setActive(assistants.length - 1);
        break;
      case "Enter":
      case " ":
        event.preventDefault();
        choose(active);
        break;
      case "Escape":
        event.preventDefault();
        hide();
        break;
      default:
        break;
    }
  };

  const onTriggerKeys = (event: React.KeyboardEvent) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    if (!disabled) show();
  };

  const listId = "pick-list";

  return (
    <div className="pick" ref={root}>
      <button
        type="button"
        ref={trigger}
        className="pick-button"
        data-graph={current?.graph_id}
        aria-label="Конвейер"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        disabled={disabled}
        title={chosen.hint}
        // Меню закрывается и по второму нажатию: иначе единственный способ
        // передумать — щёлкнуть мимо, а мимо здесь попадают в граф.
        onClick={() => (open ? setOpen(false) : show())}
        onKeyDown={onTriggerKeys}
      >
        <span className="pick-current">{chosen.label || "Выберите конвейер"}</span>
        <ChevronDown className="pick-chevron" size={16} aria-hidden="true" />
      </button>

      {open ? (
        <div
          className="pick-menu"
          id={listId}
          role="listbox"
          tabIndex={-1}
          ref={list}
          aria-label="Конвейер"
          aria-activedescendant={`pick-option-${active}`}
          onKeyDown={onListKeys}
          // Уход фокуса наружу (Tab) закрывает список: открытое меню за
          // спиной — это состояние, о котором оператор уже не помнит.
          onBlur={(event) => {
            if (!root.current?.contains(event.relatedTarget as Node | null)) setOpen(false);
          }}
        >
          {assistants.map((a, position) => {
            const { label, hint } = graphInfo(a, a.graph_id, info);
            const picked = a.assistant_id === selected;
            return (
              <div
                key={a.assistant_id}
                id={`pick-option-${position}`}
                className="pick-option"
                data-graph={a.graph_id}
                role="option"
                aria-selected={picked}
                data-active={position === active ? "true" : undefined}
                onPointerEnter={() => setActive(position)}
                onClick={() => choose(position)}
              >
                <span className="pick-option-text">
                  <span className="pick-option-label">{label}</span>
                  {hint ? <span className="pick-option-hint">{hint}</span> : null}
                </span>
                {picked ? <Check className="pick-check" size={16} aria-hidden="true" /> : null}
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
