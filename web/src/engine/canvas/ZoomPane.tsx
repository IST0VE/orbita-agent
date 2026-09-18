/**
 * Масштаб табличного списка.
 *
 * Список сохраняет нативную прокрутку и масштаб содержимого. Графический
 * холст использует собственную камеру React Flow и этот компонент не монтирует.
 */

import {
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  type Ref,
} from "react";

export const ZOOM_MIN = 0.3;
export const ZOOM_MAX = 3;
export const ZOOM_STEP = 0.1;

/**
 * Масштаб храним с точностью до процента.
 *
 * Иначе шаг колеса копит хвост double: 0.7000000000000001 в подписи — «70%», а
 * в сравнении с 0.7 — уже другое число, и кнопка «100%» перестаёт совпадать.
 */
export const clampZoom = (value: number) =>
  Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(value * 100) / 100));

export type ZoomHandle = {
  /** Шаг приближения, удерживая центр видимой области. */
  zoomBy: (delta: number) => void;
  /** Сто процентов и начало координат. */
  reset: () => void;
  /** Подобрать масштаб так, чтобы содержимое поместилось по ширине. */
  fit: () => void;
};

export function ZoomPane({
  zoom,
  onZoom,
  label,
  className = "",
  overlay,
  panOnDrag = false,
  ref,
  children,
}: {
  zoom: number;
  onZoom: (value: number) => void;
  label: string;
  className?: string;
  /** Слой поверх области: не масштабируется и не уезжает при прокрутке. */
  overlay?: ReactNode;
  /** Тянуть содержимое левой кнопкой. Включается там, где под курсором нет текста для выделения. */
  panOnDrag?: boolean;
  ref?: Ref<ZoomHandle>;
  children: ReactNode;
}) {
  const viewport = useRef<HTMLDivElement>(null);
  const sizer = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const anchor = useRef<{ x: number; y: number; contentX: number; contentY: number } | null>(null);
  const applied = useRef(zoom);
  // Заказанный масштаб отличается от отрисованного, пока React не свёл кадр.
  // Считать шаг от отрисованного нельзя: два быстрых нажатия «+» подряд дали бы
  // один шаг вместо двух.
  const requested = useRef(zoom);
  const touches = useRef(new Map<number, { x: number; y: number }>());
  const pinch = useRef<{ distance: number; zoom: number } | null>(null);
  const drag = useRef<{ pointerId: number; x: number; y: number; left: number; top: number } | null>(null);

  /**
   * Приблизить, удержав точку под курсором.
   *
   * Без этого колесо тянет содержимое из-под руки: начало координат остаётся в
   * левом верхнем углу, и узел, на который смотрели, уезжает за край.
   */
  const zoomAt = useCallback((next: number, clientX: number, clientY: number) => {
    const node = viewport.current;
    if (!node) return;
    const value = clampZoom(next);
    if (value === requested.current) return;
    const box = node.getBoundingClientRect();
    const x = clientX - box.left;
    const y = clientY - box.top;
    anchor.current = {
      x,
      y,
      contentX: (node.scrollLeft + x) / applied.current,
      contentY: (node.scrollTop + y) / applied.current,
    };
    requested.current = value;
    onZoom(value);
  }, [onZoom]);

  // Прокрутку правим до отрисовки кадра, иначе точка успевает мигнуть не на месте.
  useLayoutEffect(() => {
    const node = viewport.current;
    const previous = applied.current;
    const point = anchor.current;
    applied.current = zoom;
    requested.current = zoom;
    anchor.current = null;
    if (!node || !point || previous === zoom) return;
    node.scrollLeft = point.contentX * zoom - point.x;
    node.scrollTop = point.contentY * zoom - point.y;
  }, [zoom]);

  /**
   * Размер прокручиваемой области задаём подложке явными пикселями.
   *
   * Переполнение элемента в потоке браузер считает по вёрстке, а не по
   * `transform`: после отдаления справа оставалась пустота шириной со
   * стопроцентную схему, и полоса прокрутки уезжала в никуда. Подложка же
   * повторяет ровно то, что видно на экране.
   */
  const measure = useCallback(() => {
    const view = viewport.current;
    const frame = sizer.current;
    const box = content.current;
    if (!view || !frame || !box) return;
    const scale = applied.current;
    // Содержимое уже панели растягиваем до её ширины: иначе на отдалении
    // список и схема съезжают в левый край, а справа остаётся пустое поле.
    const minWidth = `${Math.max(0, Math.floor(view.clientWidth / scale) - 1)}px`;
    if (box.style.minWidth !== minWidth) box.style.minWidth = minWidth;
    const width = `${Math.ceil(box.offsetWidth * scale)}px`;
    const height = `${Math.ceil(box.offsetHeight * scale)}px`;
    if (frame.style.width !== width) frame.style.width = width;
    if (frame.style.height !== height) frame.style.height = height;
  }, []);

  useLayoutEffect(measure);

  useEffect(() => {
    const view = viewport.current;
    const box = content.current;
    if (!view || !box || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => measure());
    observer.observe(view);
    observer.observe(box);
    return () => observer.disconnect();
  }, [measure]);

  const zoomByCentre = useCallback((delta: number) => {
    const node = viewport.current;
    if (!node) return;
    const box = node.getBoundingClientRect();
    zoomAt(requested.current + delta, box.left + box.width / 2, box.top + box.height / 2);
  }, [zoomAt]);

  const reset = useCallback(() => {
    const node = viewport.current;
    requested.current = 1;
    onZoom(1);
    if (node) { node.scrollLeft = 0; node.scrollTop = 0; }
  }, [onZoom]);

  useImperativeHandle(ref, () => ({
    zoomBy: zoomByCentre,
    reset,
    fit: () => {
      const node = viewport.current;
      const box = content.current;
      if (!node || !box) return;
      /* `min-width` растягивает обёртку по ширине области; на время замера её
         убираем, иначе «по ширине» никогда не даст больше ста процентов. */
      const keep = box.style.minWidth;
      box.style.minWidth = "0";
      const natural = box.scrollWidth;
      box.style.minWidth = keep;
      if (!natural) return;
      // Округление вниз, а не к ближайшему: лишний процент возвращает ту самую
      // полосу прокрутки, ради которой масштаб и подбирали.
      requested.current = clampZoom(Math.floor((node.clientWidth - 2) / natural * 100) / 100);
      onZoom(requested.current);
      node.scrollLeft = 0;
      node.scrollTop = 0;
    },
  }), [onZoom, reset, zoomByCentre]);

  useEffect(() => {
    const node = viewport.current;
    if (!node) return;
    /* Ctrl/Cmd с колесом — масштаб полотна, обычное колесо остаётся прокруткой.
       Слушатель непассивный: иначе браузер заберёт жест себе и вместо схемы
       приблизит страницу целиком. */
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      const step = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaMode === 2 ? event.deltaY * 400 : event.deltaY;
      // Щелчок колеса (deltaY около сотни) даёт те же десять процентов, что и
      // кнопка, а мелкие шаги тачпада складываются в плавное движение.
      zoomAt(requested.current * Math.exp(-step / 1000), event.clientX, event.clientY);
    };
    node.addEventListener("wheel", onWheel, { passive: false });
    return () => node.removeEventListener("wheel", onWheel);
  }, [zoomAt]);

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    const node = viewport.current;
    if (!node) return;
    if (event.pointerType === "touch") {
      touches.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (touches.current.size === 2) {
        const [first, second] = [...touches.current.values()];
        pinch.current = { distance: Math.hypot(first.x - second.x, first.y - second.y) || 1, zoom: requested.current };
        // Щипок ведём сами, поэтому на время жеста забираем и прокрутку пальцем.
        node.style.touchAction = "none";
      }
      return;
    }
    const interactive = (event.target as Element | null)?.closest("a, button, input, select, textarea, tr, [role=button]");
    if (event.button !== 1 && !(panOnDrag && event.button === 0 && !interactive)) return;
    drag.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, left: node.scrollLeft, top: node.scrollTop };
    node.setPointerCapture(event.pointerId);
    // Средняя кнопка иначе включает автопрокрутку браузера поверх нашей.
    event.preventDefault();
  };

  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const node = viewport.current;
    if (!node) return;
    if (touches.current.has(event.pointerId)) {
      touches.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
      const base = pinch.current;
      if (!base || touches.current.size !== 2) return;
      const [first, second] = [...touches.current.values()];
      event.preventDefault();
      zoomAt(
        base.zoom * (Math.hypot(first.x - second.x, first.y - second.y) / base.distance),
        (first.x + second.x) / 2,
        (first.y + second.y) / 2,
      );
      return;
    }
    const move = drag.current;
    if (!move || move.pointerId !== event.pointerId) return;
    node.scrollLeft = move.left - (event.clientX - move.x);
    node.scrollTop = move.top - (event.clientY - move.y);
  };

  const onPointerEnd = (event: ReactPointerEvent<HTMLDivElement>) => {
    const node = viewport.current;
    if (touches.current.delete(event.pointerId) && touches.current.size < 2) {
      pinch.current = null;
      if (node) node.style.touchAction = "";
    }
    if (drag.current?.pointerId === event.pointerId) {
      drag.current = null;
      if (node?.hasPointerCapture(event.pointerId)) node.releasePointerCapture(event.pointerId);
    }
  };

  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if ((event.target as Element | null)?.closest("input, textarea, select")) return;
    if (event.key === "+" || event.key === "=") zoomByCentre(ZOOM_STEP);
    else if (event.key === "-" || event.key === "_") zoomByCentre(-ZOOM_STEP);
    else if (event.key === "0") reset();
    else return;
    event.preventDefault();
  };

  return <div className="zoom-frame">
    <div
      ref={viewport}
      className={`zoom-viewport${panOnDrag ? " zoom-pan" : ""}${className ? ` ${className}` : ""}`}
      role="group"
      aria-label={`${label}. Масштаб ${Math.round(zoom * 100)} процентов; Ctrl с колесом или клавиши плюс, минус, ноль`}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerEnd}
      onPointerCancel={onPointerEnd}
      onKeyDown={onKeyDown}
    >
      <div ref={sizer} className="zoom-sizer">
        <div ref={content} className="zoom-content" style={{ transform: `scale(${zoom})` }}>{children}</div>
      </div>
    </div>
    {overlay}
  </div>;
}
