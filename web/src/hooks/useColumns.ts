/**
 * Какие боковые колонки открыты и как они показаны.
 *
 * Умолчание задаёт ширина окна: на 1440 помещаются обе колонки и схема, на
 * ноутбуке — колонка материалов и схема, на планшете — только схема. Дальше
 * решает оператор, и его решение живёт до следующей смены ширины:
 * медиазапрос, который прячет колонку сам, стирал бы это решение при каждом
 * изменении размера окна — в том числе при открытии панели разработчика.
 *
 * В узком окне колонка перестаёт быть колонкой и становится выдвижной
 * панелью поверх рабочей области: две панели по 250 пикселей рядом со схемой
 * в 380 пикселей не помещаются ни при какой вёрстке, а схема — то, ради чего
 * экран открыт.
 */
import { useCallback, useEffect, useState } from "react";

/** Обе колонки и схема рядом. */
const WIDE = "(min-width: 1440px)";
/** Колонка материалов и схема. */
const MEDIUM = "(min-width: 1024px)";

const matches = (query: string) =>
  typeof window === "undefined" || typeof window.matchMedia !== "function"
    ? true
    : window.matchMedia(query).matches;

export function useColumns() {
  const [sidebar, setSidebar] = useState(() => matches(MEDIUM));
  const [inspector, setInspector] = useState(() => matches(WIDE));
  /** Колонки показаны поверх рабочей области, а не рядом с ней. */
  const [narrow, setNarrow] = useState(() => !matches(MEDIUM));

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const wide = window.matchMedia(WIDE);
    const medium = window.matchMedia(MEDIUM);
    const apply = () => {
      setSidebar(medium.matches);
      setInspector(wide.matches);
      setNarrow(!medium.matches);
    };
    wide.addEventListener("change", apply);
    medium.addEventListener("change", apply);
    return () => {
      wide.removeEventListener("change", apply);
      medium.removeEventListener("change", apply);
    };
  }, []);

  /*
   * В узком окне открытая панель занимает экран целиком, поэтому двух
   * открытых сразу там быть не может: вторая закрывает первую.
   */
  const tight = () => !matches(MEDIUM);

  const toggleSidebar = useCallback(() => {
    setSidebar((open) => {
      if (!open && tight()) setInspector(false);
      return !open;
    });
  }, []);

  const toggleInspector = useCallback(() => {
    setInspector((open) => {
      if (!open && tight()) setSidebar(false);
      return !open;
    });
  }, []);

  const openSidebar = useCallback(() => {
    setSidebar(true);
    if (tight()) setInspector(false);
  }, []);

  /** Выбрали узел — подробности приходят сами: их для этого и запрашивали. */
  const openInspector = useCallback(() => {
    setInspector(true);
    if (tight()) setSidebar(false);
  }, []);

  const closeSidebar = useCallback(() => setSidebar(false), []);
  const closeInspector = useCallback(() => setInspector(false), []);

  return {
    sidebar,
    inspector,
    narrow,
    toggleSidebar,
    toggleInspector,
    openSidebar,
    openInspector,
    closeSidebar,
    closeInspector,
  };
}
