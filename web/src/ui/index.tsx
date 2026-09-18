/**
 * Мелкие детали интерфейса, из которых собран весь остальной экран.
 *
 * Здесь нет библиотеки компонентов: карточка, точка состояния и полоса — это
 * несколько элементов и класс из дизайн-системы, а не пакет с зависимостями.
 * Правило приёма в этот файл одно: деталь должна встречаться минимум в двух
 * несвязанных местах экрана. Всё, что нужно одному месту, живёт рядом с ним.
 */

import type { ReactNode } from "react";

import { type LucideIcon } from "./icons";

/**
 * Карточка с заголовком.
 *
 * Базовая поверхность интерфейса: белый прямоугольник с мягкой тенью на
 * тёплом фоне приложения. Раньше на её месте была рамка из знаков
 * `┤ ЗАГОЛОВОК ├` — в моноширинной сетке это работало, в пропорциональной
 * работает рамка и воздух.
 */
export function Panel({
  title,
  right,
  children,
  foot,
  className = "",
  icon: Icon,
}: {
  title: string;
  right?: ReactNode;
  children: ReactNode;
  foot?: ReactNode;
  className?: string;
  icon?: LucideIcon;
}) {
  return (
    <section className={`panel ${className}`.trim()}>
      <div className="panel-title">
        {Icon ? <Icon size={16} aria-hidden="true" /> : null}
        <h3>{title}</h3>
        {right ? <div className="panel-title-right">{right}</div> : null}
      </div>
      <div className="panel-body">{children}</div>
      {foot ? <div className="panel-foot">{foot}</div> : null}
    </section>
  );
}

export type Tone = "ok" | "run" | "warn" | "bad" | "idle" | "brand";

/** Точка состояния. Один и тот же кружок в шапке, в журнале и в карточках. */
export function StatusDot({ tone = "idle" }: { tone?: Tone }) {
  return <span className={`dot dot-${tone}`} aria-hidden="true" />;
}

/**
 * Полоса доли.
 *
 * Показывает отношение, а не абсолютную величину, поэтому подпись со
 * значением стоит рядом с ярлыком, а не внутри полосы: цифра внутри полосы
 * читается только когда полоса заполнена больше чем наполовину.
 */
export function Meter({
  label,
  value,
  caption,
  tone = "",
}: {
  label: string;
  /** Доля от нуля до единицы. */
  value: number;
  caption: string;
  tone?: "" | "warn" | "bad";
}) {
  const safe = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
  return (
    <div className="meter">
      <div className="meter-head">
        <span>{label}</span>
        <b>{caption}</b>
      </div>
      <div
        className="meter-track"
        role="meter"
        aria-label={label}
        aria-valuenow={Math.round(safe * 100)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuetext={caption}
      >
        <span className={`meter-fill ${tone}`.trim()} style={{ width: `${safe * 100}%` }} />
      </div>
    </div>
  );
}

/** Вертушка ожидания. Чистый CSS: анимация знаками ушла вместе с терминалом. */
export function Spinner({ on }: { on: boolean }) {
  if (!on) return null;
  return <span className="spinner" role="status" aria-label="Идёт прогон" />;
}

/**
 * Пустое состояние.
 *
 * Пустая панель без объяснения читается как поломка. Значок, строка о том,
 * что здесь будет, и по надобности — действие, с которого это начинается.
 */
export function EmptyState({
  icon: Icon,
  title,
  hint,
  children,
}: {
  icon: LucideIcon;
  title: string;
  hint?: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <Icon size={24} aria-hidden="true" />
      <b>{title}</b>
      {hint ? <span>{hint}</span> : null}
      {children}
    </div>
  );
}
