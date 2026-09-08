/**
 * Мелкие детали интерфейса, из которых собран весь остальной экран.
 *
 * Здесь нет ни одной библиотеки компонентов — рамка, полоса и вертушка на
 * ASCII-экране это несколько символов и пара классов, а не пакет с зависимостями.
 */

import { useEffect, useRef, useState, type ReactNode } from "react";

import { frame as orbitFrame } from "./lib/orbit3d";

/** Панель в рамке. Заголовок сидит верхом на верхней линии: ┤ ЗАГОЛОВОК ├ */
export function Panel({
  title,
  right,
  children,
  foot,
  className = "",
}: {
  title: string;
  right?: ReactNode;
  children: ReactNode;
  foot?: ReactNode;
  className?: string;
}) {
  return (
    <div className={`panel ${className}`}>
      <div className="panel-title">
        <span className="bracket">┤ </span>
        <b>{title}</b>
        {right ? <> · {right}</> : null}
        <span className="bracket"> ├</span>
      </div>
      <div className="panel-body">{children}</div>
      {foot ? <div className="panel-foot">{foot}</div> : null}
    </div>
  );
}

/**
 * Полоса заполнения из символов.
 *
 * Ширина в знаках, а не в процентах ширины блока: полоса обязана лечь ровно
 * в ту же сетку, что и текст вокруг, иначе колонки разъезжаются.
 */
export function Bar({
  value,
  width = 12,
  tone = "",
}: {
  /** Доля от нуля до единицы. */
  value: number;
  width?: number;
  tone?: "" | "warn" | "bad";
}) {
  const safe = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
  const filled = Math.round(safe * width);
  return (
    <span className={`bar ${tone}`}>
      <span className="fill">{"█".repeat(filled)}</span>
      {"░".repeat(width - filled)}
    </span>
  );
}

/**
 * Трёхмерная сцена за схемой графа.
 *
 * Единственная картинка на экране, которой не нужен ни один пиксель:
 * `lib/orbit3d.ts` считает шар и кольца в трёх координатах и возвращает кадр
 * текстом. Здесь только петля кадров и камера — её ведёт курсор над схемой,
 * а скорость вращения берётся из хода: пока считает модель, сцена гонит.
 *
 * По умолчанию рисуется один кадр. При включённой анимации — до 12 кадров
 * в секунду через textContent. Скрытая вкладка и reduced motion останавливают
 * таймер; размер кадра ограничен независимо от разрешения монитора.
 */
export function Orbit({
  tone = "idle",
  steer = "parent",
  animated = false,
}: {
  tone?: "idle" | "run" | "wait";
  steer?: "parent" | "window";
  animated?: boolean;
}) {
  const host = useRef<HTMLPreElement>(null);
  const speed = useRef(1);
  speed.current = tone === "run" ? 2.6 : tone === "wait" ? 0.45 : 1;

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const stage = steer === "window" ? window : (el.parentElement ?? el);
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const probe = document.createElement("span");
    probe.textContent = "0".repeat(50);
    probe.style.cssText = "position:absolute;visibility:hidden;white-space:pre";
    el.appendChild(probe);
    const box = probe.getBoundingClientRect();
    const cellW = box.width / 50;
    const cellH = box.height;
    probe.remove();
    if (!cellW || !cellH) return;

    let cols = 0;
    let rows = 0;
    let t = 1.2;
    let yaw = 0;
    let pitch = 0;
    let wantYaw = 0;
    let wantPitch = 0;
    let timer: number | undefined;
    let last = performance.now();
    const paint = () => {
      if (!document.hidden && cols > 0 && rows > 0) {
        el.textContent = orbitFrame(cols, rows, {
          yaw: t * 0.5 + yaw,
          pitch: 0.35 + Math.sin(t * 0.19) * 0.16 + pitch,
        }, t, cellW / cellH);
      }
    };
    const measure = () => {
      // Bound frame cost even on a large monitor.
      cols = Math.min(160, Math.floor(el.clientWidth / cellW));
      rows = Math.min(70, Math.floor(el.clientHeight / cellH));
      paint();
    };
    const tick = () => {
      const now = performance.now();
      t += Math.min(0.2, (now - last) / 1000) * speed.current;
      last = now;
      yaw += (wantYaw - yaw) * 0.15;
      pitch += (wantPitch - pitch) * 0.15;
      paint();
      timer = window.setTimeout(tick, 1000 / 12);
    };
    const sync = () => {
      window.clearTimeout(timer);
      paint();
      if (animated && !motion.matches && !document.hidden) {
        last = performance.now();
        timer = window.setTimeout(tick, 1000 / 12);
      }
    };
    const onMove = (event: Event) => {
      if (!animated || motion.matches) return;
      const pointer = event as PointerEvent;
      const area = steer === "window"
        ? { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight }
        : (stage as HTMLElement).getBoundingClientRect();
      wantYaw = ((pointer.clientX - area.left) / Math.max(1, area.width) - 0.5) * 1.7;
      wantPitch = ((pointer.clientY - area.top) / Math.max(1, area.height) - 0.5) * 0.9;
    };
    const onLeave = () => { wantYaw = 0; wantPitch = 0; };
    const resize = new ResizeObserver(measure);
    resize.observe(el);
    measure();
    sync();
    if (animated) {
      stage.addEventListener("pointermove", onMove);
      stage.addEventListener("pointerleave", onLeave);
    }
    document.addEventListener("visibilitychange", sync);
    motion.addEventListener("change", sync);
    return () => {
      window.clearTimeout(timer);
      resize.disconnect();
      stage.removeEventListener("pointermove", onMove);
      stage.removeEventListener("pointerleave", onLeave);
      document.removeEventListener("visibilitychange", sync);
      motion.removeEventListener("change", sync);
    };
  }, [animated, steer]);

  return <pre ref={host} className={`orbit orbit-${tone}`} aria-hidden="true" />;
}

const SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

/** Вертушка. Кадр меняется по таймеру, а не анимацией CSS: это текст. */
export function Spinner({ on }: { on: boolean }) {
  const [frame, setFrame] = useState(0);
  useEffect(() => {
    if (!on) return;
    const id = window.setInterval(() => setFrame((f) => f + 1), 90);
    return () => window.clearInterval(id);
  }, [on]);
  if (!on) return <span className="dot-idle">·</span>;
  return <span style={{ color: "var(--cyan)" }}>{SPINNER[frame % SPINNER.length]}</span>;
}

/**
 * Число, которое доезжает до нового значения, а не прыгает.
 *
 * Деньги на экране меняются после каждого ответа модели, и мгновенная замена
 * цифры проходит мимо внимания. Двести миллисекунд движения — нет.
 */
export function Counter({
  value,
  format,
}: {
  value: number;
  format: (v: number) => string;
}) {
  const [shown, setShown] = useState(value);
  const from = useRef(value);
  const started = useRef(0);

  useEffect(() => {
    if (value === from.current) return;
    const begin = from.current;
    const delta = value - begin;
    started.current = performance.now();
    let raf = 0;
    const step = (now: number) => {
      const t = Math.min(1, (now - started.current) / 220);
      // Плавный вход и выход: линейное движение читается как рывок.
      const eased = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
      setShown(begin + delta * eased);
      if (t < 1) raf = requestAnimationFrame(step);
      else from.current = value;
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [value]);

  return <>{format(shown)}</>;
}

/** Строка «ключ ....... значение» с точечным заполнителем между ними. */
export function Row({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="kv">
      <span className="k">{k}</span>
      <span className="v">{children}</span>
    </div>
  );
}

/**
 * Заставка при первом запуске.
 *
 * Текст набирается посимвольно — это единственная анимация, которую видно
 * до подключения к серверу, и она же говорит, что фронт вообще жив.
 */
export function Boot({ lines, onDone }: { lines: string[]; onDone: () => void }) {
  const [shown, setShown] = useState("");
  const full = lines.join("\n");

  useEffect(() => {
    let i = 0;
    const id = window.setInterval(() => {
      i += 3;
      setShown(full.slice(0, i));
      if (i >= full.length) {
        window.clearInterval(id);
        window.setTimeout(onDone, 260);
      }
    }, 16);
    return () => window.clearInterval(id);
  }, [full, onDone]);

  return (
    <pre
      style={{
        margin: 0,
        padding: "2em 3ch",
        color: "var(--green)",
        whiteSpace: "pre",
        overflow: "hidden",
      }}
    >
      {shown}
      <span className="caret-blink" />
    </pre>
  );
}
