/**
 * Какие боковые колонки открыты.
 *
 * Умолчание задаёт ширина окна: на 1440 помещаются обе колонки и схема, на
 * ноутбуке — левая и схема, на планшете — только схема. Дальше решает
 * оператор, и его решение живёт до следующей смены ширины: медиазапрос,
 * который прячет колонку сам, стирал бы это решение при каждом изменении
 * размера окна — в том числе при открытии панели разработчика.
 *
 * Поэтому состояние держится здесь, а не в таблице стилей: CSS умеет
 * умолчание или решение, но не оба сразу.
 */
import { useCallback, useEffect, useState } from "react";

/** Обе колонки и схема рядом. */
const WIDE = "(min-width: 1280px)";
/** Левая колонка и схема. */
const MEDIUM = "(min-width: 1024px)";

const matches = (query: string) =>
  typeof window === "undefined" || typeof window.matchMedia !== "function"
    ? true
    : window.matchMedia(query).matches;

export function useColumns() {
  const [sidebar, setSidebar] = useState(() => matches(MEDIUM));
  const [inspector, setInspector] = useState(() => matches(WIDE));

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const wide = window.matchMedia(WIDE);
    const medium = window.matchMedia(MEDIUM);
    const apply = () => {
      setSidebar(medium.matches);
      setInspector(wide.matches);
    };
    wide.addEventListener("change", apply);
    medium.addEventListener("change", apply);
    return () => {
      wide.removeEventListener("change", apply);
      medium.removeEventListener("change", apply);
    };
  }, []);

  /*
   * В узком окне открытая колонка занимает экран целиком, поэтому двух
   * открытых сразу там быть не может: вторая закрывает первую.
   */
  const narrow = () => !matches(MEDIUM);

  const toggleSidebar = useCallback(() => {
    setSidebar((open) => {
      if (!open && narrow()) setInspector(false);
      return !open;
    });
  }, []);

  const toggleInspector = useCallback(() => {
    setInspector((open) => {
      if (!open && narrow()) setSidebar(false);
      return !open;
    });
  }, []);

  const openSidebar = useCallback(() => {
    setSidebar(true);
    if (narrow()) setInspector(false);
  }, []);

  return { sidebar, inspector, toggleSidebar, toggleInspector, openSidebar };
}
