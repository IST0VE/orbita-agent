import type {
  NodeExecution,
  RuntimeAction,
  RuntimeError,
  RuntimeEvent,
  RuntimeInterrupt,
  RuntimeSnapshot,
} from "./types";

export function createRuntimeSnapshot(
  assistantId: string,
  graphId: string,
  manifestVersion = "0.0.fallback",
  topologyHash = "",
): RuntimeSnapshot {
  return {
    assistantId,
    graphId,
    threadId: null,
    runId: null,
    topologyHash,
    manifestVersion,
    connection: "idle",
    runStatus: "idle",
    state: {},
    executions: {},
    executionOrder: [],
    interrupts: [],
    events: [],
    lastSequence: 0,
    bufferedEvents: {},
  };
}

function dataOf(event: RuntimeEvent): Record<string, unknown> {
  return event.data && typeof event.data === "object" ? (event.data as Record<string, unknown>) : {};
}

function runtimeError(value: unknown, fallback: string): RuntimeError {
  if (value && typeof value === "object") {
    const source = value as Partial<RuntimeError>;
    return {
      code: source.code ?? fallback,
      message: source.message ?? fallback,
      category: source.category ?? "graph",
      retryable: source.retryable ?? false,
      correlationId: source.correlationId,
      nodeId: source.nodeId,
      executionId: source.executionId,
    };
  }
  return { code: fallback, message: String(value ?? fallback), category: "graph", retryable: false };
}

function executionFor(state: RuntimeSnapshot, event: RuntimeEvent): NodeExecution | null {
  const data = dataOf(event);
  const nodeId = String(data.nodeId ?? data.node_id ?? "");
  if (!nodeId) return null;
  const attempt = Number(data.attempt ?? Object.values(state.executions).filter((e) => e.nodeId === nodeId).length + 1);
  const executionId = String(data.executionId ?? data.execution_id ?? `${nodeId}:${attempt}`);
  return (
    state.executions[executionId] ?? {
      executionId,
      nodeId,
      attempt,
      status: "queued",
    }
  );
}

function applyOrdered(state: RuntimeSnapshot, event: RuntimeEvent): RuntimeSnapshot {
  const data = dataOf(event);
  let next: RuntimeSnapshot = {
    ...state,
    threadId: event.threadId || state.threadId,
    runId: event.runId || state.runId,
    lastSequence: event.sequence,
    events: [...state.events, { ...event, receivedAt: event.receivedAt ?? new Date().toISOString() }].slice(-10_000),
  };
  if (event.type === "thread.created") next.threadId = String(data.threadId ?? event.threadId);
  if (event.type === "run.queued" || (event.type === "run.created" && state.runStatus !== "running")) next.runStatus = "queued";
  if (event.type === "run.started") next.runStatus = "running";
  if (event.type === "run.completed" && state.runStatus !== "cancelled" && state.runStatus !== "failed") next.runStatus = "completed";
  if (event.type === "run.failed") {
    next.runStatus = "failed";
    next.error = runtimeError(data.error, "run_failed");
  }
  if (event.type === "run.cancelled") next.runStatus = "cancelled";
  if (event.type === "connection.warning") next.connection = "reconnecting";
  if (event.type === "state.snapshot") {
    const value = data.state;
    if (value && typeof value === "object" && !Array.isArray(value)) {
      next.state = value as Record<string, unknown>;
    }
  }
  if (event.type === "interrupt.created") {
    const interrupt: RuntimeInterrupt = {
      interruptId: String(data.interruptId ?? data.interrupt_id ?? event.eventId),
      nodeId: typeof data.nodeId === "string" ? data.nodeId : undefined,
      value: data.value,
      createdAt: event.timestamp,
      status: "pending",
    };
    next.interrupts = [...next.interrupts.filter((item) => item.interruptId !== interrupt.interruptId), interrupt];
    next.runStatus = "interrupted";
  }
  if (event.type === "interrupt.resolved") {
    const id = String(data.interruptId ?? data.interrupt_id ?? "");
    next.interrupts = next.interrupts.map((item) =>
      !id || item.interruptId === id ? { ...item, status: "resolved", resolvedAt: event.timestamp } : item,
    );
    next.runStatus = "running";
  }
  if (event.type.startsWith("node.")) {
    const execution = executionFor(next, event);
    if (execution) {
      let updated = execution;
      if (event.type === "node.queued") updated = { ...execution, status: "queued" };
      if (event.type === "node.started") updated = { ...execution, status: "running", startedAt: event.timestamp };
      if (event.type === "node.update") updated = { ...execution, update: data.update ?? data.value };
      if (event.type === "node.completed") {
        const started = execution.startedAt ? Date.parse(execution.startedAt) : NaN;
        updated = {
          ...execution,
          status: "completed",
          update: data.update ?? data.value,
          finishedAt: event.timestamp,
          durationMs: Number.isFinite(started) ? Math.max(0, Date.parse(event.timestamp) - started) : undefined,
        };
      }
      if (event.type === "node.failed") {
        updated = {
          ...execution,
          status: "failed",
          finishedAt: event.timestamp,
          error: runtimeError(data.error, "node_failed"),
        };
      }
      const exists = !!next.executions[updated.executionId];
      next.executions = { ...next.executions, [updated.executionId]: updated };
      if (!exists) next.executionOrder = [...next.executionOrder, updated.executionId];
    }
  }
  return next;
}

function drain(state: RuntimeSnapshot): RuntimeSnapshot {
  let next = state;
  while (next.bufferedEvents[next.lastSequence + 1]) {
    const sequence = next.lastSequence + 1;
    const event = next.bufferedEvents[sequence];
    const bufferedEvents = { ...next.bufferedEvents };
    delete bufferedEvents[sequence];
    next = applyOrdered({ ...next, bufferedEvents }, event);
  }
  return next;
}

/**
 * Структурное равенство JSON-подобного состояния LangGraph.
 *
 * `useStream` может отдать тот же снимок под новыми ссылками не только для
 * верхнего `{}` и `[]`, но и для вложенных `artifacts`, `cost`, сообщений и
 * результатов инструментов. Сравнение только верхнего уровня принимало такой
 * снимок за изменение: reconcile обновлял runtime, рендер получал очередные
 * новые ссылки и запускал reconcile снова, пока React не останавливал цикл с
 * `Maximum update depth exceeded`.
 *
 * Значения состояния приходят из JSON API, поэтому рекурсия ограничена
 * массивами и обычными объектами. Нестандартные объекты сравниваются по ссылке.
 */
function sameValue(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) && Array.isArray(right)) {
    return left.length === right.length && left.every((item, index) => sameValue(item, right[index]));
  }
  if (
    left !== null &&
    right !== null &&
    typeof left === "object" &&
    typeof right === "object" &&
    !Array.isArray(left) &&
    !Array.isArray(right) &&
    Object.getPrototypeOf(left) === Object.prototype &&
    Object.getPrototypeOf(right) === Object.prototype
  ) {
    return sameRecord(
      left as Record<string, unknown>,
      right as Record<string, unknown>,
    );
  }
  return false;
}

function sameRecord(left: Record<string, unknown>, right: Record<string, unknown>): boolean {
  const keys = Object.keys(left);
  return (
    keys.length === Object.keys(right).length &&
    keys.every((key) => Object.prototype.hasOwnProperty.call(right, key) && sameValue(left[key], right[key]))
  );
}

function sameSnapshot(left: RuntimeSnapshot, right: RuntimeSnapshot): boolean {
  return (Object.keys(right) as Array<keyof RuntimeSnapshot>).every((key) => {
    if (key === "state") return sameRecord(left.state, right.state);
    if (key === "bufferedEvents") return Object.keys(left.bufferedEvents).length === 0;
    return sameValue(left[key], right[key]);
  });
}

/**
 * Начало нового прогона в том же треде.
 *
 * Нумерация событий идёт от единицы внутри прогона — так их выдаёт и backend
 * (`EventNormalizer`), и live-адаптер. Значит `sequence === 1` при непустом
 * окне — это не опоздавшее событие, а новый прогон, и окно порядка нужно
 * открыть заново. Иначе все события второго прогона отсекаются как старые.
 *
 * Вместе с окном сбрасывается и картина хода: статусы узлов, счётчики циклов
 * и незакрытые остановки принадлежат прошлому прогону. Лента событий не
 * трогается — это журнал треда, а не состояние текущего хода.
 */
function startRunWindow(state: RuntimeSnapshot): RuntimeSnapshot {
  return {
    ...state,
    lastSequence: 0,
    bufferedEvents: {},
    executions: {},
    executionOrder: [],
    interrupts: [],
    error: undefined,
  };
}

export function runtimeReducer(state: RuntimeSnapshot, action: RuntimeAction): RuntimeSnapshot {
  if (action.type === "reset") {
    return createRuntimeSnapshot(
      action.assistantId,
      action.graphId,
      action.manifestVersion,
      action.topologyHash,
    );
  }
  if (action.type === "connection") return state.connection === action.connection ? state : { ...state, connection: action.connection };
  if (action.type === "thread") return state.threadId === action.threadId ? state : { ...state, threadId: action.threadId };
  if (action.type === "error") return { ...state, error: action.error };
  if (action.type === "reconcile") {
    const next: RuntimeSnapshot = {
      ...state,
      ...action.snapshot,
      state: action.snapshot.state,
      connection: "live",
      bufferedEvents: {},
    };
    // Тот же снимок под новой ссылкой — не изменение: вернуть прежний объект
    // значит дать useReducer прервать цепочку рендеров.
    return sameSnapshot(state, next) ? state : next;
  }
  const event = action.event;
  if (event.eventId && state.events.some((item) => item.eventId === event.eventId)) return state;
  const current = event.sequence === 1 && state.lastSequence > 0 ? startRunWindow(state) : state;
  if (event.sequence <= current.lastSequence) return state;
  if (event.sequence > current.lastSequence + 1) {
    return {
      ...current,
      connection: "reconnecting",
      bufferedEvents: { ...current.bufferedEvents, [event.sequence]: event },
    };
  }
  return drain(applyOrdered(current, event));
}
