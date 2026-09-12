import type { ActionValidation } from "../api/client";
import type { ActionKind, EngineCapabilities, UiManifest, WidgetAction } from "../manifest/types";
import type { RuntimeSnapshot } from "../runtime/types";

const SYSTEM_ACTIONS = new Set<ActionKind>([
  "thread.create",
  "run.start",
  "run.stop",
  "run.retry",
  "thread.fork",
  "interrupt.resume",
  "artifact.open",
  "artifact.edit",
  "artifact.compare",
  "publication.open",
  "resource.refresh",
]);
const MUTATING_ACTIONS = new Set<ActionKind>([
  "thread.create",
  "run.start",
  "run.stop",
  "run.retry",
  "thread.fork",
  "interrupt.resume",
  "artifact.edit",
]);

type Handlers = Partial<Record<ActionKind, (payload: unknown, action: WidgetAction) => void | Promise<void>>>;

export class ActionDispatcher {
  private inflight = new Set<string>();

  private readonly validate: (body: Record<string, unknown>) => Promise<ActionValidation>;
  private readonly manifest: UiManifest;
  private readonly capabilities: EngineCapabilities | null;
  private readonly runtime: () => RuntimeSnapshot;
  private readonly handlers: Handlers;

  constructor(
    manifest: UiManifest,
    capabilities: EngineCapabilities | null,
    runtime: () => RuntimeSnapshot,
    handlers: Handlers,
    validate: (body: Record<string, unknown>) => Promise<ActionValidation>,
  ) {
    this.validate = validate;
    this.manifest = manifest;
    this.capabilities = capabilities;
    this.runtime = runtime;
    this.handlers = handlers;
  }

  can(kind: ActionKind): boolean {
    if (!SYSTEM_ACTIONS.has(kind)) return false;
    if (!(this.manifest.actions ?? []).some((action) => action.kind === kind)) return false;
    const status = this.runtime().runStatus;
    if (kind === "run.stop") {
      return this.manifest.capabilities?.stop_run === true && ["queued", "running"].includes(status);
    }
    if (kind === "interrupt.resume") {
      return this.manifest.capabilities?.resume_interrupt === true && status === "interrupted";
    }
    if (kind === "run.retry") {
      return this.manifest.capabilities?.retry_node === true && this.capabilities?.features.node_retry === true;
    }
    if (kind === "thread.fork") return this.capabilities?.features.thread_fork === true;
    if (kind === "run.start") return !["queued", "running"].includes(status);
    return true;
  }

  async dispatch(action: WidgetAction): Promise<boolean> {
    if (!this.can(action.kind)) throw new Error(`действие ${action.kind} недоступно`);
    const handler = this.handlers[action.kind];
    if (!handler) throw new Error(`для действия ${action.kind} нет локального adapter`);
    const idempotencyKey = crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
    if (this.inflight.has(action.kind)) return false;
    this.inflight.add(action.kind);
    try {
      // Preflight validates the payload; it does not consume execution.
      // The actual resume command is addressed to a LangGraph interrupt ID.
      if (MUTATING_ACTIONS.has(action.kind)) {
        // Отказ валидации бросает исключение и не даёт дойти до adapter.
        await this.validate({
          graph_id: this.manifest.graph_id,
          kind: action.kind,
          ...(action.interruptId ? { interrupt_id: action.interruptId } : {}),
          ...(action.ruleId ? { interrupt_rule_id: action.ruleId } : {}),
          ...(this.runtime().threadId ? { thread_id: this.runtime().threadId } : {}),
          ...(this.runtime().runId ? { run_id: this.runtime().runId } : {}),
          payload: action.payload,
          idempotency_key: idempotencyKey,
        });
      }
      await handler(action.payload, action);
      return true;
    } finally {
      this.inflight.delete(action.kind);
    }
  }
}
