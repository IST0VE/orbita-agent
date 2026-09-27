/**
 * Всплывающее меню: вторичные действия, убранные с глаз.
 *
 * Одна деталь на весь продукт — меню графа, меню профиля, меню строки
 * результата. Отдельный компонент, а не три похожих `useState(open)`, потому
 * что цена меню не в раскрытии, а в мелочах вокруг: щелчок мимо закрывает,
 * Escape закрывает и возвращает фокус, стрелки ходят по пунктам, Tab наружу
 * закрывает. Каждая из них забывается ровно один раз на реализацию.
 *
 * Модель фокуса здесь одна, и это настоящий фокус браузера. Раньше их было
 * две: пункты оставались обычными кнопками в порядке обхода, а Enter на
 * контейнере выполнял пункт по внутреннему счётчику `active`, который Tab не
 * двигал. Два нажатия Tab и Enter открывали не тот раздел, на который смотрел
 * оператор. Теперь по списку ходит сам фокус (roving tabindex), `active` —
 * только его отражение для подсветки, а Enter и пробел достаются
 * сфокусированной кнопке, как любой другой кнопке на экране.
 */
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import type { LucideIcon } from "./icons";

export type MenuItem = {
  id: string;
  label: string;
  icon?: LucideIcon;
  hint?: string;
  disabled?: boolean;
  /** Пункт-переключатель: рисуется галкой и объявляется как `menuitemcheckbox`. */
  checked?: boolean;
  onSelect: () => void;
};

export function Menu({
  label,
  items,
  trigger,
  align = "end",
  className = "",
}: {
  /** Имя меню для экранного диктора и подсказка кнопки. */
  label: string;
  items: MenuItem[];
  /** Содержимое кнопки: значок, значок с текстом, аватар. */
  trigger: ReactNode;
  align?: "start" | "end";
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  /** Отражение фокуса: какой пункт табулируем и подсвечен. */
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  /** Кнопки пунктов по их порядку среди доступных: по ним ходит фокус. */
  const options = useRef<Array<HTMLButtonElement | null>>([]);
  const enabled = items.filter((item) => !item.disabled);
  /*
   * Пункт может стать недоступным, пока меню открыто: мини-карта выключается
   * вместе с переходом к списку. Табулируемым должен остаться существующий
   * пункт, иначе в меню не остаётся ни одного элемента в порядке обхода.
   */
  const focused = Math.min(active, Math.max(0, enabled.length - 1));
  /** Набор доступных пунктов: по его смене фокус сверяется заново. */
  const itemsKey = enabled.map((item) => item.id).join("\n");

  const hide = useCallback((restoreFocus = true) => {
    setOpen(false);
    if (restoreFocus) button.current?.focus();
  }, []);

  useEffect(() => {
    if (!open) return;
    const away = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [open]);

  // Открытие уводит фокус в список: иначе стрелки листали бы страницу, а не
  // пункты, и первое же нажатие Enter ушло бы кнопке-триггеру. Наводится он
  // один раз на раскрытие, дальше фокусом двигают стрелки и мышь.
  //
  // Кроме одного случая: набор пунктов сменился при открытом меню. Меню
  // профиля открывают раньше, чем сервер назовёт роль, и с ответом пункт
  // «Мои подключения» уступает место «Настройкам сервера». Сфокусированный
  // пункт остался — подсветка идёт за ним на его новое место. Исчез — фокус
  // встаёт на пункт, занявший его место; раньше он падал на страницу, и меню
  // оставалось открытым, но стрелки листали страницу.
  //
  // До отрисовки, а не после: между кадром с новым набором и фокусом нажатие
  // клавиши досталось бы не тому элементу.
  useLayoutEffect(() => {
    if (!open) return;
    const nodes = options.current.slice(0, enabled.length);
    const kept = nodes.findIndex((node) => node !== null && node === document.activeElement);
    if (kept >= 0) setActive(kept);
    else nodes[focused]?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, itemsKey]);

  const show = (position: number) => {
    setActive(position);
    setOpen(true);
  };

  const choose = (item: MenuItem | undefined) => {
    if (!item || item.disabled) return;
    hide();
    item.onSelect();
  };

  /** Фокус переезжает на соседний доступный пункт; за краями списка стоит. */
  const step = (delta: number) => {
    const next = Math.min(enabled.length - 1, Math.max(0, focused + delta));
    options.current[next]?.focus();
  };

  return (
    <div className={`menu ${className}`.trim()} ref={root}>
      <button
        ref={button}
        type="button"
        className="btn-ghost btn-icon menu-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        title={label}
        onClick={() => (open ? setOpen(false) : show(0))}
        onKeyDown={(event) => {
          if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
          event.preventDefault();
          show(event.key === "ArrowUp" ? Math.max(0, enabled.length - 1) : 0);
        }}
      >
        {trigger}
      </button>

      {open ? (
        <div
          className="menu-list"
          role="menu"
          aria-label={label}
          data-align={align}
          onKeyDown={(event) => {
            // Enter и пробел здесь не перехватываются: их обрабатывает сама
            // сфокусированная кнопка, и выполняется именно её действие.
            switch (event.key) {
              case "ArrowDown": event.preventDefault(); step(1); break;
              case "ArrowUp": event.preventDefault(); step(-1); break;
              case "Home": event.preventDefault(); options.current[0]?.focus(); break;
              case "End": event.preventDefault(); options.current[enabled.length - 1]?.focus(); break;
              case "Escape": event.preventDefault(); hide(); break;
              default: break;
            }
          }}
          onBlur={(event) => {
            // Пункт, исчезнувший или ставший недоступным, теряет фокус сам —
            // это не уход оператора из меню: фокус вернёт эффект выше.
            const left: EventTarget = event.target;
            const lost = left instanceof HTMLButtonElement && (!left.isConnected || left.disabled);
            if (!event.relatedTarget && lost) return;
            if (!root.current?.contains(event.relatedTarget as Node | null)) setOpen(false);
          }}
        >
          {items.map((item) => {
            const position = enabled.indexOf(item);
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                type="button"
                ref={(node) => {
                  if (position >= 0) options.current[position] = node;
                }}
                className="menu-item"
                role={item.checked === undefined ? "menuitem" : "menuitemcheckbox"}
                aria-checked={item.checked}
                disabled={item.disabled}
                // Табулируем ровно один пункт: Tab из меню уводит наружу и
                // закрывает его, а не идёт по списку мимо подсветки.
                tabIndex={position === focused ? 0 : -1}
                data-active={position >= 0 && position === focused ? "true" : undefined}
                title={item.hint}
                onFocus={() => position >= 0 && setActive(position)}
                onPointerEnter={() => options.current[position]?.focus()}
                onClick={() => choose(item)}
              >
                {Icon ? <Icon size={15} aria-hidden="true" /> : <span className="menu-item-mark" />}
                <span className="menu-item-label">{item.label}</span>
                {item.checked ? <span className="menu-item-state">вкл</span> : null}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
