/**
 * Строка контекста под шапкой.
 *
 * Отвечает на три вопроса, которые оператор задаёт себе, вернувшись к экрану:
 * где я, с чем я работаю и что сейчас происходит. Раньше ответ на первый
 * лежал в шапке, на второй — в левой колонке, а на третий — сразу в трёх
 * местах и разными словами.
 *
 * Здесь же стоят действия самого сценария: начать заново и открыть консоль.
 * Глобальных действий здесь нет — они в шапке; действий над объектами нет —
 * они рядом с объектами.
 */

import { RUN_LABELS, RUN_TONES } from "../engine/runtime/labels";
import type { RunStatus } from "../engine/runtime/types";
import {
  ChevronRight,
  FolderOpen,
  PanelLeft,
  PanelRight,
  Plus,
  ScrollText,
  Square,
} from "../ui/icons";
import { StatusDot } from "../ui";

export function ContextBar({
  task,
  onPickTask,
  scenario,
  scenarioHint,
  runStatus,
  running,
  canStop,
  onStop,
  onNewThread,
  newThreadDisabled,
  events,
  consoleOpen,
  onToggleConsole,
  sidebarOpen,
  onToggleSidebar,
  inspectorOpen,
  onToggleInspector,
}: {
  /** Папка задачи: с чем я работаю. Пусто — материалы ещё не выбраны. */
  task: string;
  onPickTask: () => void;
  /** Сценарий: чем я работаю. */
  scenario: string;
  scenarioHint: string;
  /** Что происходит: состояние прогона, и только здесь. */
  runStatus: RunStatus;
  running: boolean;
  /** Сценарий объявил, что прогон можно остановить. */
  canStop: boolean;
  onStop: () => void;
  onNewThread: () => void;
  newThreadDisabled: boolean;
  events: number;
  consoleOpen: boolean;
  onToggleConsole: () => void;
  sidebarOpen: boolean;
  onToggleSidebar: () => void;
  inspectorOpen: boolean;
  onToggleInspector: () => void;
}) {
  return (
    <div className="context-bar">
      {/*
        Крошка начинается с папки задачи. Корнем стояло название раздела —
        неподвижная надпись «Проекты», которая никуда не вела и называла
        сущность, которой нет. Крошка из двух шагов, каждый из которых
        что-то делает, честнее крошки из трёх, где первый шаг декоративный.
      */}
      <nav className="crumbs" aria-label="Контекст работы">
        <button
          type="button"
          className="crumb crumb-action"
          title={task ? `Папка задачи: ${task}. Показать материалы` : "Выбрать папку задачи в материалах"}
          onClick={onPickTask}
        >
          <FolderOpen size={14} aria-hidden="true" />
          <span className="truncate">{task || "Материалы не выбраны"}</span>
        </button>
        <ChevronRight className="crumb-sep" size={14} aria-hidden="true" />
        <span className="crumb crumb-current truncate" title={scenarioHint}>{scenario}</span>
      </nav>

      <div className="context-actions">
        <span className={`run-badge run-${runStatus}`} aria-live="polite">
          <StatusDot tone={RUN_TONES[runStatus]} />
          {RUN_LABELS[runStatus]}
        </span>

        {/* Остановка стоит рядом с состоянием прогона: останавливают ход,
            а не поле ввода, и смотрят при этом сюда. */}
        {running && canStop ? (
          <button className="btn-danger btn-sm" title="Остановить идущий прогон" onClick={onStop}>
            <Square size={14} aria-hidden="true" />
            Остановить
          </button>
        ) : null}

        <button
          className="btn-ghost btn-sm"
          disabled={newThreadDisabled}
          title="Начать новый прогон: очистить тред, сообщения и результаты"
          onClick={onNewThread}
        >
          <Plus size={15} aria-hidden="true" />
          Новый прогон
        </button>

        {/*
          Три переключателя панелей рядом: показать материалы, показать
          подробности, показать консоль. Это один вид действий — «что ещё
          видно на экране», — и место у них одно.
        */}
        <span className="panel-toggles">
          <button
            className="btn-ghost btn-icon btn-sm"
            aria-label="Колонка материалов"
            aria-pressed={sidebarOpen}
            title={sidebarOpen ? "Скрыть материалы" : "Показать материалы"}
            onClick={onToggleSidebar}
          >
            <PanelLeft size={15} aria-hidden="true" />
          </button>
          <button
            className="btn-ghost btn-icon btn-sm"
            aria-label="Колонка подробностей"
            aria-pressed={inspectorOpen}
            title={inspectorOpen ? "Скрыть подробности" : "Показать подробности"}
            onClick={onToggleInspector}
          >
            <PanelRight size={15} aria-hidden="true" />
          </button>
        <button
          className="btn-ghost btn-sm console-toggle"
          aria-label="Консоль выполнения"
          aria-pressed={consoleOpen}
          title={consoleOpen ? "Скрыть консоль выполнения" : "Показать консоль выполнения"}
          onClick={onToggleConsole}
        >
          <ScrollText size={15} aria-hidden="true" />
          {events ? <span className="console-toggle-count">{events}</span> : null}
          {running ? <span className="dot dot-run" aria-hidden="true" /> : null}
        </button>
        </span>
      </div>
    </div>
  );
}
