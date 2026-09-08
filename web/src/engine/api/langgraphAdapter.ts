import type { RuntimeEvent } from "../runtime/types";

/** The stream hides exception text but preserves its class in Error.name. */
export function runErrorMessage(error: unknown): string {
  const source = error && typeof error === "object"
    ? error as { name?: unknown; error?: unknown; code?: unknown; message?: unknown } : {};
  const name = String(source.name ?? source.error ?? source.code ?? "");
  if (name === "AuthenticationError" || name === "PermissionDeniedError") {
    return "API модели отказал в доступе. Проверьте ключ провайдера и доступ к выбранной модели (LLM_MODEL).";
  }
  if (name === "RateLimitError") return "API модели ограничил запросы или квоту. Проверьте лимиты и повторите позже.";
  if (name === "APIConnectionError" || name === "APITimeoutError") {
    return "Не удалось получить ответ от API модели. Проверьте соединение и доступность сервера модели.";
  }
  const message = String(source.message ?? error ?? "Ошибка выполнения");
  if (message === "An internal error occurred") {
    const kind = /^[A-Za-z][A-Za-z0-9_]{0,79}$/.test(name) ? ` (${name})` : "";
    return `Ошибка выполнения${kind}. Подробности доступны в журнале сервера.`;
  }
  return message;
}

export class LiveEventFactory {
  private sequence = 0;
  private runCounter = 0;
  private runId = "";
  private attachedThreadId = "";
  private attempts = new Map<string, number>();
  private tasks = new Map<string, { nodeId: string; attempt: number }>();
  // Поля объявлены и присвоены вручную, а не через parameter properties:
  // так модуль читается `node --experimental-strip-types`, и адаптер попадает
  // под те же тесты, что и редьюсер.
  private readonly assistantId: string;
  private readonly graphId: string;
  private readonly threadId: () => string;

  constructor(assistantId: string, graphId: string, threadId: () => string) {
    this.assistantId = assistantId;
    this.graphId = graphId;
    this.threadId = threadId;
  }

  startRun(): RuntimeEvent {
    this.runCounter += 1;
    this.runId = `live-${Date.now()}-${this.runCounter}`;
    this.sequence = 0;
    this.attempts.clear();
    this.tasks.clear();
    return this.event("run.started", { lifecycle: "live-derived" });
  }

  created(runId: string, threadId: string): RuntimeEvent {
    this.runId = runId;
    this.attachedThreadId = threadId;
    return this.event("run.created", { runId, threadId });
  }

  nodeStarted(nodeId: string, executionId: string, input: unknown): RuntimeEvent {
    const attempt = (this.attempts.get(nodeId) ?? 0) + 1;
    this.attempts.set(nodeId, attempt);
    this.tasks.set(executionId, { nodeId, attempt });
    return this.event("node.started", { nodeId, executionId, attempt, input });
  }

  nodeUpdated(nodeId: string, update: unknown): RuntimeEvent {
    const attempt = this.attempts.get(nodeId) ?? 1;
    const executionId = [...this.tasks.entries()]
      .reverse()
      .find(([, task]) => task.nodeId === nodeId)?.[0] ?? `${nodeId}:${attempt}`;
    return this.event("node.update", { nodeId, executionId, attempt, update });
  }

  nodeCompleted(nodeId: string, executionId: string, update: unknown): RuntimeEvent {
    const task = this.tasks.get(executionId);
    const attempt = task?.attempt ?? (this.attempts.get(nodeId) ?? 0) + 1;
    if (!task) this.attempts.set(nodeId, attempt);
    return this.event("node.completed", {
      nodeId,
      attempt,
      executionId,
      update,
    });
  }

  nodeFailed(nodeId: string, executionId: string, error: unknown): RuntimeEvent {
    const task = this.tasks.get(executionId);
    const attempt = task?.attempt ?? (this.attempts.get(nodeId) ?? 0) + 1;
    return this.event("node.failed", { nodeId, executionId, attempt, error });
  }

  snapshot(state: Record<string, unknown>): RuntimeEvent {
    return this.event("state.snapshot", { state });
  }

  interrupt(value: unknown, nodeId?: string): RuntimeEvent {
    return this.event("interrupt.created", {
      interruptId: `interrupt-${this.runId}`,
      value,
      nodeId,
    });
  }

  resolved(interruptId: string): RuntimeEvent {
    return this.event("interrupt.resolved", { interruptId });
  }

  completed(): RuntimeEvent {
    return this.event("run.completed", { lifecycle: "live-derived" });
  }

  cancelled(): RuntimeEvent {
    return this.event("run.cancelled", { lifecycle: "sdk-stop" });
  }

  failed(error: unknown): RuntimeEvent {
    const value = runErrorMessage(error);
    const source = error && typeof error === "object" ? error as { name?: string; error?: string } : {};
    return this.event("run.failed", {
      error: { code: source.name ?? source.error ?? "run_failed", message: value, category: "graph", retryable: false },
    });
  }

  private event(type: string, data: unknown): RuntimeEvent {
    this.sequence += 1;
    const timestamp = new Date().toISOString();
    return {
      eventId: `${this.runId}:${this.sequence}`,
      sequence: this.sequence,
      timestamp,
      graphId: this.graphId,
      assistantId: this.assistantId,
      threadId: this.attachedThreadId || this.threadId(),
      runId: this.runId,
      type,
      data,
      schemaVersion: "1.0",
      receivedAt: timestamp,
    };
  }
}
