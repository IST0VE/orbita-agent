/**
 * Ширина левой колонки: мышь, память и сброс.
 *
 * Вынесено из `App.tsx` как законченная обязанность: состояние, обработчик
 * перетаскивания с двумя подписками на окно, запись в localStorage и сброс.
 * В компоненте эти четыре куска лежали в четырёх разных местах — между
 * загрузкой манифеста, разметкой и обработчиком двойного клика.
 *
 * Ширину тянет мышь, а не медиазапрос: длина имён документов у каждого своя,
 * и угадать её за оператора нельзя. Ноль означает «как решит вёрстка».
 */
import { useCallback, useEffect, useState, type PointerEvent } from "react";

const KEY = "orbita.leftWidth";
const MIN = 180;
const MAX = 640;

export function useColumnWidth() {
  const [width, setWidth] = useState(() => Number(localStorage.getItem(KEY)) || 0);

  useEffect(() => {
    if (width) localStorage.setItem(KEY, String(width));
  }, [width]);

  const startResize = useCallback((event: PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const origin = event.currentTarget.parentElement?.getBoundingClientRect().left ?? 0;
    const move = (moving: globalThis.PointerEvent) => {
      setWidth(Math.round(Math.min(MAX, Math.max(MIN, moving.clientX - origin))));
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  }, []);

  const reset = useCallback(() => {
    setWidth(0);
    localStorage.removeItem(KEY);
  }, []);

  return { width, startResize, reset };
}
