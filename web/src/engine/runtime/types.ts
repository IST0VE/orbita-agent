export type ConnectionStatus = "idle" | "connecting" | "live" | "reconnecting" | "offline";
export type RunStatus = "idle" | "queued" | "running" | "interrupted" | "completed" | "failed" | "cancelled";
export type ExecutionStatus = "queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted";

export type RuntimeError = {
  code: string;
  message: string;
  category: "validation" | "auth" | "network" | "server" | "graph" | "widget" | "unknown";
  retryable: boolean;
  correlationId?: string;
  nodeId?: string;
  executionId?: string;
};

export type NodeExecution = {
  executionId: string;
  nodeId: string;
  attempt: number;
  status: ExecutionStatus;
  startedAt?: string;
  finishedAt?: string;
  durationMs?: number;
  update?: unknown;
  error?: RuntimeError;
};

export type RuntimeInterrupt = {
  interruptId: string;
  nodeId?: string;
  value: unknown;
  createdAt?: string;
  resolvedAt?: string;
  status: "pending" | "resolved";
};

export type RuntimeEvent<T = unknown> = {
  eventId: string;
  sequence: number;
  timestamp: string;
  graphId: string;
  assistantId: string;
  threadId: string;
  runId: string;
  type: string;
  data: T;
  schemaVersion: "1.0";
  receivedAt?: string;
};

export type RuntimeSnapshot = {
  assistantId: string;
  graphId: string;
  threadId: string | null;
  runId: string | null;
  topologyHash: string;
  manifestVersion: string;
  connection: ConnectionStatus;
  runStatus: RunStatus;
  state: Record<string, unknown>;
  executions: Record<string, NodeExecution>;
  executionOrder: string[];
  interrupts: RuntimeInterrupt[];
  events: RuntimeEvent[];
  lastSequence: number;
  bufferedEvents: Record<number, RuntimeEvent>;
  error?: RuntimeError;
};

export type RuntimeAction =
  | { type: "event"; event: RuntimeEvent }
  | { type: "connection"; connection: ConnectionStatus }
  | { type: "reconcile"; snapshot: Partial<RuntimeSnapshot> & { state: Record<string, unknown> } }
  | { type: "reset"; assistantId: string; graphId: string; manifestVersion: string; topologyHash?: string }
  | { type: "thread"; threadId: string | null }
  | { type: "error"; error?: RuntimeError };
