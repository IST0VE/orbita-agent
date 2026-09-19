/**
 * Состояние взгляда на схему: представление, поиск, мини-карта, масштаб.
 *
 * Живёт отдельно от самой схемы, потому что смотрят на схему из двух мест
 * сразу: полотно рисует узлы, а панель рабочей области держит поиск, масштаб
 * и переключатель представления. Раньше панель стояла внутри полотна и была
 * второй строкой вкладок под первой; теперь строка одна, и общее состояние
 * ей нужно общее.
 */
import { useCallback, useRef, useState } from "react";

import type { DiagramHandle } from "./GraphDiagram";
import type { ZoomHandle } from "./ZoomPane";

/**
 * Как показан граф.
 *
 * Мини-карта перестала быть третьим представлением: это была та же схема с
 * навигатором в углу, то есть настройка, объявленная вкладкой. Настройки
 * живут в меню, представления — в переключателе.
 */
export type Representation = "diagram" | "list";

const MINIMAP_KEY = "orbita.graph.minimap";

export type GraphView = {
  representation: Representation;
  setRepresentation: (value: Representation) => void;
  minimap: boolean;
  toggleMinimap: () => void;
  query: string;
  setQuery: (value: string) => void;
  /** Масштаб активного представления — тот, что показан в панели. */
  zoom: number;
  onZoom: (value: number) => void;
  onListZoom: (value: number) => void;
  diagram: React.RefObject<DiagramHandle | null>;
  list: React.RefObject<ZoomHandle | null>;
  /** Управление масштабом адресуется тому представлению, которое открыто. */
  pane: React.RefObject<ZoomHandle | null>;
  arrange: () => void;
};

export function useGraphView(): GraphView {
  const [representation, setRepresentation] = useState<Representation>("diagram");
  const [minimap, setMinimap] = useState(() => localStorage.getItem(MINIMAP_KEY) === "1");
  const [query, setQuery] = useState("");
  const [zoom, setZoom] = useState(1);
  const [listZoom, setListZoom] = useState(1);
  const diagram = useRef<DiagramHandle>(null);
  const list = useRef<ZoomHandle>(null);

  const toggleMinimap = useCallback(() => {
    setMinimap((value) => {
      localStorage.setItem(MINIMAP_KEY, value ? "0" : "1");
      return !value;
    });
  }, []);

  return {
    representation,
    setRepresentation,
    minimap,
    toggleMinimap,
    query,
    setQuery,
    zoom: representation === "list" ? listZoom : zoom,
    onZoom: setZoom,
    onListZoom: setListZoom,
    diagram,
    list,
    pane: representation === "list" ? list : diagram,
    arrange: () => diagram.current?.arrange(),
  };
}
