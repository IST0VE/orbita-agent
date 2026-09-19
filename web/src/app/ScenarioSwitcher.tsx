/**
 * Переключатель сценария в шапке и каталог за ним.
 *
 * В шапке — значок, название и шеврон: какой конвейер сейчас работает. За
 * шевроном — каталог, а не длинный список: у сценария есть название, строка
 * о том, что он делает, и раздел, в котором он лежит. Выпадающий `select`
 * этого не умеет — он умеет строку текста и системное синее выделение, и
 * восемь сценариев в нём читаются как восемь одинаковых пунктов меню.
 *
 * Клавиатура и закрытие по щелчку мимо собраны вручную, по роли `listbox`:
 * это цена своего списка, и платится она здесь один раз.
 */

import { useEffect, useRef, useState } from "react";

import type { Assistant } from "../api";
import { Check, ChevronDown, Clock } from "../ui/icons";
import {
  CATEGORY_ORDER,
  CATEGORY_TITLES,
  graphInfo,
  recentScenarios,
  scenarioCategory,
  scenarioIcon,
  type ScenarioCategory,
} from "./scenarios";

type Option = { assistant: Assistant; label: string; hint: string };
type Group = { id: string; title: string; icon?: boolean; options: Option[] };

export function ScenarioSwitcher({
  assistants,
  selected,
  onSelect,
  disabled,
  info = {},
}: {
  assistants: Assistant[];
  selected: string | null;
  onSelect: (id: string) => void;
  /** Во время прогона сценарий не переключается: тред принадлежит своему. */
  disabled: boolean;
  info?: Record<string, { label: string; hint: string }>;
}) {
  const [open, setOpen] = useState(false);
  /** Строка под клавишами — она же `aria-activedescendant` списка. */
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);

  const current = assistants.find((item) => item.assistant_id === selected);
  const chosen = graphInfo(current, "", info);
  const CurrentIcon = scenarioIcon(current?.graph_id ?? "");

  // Прогон начался — каталог закрыт: открытое меню, которое ничего не меняет,
  // обещает больше, чем может.
  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled]);

  // Щелчок мимо закрывает каталог. Именно `pointerdown`, а не `click`: щелчок
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

  // Фокус уходит в каталог: иначе стрелки листали бы страницу, а не сценарии.
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

  const options: Option[] = assistants.map((assistant) => {
    const { label, hint } = graphInfo(assistant, assistant.graph_id, info);
    return { assistant, label, hint };
  });

  const recent = recentScenarios()
    .map((graphId) => options.find((option) => option.assistant.graph_id === graphId))
    .filter((option): option is Option => Boolean(option));

  const groups: Group[] = [];
  // Недавние показываются, только когда они действительно сокращают путь:
  // один сценарий из двух — это не история, а повтор всего каталога.
  if (recent.length > 1 && options.length > recent.length) {
    groups.push({ id: "recent", title: "Недавние", icon: true, options: recent });
  }
  for (const category of CATEGORY_ORDER) {
    const inCategory = options.filter(
      (option) => scenarioCategory(option.assistant.graph_id) === (category as ScenarioCategory),
    );
    if (inCategory.length) {
      groups.push({ id: category, title: CATEGORY_TITLES[category], options: inCategory });
    }
  }

  /** Плоский порядок обхода клавишами: каталог сгруппирован, ходьба — нет. */
  const flat = groups.flatMap((group) => group.options.map((option) => ({ group: group.id, option })));
  const index = flat.findIndex((item) => item.option.assistant.assistant_id === selected);

  const show = () => {
    setActive(index >= 0 ? index : 0);
    setOpen(true);
  };

  const hide = () => {
    setOpen(false);
    trigger.current?.focus();
  };

  const choose = (position: number) => {
    const item = flat[position];
    if (item) onSelect(item.option.assistant.assistant_id);
    hide();
  };

  const step = (delta: number) =>
    setActive((value) => Math.min(flat.length - 1, Math.max(0, value + delta)));

  const listId = "scenario-catalog";

  return (
    <div className="pick" ref={root}>
      <button
        type="button"
        ref={trigger}
        className="pick-button"
        data-graph={current?.graph_id}
        aria-label="Сценарий"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        disabled={disabled}
        title={chosen.hint || "Выбрать сценарий"}
        // Каталог закрывается и по второму нажатию: иначе единственный способ
        // передумать — щёлкнуть мимо, а мимо здесь попадают в граф.
        onClick={() => (open ? setOpen(false) : show())}
        onKeyDown={(event) => {
          if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
          event.preventDefault();
          if (!disabled) show();
        }}
      >
        <CurrentIcon className="pick-icon" size={16} aria-hidden="true" />
        <span className="pick-current">{chosen.label || "Выберите сценарий"}</span>
        <ChevronDown className="pick-chevron" size={15} aria-hidden="true" />
      </button>

      {open ? (
        <div
          className="pick-menu"
          id={listId}
          role="listbox"
          tabIndex={-1}
          ref={list}
          aria-label="Каталог сценариев"
          aria-activedescendant={`pick-option-${active}`}
          onKeyDown={(event) => {
            switch (event.key) {
              case "ArrowDown": event.preventDefault(); step(1); break;
              case "ArrowUp": event.preventDefault(); step(-1); break;
              case "Home": event.preventDefault(); setActive(0); break;
              case "End": event.preventDefault(); setActive(flat.length - 1); break;
              case "Enter":
              case " ": event.preventDefault(); choose(active); break;
              case "Escape": event.preventDefault(); hide(); break;
              default: break;
            }
          }}
          // Уход фокуса наружу (Tab) закрывает каталог: открытое меню за
          // спиной — это состояние, о котором оператор уже не помнит.
          onBlur={(event) => {
            if (!root.current?.contains(event.relatedTarget as Node | null)) setOpen(false);
          }}
        >
          <p className="pick-menu-lead">Сценарий определяет, какой конвейер пойдёт со следующего запуска.</p>
          {groups.map((group) => (
            <div className="pick-group" key={group.id} role="group" aria-label={group.title}>
              <span className="eyebrow pick-group-title">
                {group.icon ? <Clock size={12} aria-hidden="true" /> : null}
                {group.title}
              </span>
              {group.options.map((option) => {
                const position = flat.findIndex(
                  (item) => item.group === group.id
                    && item.option.assistant.assistant_id === option.assistant.assistant_id,
                );
                const picked = option.assistant.assistant_id === selected;
                const Icon = scenarioIcon(option.assistant.graph_id);
                return (
                  <div
                    key={`${group.id}:${option.assistant.assistant_id}`}
                    id={`pick-option-${position}`}
                    className="pick-option"
                    data-graph={option.assistant.graph_id}
                    role="option"
                    aria-selected={picked}
                    data-active={position === active ? "true" : undefined}
                    onPointerEnter={() => setActive(position)}
                    onClick={() => choose(position)}
                  >
                    <span className="pick-option-icon"><Icon size={16} aria-hidden="true" /></span>
                    <span className="pick-option-text">
                      <span className="pick-option-label">{option.label}</span>
                      {option.hint ? <span className="pick-option-hint">{option.hint}</span> : null}
                    </span>
                    {picked ? <Check className="pick-check" size={16} aria-hidden="true" /> : null}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
