/**
 * Слова, которыми состояния называются на экране.
 *
 * Один словарь на весь интерфейс: одно и то же состояние в шапке, в схеме, в
 * списке узлов и в карточке узла обязано называться одинаково. Пока подписи
 * стояли по месту употребления, прогон успевал побывать и «остановлен», и
 * «cancelled» на одном экране.
 */

import type { ExecutionStatus, RunStatus } from "./types";

export type Tone = "ok" | "run" | "warn" | "bad" | "idle" | "brand";

export const RUN_LABELS: Record<RunStatus, string> = {
  idle: "Готов к работе",
  queued: "В очереди",
  running: "Выполняется",
  interrupted: "Ждёт решения",
  completed: "Завершён",
  failed: "Ошибка",
  cancelled: "Остановлен",
};

export const RUN_TONES: Record<RunStatus, Tone> = {
  idle: "idle",
  queued: "run",
  running: "run",
  interrupted: "warn",
  completed: "ok",
  failed: "bad",
  cancelled: "idle",
};

/** Что интерфейс говорит о состоянии прогона одной строкой пояснения. */
export const RUN_NOTES: Record<RunStatus, string> = {
  idle: "Все системы в норме.",
  queued: "Задача принята и ждёт очереди.",
  running: "Конвейер выполняет этапы.",
  interrupted: "Нужно решение оператора.",
  completed: "Прогон завершён, результаты ниже.",
  failed: "Прогон остановлен ошибкой.",
  cancelled: "Прогон остановлен оператором.",
};

export type NodeStatus = ExecutionStatus | "idle";

export const NODE_LABELS: Record<NodeStatus, string> = {
  idle: "Не выполнялся",
  queued: "В очереди",
  running: "Выполняется",
  completed: "Выполнен",
  failed: "Ошибка",
  cancelled: "Остановлен",
  interrupted: "Ждёт решения",
};

export const NODE_TONES: Record<NodeStatus, Tone> = {
  idle: "idle",
  queued: "run",
  running: "run",
  completed: "ok",
  failed: "bad",
  cancelled: "idle",
  interrupted: "warn",
};

/**
 * Тип узла словами.
 *
 * Манифест называет род занятий узла по-английски, потому что это ключ, а не
 * подпись. Подпись — здесь.
 */
export const KIND_LABELS: Record<string, string> = {
  task: "Этап",
  router: "Ворота",
  tool: "Инструменты",
  approval: "Подтверждение",
  system: "Система",
};

/**
 * Событие журнала словами.
 *
 * Ключи приходят из `engine/api/langgraphAdapter.ts`. Незнакомое событие
 * показывается как есть: выдумывать ему перевод хуже, чем показать ключ.
 */
export const EVENT_LABELS: Record<string, string> = {
  "run.created": "Задача принята",
  "run.queued": "В очереди",
  "run.started": "Прогон начат",
  "run.completed": "Прогон завершён",
  "run.failed": "Ошибка прогона",
  "run.cancelled": "Прогон остановлен",
  "node.queued": "Узел в очереди",
  "node.started": "Узел начат",
  "node.update": "Обновление узла",
  "node.completed": "Узел выполнен",
  "node.failed": "Узел с ошибкой",
  "state.snapshot": "Снимок состояния",
  "interrupt.created": "Требуется решение",
  "interrupt.resolved": "Решение принято",
};

export const EVENT_TONES: Record<string, Tone> = {
  "run.created": "run",
  "run.queued": "run",
  "run.started": "run",
  "run.completed": "ok",
  "run.failed": "bad",
  "run.cancelled": "idle",
  "node.queued": "run",
  "node.started": "run",
  "node.update": "idle",
  "node.completed": "ok",
  "node.failed": "bad",
  "state.snapshot": "idle",
  "interrupt.created": "warn",
  "interrupt.resolved": "ok",
};
