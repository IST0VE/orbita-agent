/**
 * Верхняя строка приложения.
 *
 * Слева — кто мы и в каком режиме работаем, справа — состояние связи и
 * действия над сеансом. Между ними навигация: она не меняет адрес (в
 * приложении один экран), а приводит внимание туда, где лежит названное, —
 * поэтому это кнопки, а не ссылки.
 */

import type { Assistant, ServerStatus } from "../api";
import { RUN_LABELS, RUN_TONES } from "../engine/runtime/labels";
import type { RunStatus } from "../engine/runtime/types";
import { AgentPicker } from "../panels/Agents";
import { Spinner, StatusDot } from "../ui";
import {
  Bell,
  BrandMark,
  Orbit,
  PanelLeft,
  PanelRight,
  Plus,
  ScrollText,
  Settings,
} from "../ui/icons";

const CONNECTION: Record<string, { label: string; tone: "ok" | "warn" | "bad" }> = {
  ok: { label: "На связи", tone: "ok" },
  unauthorized: { label: "Нет доступа", tone: "warn" },
  offline: { label: "Нет сервера", tone: "bad" },
};

export type NavTarget = "projects" | "analytics" | "knowledge" | "settings";

const NAV: Array<{ id: NavTarget; label: string; hint: string }> = [
  { id: "projects", label: "Проекты", hint: "Папки задач и их материалы" },
  { id: "analytics", label: "Аналитика", hint: "Схема конвейера" },
  { id: "knowledge", label: "База знаний", hint: "Опубликованные документы" },
  { id: "settings", label: "Настройки", hint: "Переменные окружения агента" },
];

export function TopBar({
  assistants,
  assistantId,
  manifestInfo,
  onSelectAssistant,
  locked,
  online,
  runStatus,
  running,
  onNavigate,
  navActive,
  onNewThread,
  onToggleLog,
  logOpen,
  alerts,
  animated,
  onToggleAnimation,
  sidebarOpen,
  onToggleSidebar,
  inspectorOpen,
  onToggleInspector,
}: {
  assistants: Assistant[];
  assistantId: string;
  manifestInfo: Record<string, { label: string; hint: string }>;
  onSelectAssistant: (id: string) => void;
  /** Во время прогона граф и тред не переключаются. */
  locked: boolean;
  online: ServerStatus | null;
  runStatus: RunStatus;
  running: boolean;
  onNavigate: (target: NavTarget) => void;
  navActive: NavTarget;
  onNewThread: () => void;
  onToggleLog: () => void;
  logOpen: boolean;
  /** Сколько в текущем прогоне того, о чём стоит сказать: отказы и остановки. */
  alerts: number;
  animated: boolean;
  onToggleAnimation: () => void;
  sidebarOpen: boolean;
  onToggleSidebar: () => void;
  inspectorOpen: boolean;
  onToggleInspector: () => void;
}) {
  const connection = online === null
    ? { label: "Связь…", tone: "idle" as const }
    : CONNECTION[online] ?? CONNECTION.offline;

  return (
    <header className="app-header">
      <span className="brand">
        <span className="brand-mark"><BrandMark size={28} /></span>
        <span className="brand-name">ORBITA</span>
      </span>

      <AgentPicker
        assistants={assistants}
        selected={assistantId}
        onSelect={onSelectAssistant}
        disabled={locked}
        info={manifestInfo}
      />

      <nav className="app-nav" aria-label="Разделы">
        {NAV.map((item) => (
          <button
            key={item.id}
            className="nav-link"
            title={item.hint}
            aria-current={navActive === item.id ? "page" : undefined}
            onClick={() => onNavigate(item.id)}
          >
            {item.label}
          </button>
        ))}
      </nav>

      <span className="header-spacer" />

      <span className="status" aria-live="polite">
        <Spinner on={running} />
        <span className="status-link">
          <StatusDot tone={connection.tone} />
          {connection.label}
        </span>
        <span className="status-link status-run">
          <StatusDot tone={RUN_TONES[runStatus]} />
          {RUN_LABELS[runStatus]}
        </span>
      </span>

      <span className="header-actions">
        <button
          className="btn-ghost btn-icon"
          aria-label="Показать или скрыть левую колонку"
          aria-pressed={sidebarOpen}
          title="Левая колонка"
          onClick={onToggleSidebar}
        >
          <PanelLeft size={18} aria-hidden="true" />
        </button>
        <button
          className="btn-ghost btn-icon"
          aria-label="Показать или скрыть правую колонку"
          aria-pressed={inspectorOpen}
          title="Правая колонка"
          onClick={onToggleInspector}
        >
          <PanelRight size={18} aria-hidden="true" />
        </button>

        <span className="header-divider" aria-hidden="true" />

        <button disabled={locked} onClick={onNewThread}>
          <Plus size={16} aria-hidden="true" />
          Новый диалог
        </button>
        <button
          className="btn-ghost btn-icon"
          aria-pressed={logOpen}
          aria-label="Журнал выполнения"
          title="Журнал выполнения"
          onClick={onToggleLog}
        >
          <ScrollText size={18} aria-hidden="true" />
        </button>
        {/*
          Колокольчик сообщает о том, что уже произошло в этом прогоне:
          отказах и остановках. Пока их нет, он выключен — кнопка, которая
          ничего не делает, но выглядит нажимаемой, хуже её отсутствия.
        */}
        <button
          className="btn-ghost btn-icon header-alerts"
          aria-label={alerts ? `Событий, требующих внимания: ${alerts}` : "Уведомлений нет"}
          title={alerts ? `Событий, требующих внимания: ${alerts}` : "Уведомлений нет"}
          disabled={!alerts}
          onClick={onToggleLog}
        >
          <Bell size={18} aria-hidden="true" />
          {alerts ? <span className="dot dot-bad bell-dot" aria-hidden="true" /> : null}
        </button>
        <button
          className="btn-ghost btn-icon"
          aria-label="Движение фона"
          aria-pressed={animated}
          title={animated ? "Движение фона: включено" : "Движение фона: выключено"}
          onClick={onToggleAnimation}
        >
          <Orbit size={18} aria-hidden="true" />
        </button>
        <button
          className="btn-ghost btn-icon"
          aria-label="Настройки"
          title="Настройки"
          onClick={() => onNavigate("settings")}
        >
          <Settings size={18} aria-hidden="true" />
        </button>
        <span className="avatar" title="Оператор" aria-hidden="true">ОП</span>
      </span>
    </header>
  );
}
