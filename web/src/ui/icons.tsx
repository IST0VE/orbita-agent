/**
 * Словарь значков интерфейса.
 *
 * Один файл, а не импорт из `lucide-react` в каждом компоненте: набор значков
 * это часть языка продукта, и он должен быть перечислим. Пока список лежит
 * здесь, видно, что «папка» во всём интерфейсе одна и та же папка, а размеры
 * и толщина линии заданы один раз.
 *
 * Размеры: 13-14 — внутри строки текста, 15-16 — в кнопках и списках, 18 —
 * в шапке, 20 — в заголовке подтверждения. Толщину линии значки берут свою,
 * общую для всего набора.
 */

import {
  Bell,
  Blocks,
  BookOpen,
  Brain,
  Check,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  ClipboardList,
  Clock,
  Copy,
  Cpu,
  Download,
  Ellipsis,
  ExternalLink,
  Eye,
  File,
  FileSearch,
  FileText,
  Flag,
  Folder,
  FolderOpen,
  FolderPlus,
  Gauge,
  GitBranch,
  Inbox,
  KeyRound,
  Layers,
  LayoutGrid,
  List,
  Map,
  Maximize2,
  MessageSquare,
  Minus,
  Network,
  Palette,
  PanelLeft,
  PanelLeftClose,
  PanelRight,
  PanelRightClose,
  Paperclip,
  Play,
  Plus,
  Rocket,
  RotateCcw,
  ScrollText,
  Search,
  Server,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Square,
  SquareKanban,
  SquarePen,
  TriangleAlert,
  Waypoints,
  Wrench,
  X,
  type LucideIcon,
} from "lucide-react";

export type { LucideIcon };

export {
  Bell,
  Blocks,
  BookOpen,
  Brain,
  Check,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  ClipboardList,
  Clock,
  Copy,
  Cpu,
  Download,
  Ellipsis,
  ExternalLink,
  Eye,
  File,
  FileSearch,
  FileText,
  Flag,
  Folder,
  FolderOpen,
  FolderPlus,
  Gauge,
  GitBranch,
  Inbox,
  KeyRound,
  Layers,
  LayoutGrid,
  List,
  Map,
  Maximize2,
  MessageSquare,
  Minus,
  Network,
  Palette,
  PanelLeft,
  PanelLeftClose,
  PanelRight,
  PanelRightClose,
  Paperclip,
  Play,
  Plus,
  Rocket,
  RotateCcw,
  ScrollText,
  Search,
  Server,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Square,
  SquareKanban,
  SquarePen,
  TriangleAlert,
  Waypoints,
  Wrench,
  X,
};

/* --------------------------------------------------------------------- */
/* Узлы графа                                                             */
/* --------------------------------------------------------------------- */

export type NodeKind = "task" | "router" | "tool" | "approval" | "system";
export type NodeColor = "neutral" | "info" | "success" | "warning" | "danger";

/**
 * Значок узла по его роду занятий.
 *
 * Род берётся из манифеста (`nodes[].kind`), а не угадывается по имени узла:
 * имена придумывает граф, и «prepare_publish» ничем не отличается от
 * «prepare_context», пока манифест не скажет, что одно — подготовка внешнего
 * действия, а другое — системный шаг.
 */
const KIND_ICON: Record<NodeKind, LucideIcon> = {
  task: FileText,
  router: GitBranch,
  tool: Wrench,
  approval: ShieldCheck,
  system: Cpu,
};

/** Отдельные узлы узнаваемы сами по себе и заслуживают своего значка. */
const ID_ICON: Record<string, LucideIcon> = {
  context: Layers,
  remember: Brain,
  memory: Brain,
  tools: Wrench,
  publish: ExternalLink,
  prepare_publish: BookOpen,
  approve: ShieldCheck,
  over_budget: TriangleAlert,
  halted: Square,
  no_input: Inbox,
};

export function nodeIcon(nodeId: string, kind?: string): LucideIcon {
  return ID_ICON[nodeId] ?? KIND_ICON[(kind ?? "task") as NodeKind] ?? FileText;
}

/**
 * Цвет значка узла.
 *
 * Манифест объявляет `color` смыслом («это предупреждение»), а не краской, —
 * поэтому перевод в оттенок живёт здесь, а не в манифесте. Род узла отвечает
 * за тех, кому цвет не объявлен: инструменты бирюзовые, подтверждение
 * оранжевое, остальное синее.
 */
const COLOR_TONE: Record<NodeColor, string> = {
  neutral: "tone-neutral",
  info: "tone-info",
  success: "tone-success",
  warning: "tone-warning",
  danger: "tone-danger",
};

const KIND_TONE: Record<NodeKind, string> = {
  task: "tone-info",
  router: "tone-warning",
  tool: "tone-teal",
  approval: "tone-brand",
  system: "tone-info",
};

export function nodeTone(kind?: string, color?: string): string {
  if (kind === "tool" || kind === "approval") return KIND_TONE[kind];
  if (color && color in COLOR_TONE) return COLOR_TONE[color as NodeColor];
  return KIND_TONE[(kind ?? "task") as NodeKind] ?? "tone-info";
}

/* --------------------------------------------------------------------- */
/* Фирменный знак                                                         */
/* --------------------------------------------------------------------- */

/**
 * Логотип: орбита с точкой на ней.
 *
 * Метафора продукта в одном знаке — узлы, связанные траекторией. Рисуется
 * вручную, а не берётся из набора значков: это подпись, а не иконка.
 */
export function BrandMark({ size = 28 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 28 28"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="14" cy="14" r="4.6" fill="currentColor" />
      <ellipse
        cx="14"
        cy="14"
        rx="12.2"
        ry="6.4"
        stroke="currentColor"
        strokeWidth="1.6"
        opacity="0.55"
        transform="rotate(-28 14 14)"
      />
      <circle cx="24" cy="8.4" r="2.1" fill="currentColor" opacity="0.85" />
    </svg>
  );
}

/**
 * Орбитальный водяной знак полотна.
 *
 * Держится на грани видимости (прозрачность задана в CSS) и не несёт никакой
 * информации — это фирменный след, а не элемент схемы. Поэтому `aria-hidden`
 * и никаких обработчиков.
 */
export function OrbitWatermark() {
  return (
    <svg className="orbit" viewBox="0 0 520 520" aria-hidden="true" focusable="false">
      <g className="orbit-ring orbit-ring-1">
        <ellipse cx="260" cy="260" rx="240" ry="120" stroke="currentColor" strokeWidth="1.5" fill="none" />
        <circle cx="500" cy="260" r="7" fill="currentColor" />
      </g>
      <g className="orbit-ring orbit-ring-2" transform="rotate(58 260 260)">
        <ellipse cx="260" cy="260" rx="190" ry="95" stroke="currentColor" strokeWidth="1.5" fill="none" />
        <circle cx="70" cy="260" r="5" fill="currentColor" />
      </g>
      <g className="orbit-ring orbit-ring-3" transform="rotate(-34 260 260)">
        <ellipse cx="260" cy="260" rx="135" ry="68" stroke="currentColor" strokeWidth="1.5" fill="none" />
      </g>
      <circle cx="260" cy="260" r="34" fill="currentColor" />
    </svg>
  );
}
