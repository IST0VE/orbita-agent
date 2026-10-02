/**
 * Ширина боковой колонки: мышь, память и сброс.
 *
 * Вынесено из `App.tsx` как законченная обязанность: состояние, обработчик
 * перетаскивания с двумя подписками на окно, запись в localStorage и сброс.
 * В компоненте эти четыре куска лежали в четырёх разных местах — между
 * загрузкой манифеста, разметкой и обработчиком двойного клика.
 *
 * Ширину тянет мышь, а не медиазапрос: длина имён документов у каждого своя,
 * и угадать её за оператора нельзя. Ноль означает «как решит вёрстка».
 *
 * Обе колонки тянутся одинаково, различается только кромка: левую — за
 * правую, правую — за левую. Ручка упирается не только в свой максимум, но и
 * в ширину схемы: две широкие колонки на ноутбуке иначе сжимали бы её в
 * полоску, а схема — то, ради чего экран открыт.
 */
import { useCallback, useEffect, useState, type PointerEvent } from "react";

export type ColumnSide = "left" | "right";

const KEYS: Record<ColumnSide, string> = {
  left: "orbita.leftWidth",
  right: "orbita.rightWidth",
};
/**
 * Правой нужно больше: в её шапке две вкладки и «К прогону». Те же минимумы и
 * `MAIN_MIN` стоят в сетке `.app-body` (shell.css): ручка упирается в них при
 * перетаскивании, сетка — когда окно уменьшили уже после.
 */
const MIN: Record<ColumnSide, number> = { left: 180, right: 240 };
const MAX = 640;
/** Уже этого схема перестаёт читаться. */
const MAIN_MIN = 380;

export function useColumnWidth(side: ColumnSide) {
  const key = KEYS[side];
  const [width, setWidth] = useState(() => Number(localStorage.getItem(key)) || 0);

  useEffect(() => {
    if (width) localStorage.setItem(key, String(width));
  }, [key, width]);

  const startResize = useCallback((event: PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const column = event.currentTarget.parentElement;
    if (!column) return;
    const box = column.getBoundingClientRect();
    const main = column.parentElement?.querySelector<HTMLElement>(":scope > .app-main");
    const room = main ? main.getBoundingClientRect().width - MAIN_MIN : Infinity;
    // Схема уже сейчас уже минимума — колонку можно сузить, но не расширить.
    const limit = Math.min(MAX, box.width + Math.max(0, room));
    const move = (moving: globalThis.PointerEvent) => {
      const wanted = side === "left" ? moving.clientX - box.left : box.right - moving.clientX;
      setWidth(Math.round(Math.min(limit, Math.max(MIN[side], wanted))));
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  }, [side]);

  const reset = useCallback(() => {
    setWidth(0);
    localStorage.removeItem(key);
  }, [key]);

  return { width, startResize, reset };
}
