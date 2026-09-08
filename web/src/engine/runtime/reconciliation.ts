import type { RuntimeSnapshot } from "./types";

export function reconcileSnapshot(
  local: RuntimeSnapshot,
  serverState: Record<string, unknown>,
  patch: Partial<RuntimeSnapshot> = {},
): RuntimeSnapshot {
  return {
    ...local,
    ...patch,
    state: serverState,
    connection: "live",
    bufferedEvents: {},
    // Only server-confirmed optimistic state survives. Draft form values live
    // outside RuntimeSnapshot and are therefore not accidentally committed.
    error: patch.error,
  };
}
