import type { ExecutionStatus, RuntimeSnapshot } from "./types";

export function executionPath(state: RuntimeSnapshot): string[] {
  return state.executionOrder
    .map((id) => state.executions[id])
    .filter((execution) => execution?.status === "completed")
    .map((execution) => execution.nodeId);
}

export function activeNodes(state: RuntimeSnapshot): string[] {
  return state.executionOrder
    .map((id) => state.executions[id])
    .filter((execution) => execution?.status === "running")
    .map((execution) => execution.nodeId);
}

export function waitingNodes(state: RuntimeSnapshot): string[] {
  return state.interrupts
    .filter((interrupt) => interrupt.status === "pending" && interrupt.nodeId)
    .map((interrupt) => interrupt.nodeId as string);
}

export function nodeStatus(state: RuntimeSnapshot, nodeId: string): ExecutionStatus | "idle" {
  if (waitingNodes(state).includes(nodeId)) return "interrupted";
  const executions = state.executionOrder
    .map((id) => state.executions[id])
    .filter((execution) => execution?.nodeId === nodeId);
  return executions[executions.length - 1]?.status ?? "idle";
}

export function visitCount(state: RuntimeSnapshot, nodeId: string): number {
  return Object.values(state.executions).filter(
    (execution) => execution.nodeId === nodeId && execution.status === "completed",
  ).length;
}
