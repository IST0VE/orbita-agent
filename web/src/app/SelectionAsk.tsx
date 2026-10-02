/**
 * «Спросить об этом»: выделенный кусок ответа — цитатой в поле задачи.
 *
 * Ответы ORBITA — это документы на тысячи знаков, и вопрос обычно о
 * конкретном абзаце. Раньше его копировали, вставляли в поле и дописывали
 * вопрос; теперь достаточно выделить: рядом с выделением появляется кнопка,
 * и кусок уезжает в поле задачи цитатой `> …`, а курсор встаёт после неё.
 *
 * Кнопка появляется только над тем, что помечено `data-askable`: ответ агента
 * в ленте чата, открытый документ и итог прогона. Выделение в поле ввода, в
 * своём запросе или в настройках — это не вопрос к ответу.
 *
 * Мышью кнопка показывается, когда выделение закончено (кнопку отпустили), а
 * не на каждом шаге протяжки; с клавиатуры (Shift + стрелки) — после паузы.
 */

import { useEffect, useRef, useState } from "react";

import { TextQuote } from "../ui/icons";

type Spot = { left: number; top: number; text: string };

/** Не длиннее этого: цитата в поле задачи — фрагмент, а не весь документ. */
const MAX_QUOTE = 4000;
const BUTTON_WIDTH = 170;
const KEYBOARD_PAUSE_MS = 250;

function askableOf(node: Node | null): Element | null {
  const element = node instanceof Element ? node : node?.parentElement ?? null;
  return element?.closest("[data-askable]") ?? null;
}

function measure(): Spot | null {
  const selection = document.getSelection();
  if (!selection || selection.isCollapsed || !selection.rangeCount) return null;
  const range = selection.getRangeAt(0);
  const host = askableOf(range.startContainer);
  // Выделение, начатое в ответе и законченное в соседнем блоке, — не цитата
  // одного ответа: такое не предлагаем.
  if (!host || host !== askableOf(range.endContainer)) return null;
  const text = selection.toString().trim();
  if (!text) return null;
  const rects = Array.from(range.getClientRects()).filter((rect) => rect.width || rect.height);
  const last = rects.at(-1) ?? range.getBoundingClientRect();
  const left = Math.min(Math.max(8, last.right - BUTTON_WIDTH / 2), window.innerWidth - BUTTON_WIDTH - 8);
  // Под последней строкой выделения; не помещается снизу — над первой.
  const below = last.bottom + 6;
  const top = below + 36 < window.innerHeight ? below : Math.max(8, (rects[0] ?? last).top - 40);
  return { left, top, text: text.length > MAX_QUOTE ? `${text.slice(0, MAX_QUOTE).trimEnd()}…` : text };
}

export function SelectionAsk({ onAsk, disabled }: { onAsk: (text: string) => void; disabled?: boolean }) {
  const [spot, setSpot] = useState<Spot | null>(null);
  const pointerDown = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => {
    const update = () => setSpot(pointerDown.current ? null : measure());
    const onSelection = () => {
      clearTimeout(timer.current);
      // Пока тянут мышью, кнопка прыгала бы за курсором: ждём отпускания.
      if (pointerDown.current) { setSpot(null); return; }
      timer.current = setTimeout(update, KEYBOARD_PAUSE_MS);
    };
    const onDown = (event: PointerEvent) => {
      if ((event.target as Element | null)?.closest?.(".selection-ask")) return;
      pointerDown.current = true;
      setSpot(null);
    };
    const onUp = () => {
      pointerDown.current = false;
      clearTimeout(timer.current);
      // Выделение обновляется после pointerup — читаем его на следующем кадре.
      requestAnimationFrame(update);
    };
    const onScroll = () => setSpot((current) => (current ? measure() : null));
    document.addEventListener("selectionchange", onSelection);
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("pointerup", onUp, true);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    return () => {
      clearTimeout(timer.current);
      document.removeEventListener("selectionchange", onSelection);
      document.removeEventListener("pointerdown", onDown, true);
      document.removeEventListener("pointerup", onUp, true);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
    };
  }, []);

  if (!spot || disabled) return null;
  return (
    <button
      type="button"
      className="selection-ask"
      style={{ left: spot.left, top: spot.top }}
      title="Вставить выделенное цитатой в поле задачи и задать о нём вопрос"
      // Нажатие не должно снимать выделение до того, как его прочитали.
      onMouseDown={(event) => event.preventDefault()}
      onClick={() => {
        onAsk(spot.text);
        document.getSelection()?.removeAllRanges();
        setSpot(null);
      }}
    >
      <TextQuote size={14} aria-hidden="true" />
      Спросить об этом
    </button>
  );
}
