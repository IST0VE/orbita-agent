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
  MessageSquare,
  PanelLeft,
  PanelRight,
  Pause,
  Plus,
  ScrollText,
  Square,
} from "../ui/icons";
import { StatusDot } from "../ui";

export function ContextBar({
  chat,
  fileCount,
  onShowChat,
  scenario,
  scenarioHint,
  runStatus,
  running,
  canStop,
  onStop,
  canPause,
  pauseRequested,
  onPause,
  onCancelPause,
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
  /** Открытый чат: с чем я работаю. */
  chat: string;
  /** Сколько файлов загружено в этот чат. */
  fileCount: number;
  onShowChat: () => void;
  /** Сценарий: чем я работаю. */
  scenario: string;
  scenarioHint: string;
  /** Что происходит: состояние прогона, и только здесь. */
  runStatus: RunStatus;
  running: boolean;
  /** Сценарий объявил, что прогон можно остановить. */
  canStop: boolean;
  onStop: () => void;
  /** Сценарий объявил, что прогон можно поставить на паузу. */
  canPause: boolean;
  /** Заявка оставлена, но граф до ближайшей границы шага ещё не дошёл. */
  pauseRequested: boolean;
  onPause: () => void;
  onCancelPause: () => void;
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
        Крошка начинается с открытого чата: раньше здесь стояла папка задачи,
        общая для всех вошедших. Чат свой, и его файлы видны только в нём —
        щелчок открывает их в правой колонке.
      */}
      <nav className="crumbs" aria-label="Контекст работы">
        <button
          type="button"
          className="crumb crumb-action"
          title={`Чат «${chat}». Файлов: ${fileCount}. Показать файлы чата`}
          onClick={onShowChat}
        >
          <MessageSquare size={14} aria-hidden="true" />
          <span className="truncate">{chat}</span>
          {fileCount ? <span className="crumb-count">{fileCount}</span> : null}
        </button>
        <ChevronRight className="crumb-sep" size={14} aria-hidden="true" />
        <span className="crumb crumb-current truncate" title={scenarioHint}>{scenario}</span>
      </nav>

      <div className="context-actions">
        <span className={`run-badge run-${runStatus}`} aria-live="polite">
          <StatusDot tone={RUN_TONES[runStatus]} />
          {RUN_LABELS[runStatus]}
        </span>

        {/*
          Пауза стоит перед остановкой: она мягче и нужна чаще. Остановка
          обрывает ход, и начатый этап придётся оплачивать заново; пауза
          доводит этап до конца, замораживает тред и ждёт, что оператор
          допишет. Пока заявка не взята, кнопка показывает именно это —
          ожидание, а не остановку, — и позволяет передумать.
        */}
        {running && canPause ? (
          pauseRequested ? (
            <button
              className="btn-ghost btn-sm pause-pending"
              title="Заявка оставлена: граф остановится перед следующим обращением к модели. Нажмите, чтобы отменить"
              onClick={onCancelPause}
            >
              <Pause size={14} aria-hidden="true" />
              Пауза запрошена
            </button>
          ) : (
            <button
              className="btn-ghost btn-sm"
              title="Остановить прогон на ближайшей границе шага: можно будет дописать и продолжить"
              onClick={onPause}
            >
              <Pause size={14} aria-hidden="true" />
              Пауза
            </button>
          )
        ) : null}

        {/* Остановка стоит рядом с состоянием прогона: останавливают ход,
            а не поле ввода, и смотрят при этом сюда. */}
        {running && canStop ? (
          <button className="btn-danger btn-sm" title="Остановить идущий прогон" onClick={onStop}>
            <Square size={14} aria-hidden="true" />
            Остановить
          </button>
        ) : null}

        <button
          className="btn-ghost btn-sm new-chat"
          disabled={newThreadDisabled}
          title="Начать новый чат: прежний останется в списке слева вместе с файлами"
          onClick={onNewThread}
        >
          <Plus size={15} aria-hidden="true" />
          Новый чат
        </button>

        {/*
          Три переключателя панелей рядом: показать материалы, показать
          подробности, показать консоль. Это один вид действий — «что ещё
          видно на экране», — и место у них одно.
        */}
        <span className="panel-toggles">
          <button
            className="btn-ghost btn-icon btn-sm"
            aria-label="Колонка чатов"
            aria-pressed={sidebarOpen}
            title={sidebarOpen ? "Скрыть список чатов" : "Показать список чатов"}
            onClick={onToggleSidebar}
          >
            <PanelLeft size={15} aria-hidden="true" />
          </button>
          <button
            className="btn-ghost btn-icon btn-sm"
            aria-label="Колонка файлов и подробностей"
            aria-pressed={inspectorOpen}
            title={inspectorOpen ? "Скрыть файлы и подробности" : "Показать файлы чата и подробности"}
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
