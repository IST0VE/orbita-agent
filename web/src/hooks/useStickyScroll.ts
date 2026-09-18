/**
 * Прокрутка, которая держится низа.
 *
 * Лента прогона дописывается снизу, и человек, читающий её вживую, не должен
 * догонять последнюю строку колесом. Но и утаскивать его вниз, когда он
 * отлистал назад и читает старое сообщение, нельзя — поэтому низ удерживается
 * только если он там и был.
 *
 * Слежение идёт за размером содержимого, а не за пропсами: сообщение может
 * подрасти после отрисовки (Markdown, таблица), и прокрутка по событию
 * обновления встала бы на полсообщения выше конца.
 */
import { useEffect, useRef } from "react";

/** Насколько близко к низу человек ещё считается «читающим вживую». */
const THRESHOLD = 48;

export function useStickyScroll<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const pinned = useRef(true);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const remember = () => {
      pinned.current = node.scrollHeight - node.scrollTop - node.clientHeight <= THRESHOLD;
    };
    const follow = () => {
      if (pinned.current) node.scrollTop = node.scrollHeight;
    };
    node.addEventListener("scroll", remember, { passive: true });
    follow();
    if (typeof ResizeObserver === "undefined") {
      return () => node.removeEventListener("scroll", remember);
    }
    const observer = new ResizeObserver(follow);
    observer.observe(node);
    for (const child of node.children) observer.observe(child);
    // Содержимое приходит виджетами и появляется позже самой области, поэтому
    // за составом детей тоже надо следить: без этого лента «оживает» только
    // после первого изменения размера.
    const mutations = new MutationObserver(() => {
      for (const child of node.children) observer.observe(child);
      follow();
    });
    mutations.observe(node, { childList: true, subtree: true });
    return () => {
      node.removeEventListener("scroll", remember);
      observer.disconnect();
      mutations.disconnect();
    };
  }, []);

  return ref;
}
