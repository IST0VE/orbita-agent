import { useEffect, useRef, useState } from "react";
import type { GraphLayout, NodeBox } from "./layout";
import type { EdgeRoutes, RoutingReply, RoutingRequest } from "./routing.worker";

const DEFER_ROUTING_ABOVE = 50;
type Session = { enqueue: (boxes: NodeBox[]) => void };
type Result = { layout: GraphLayout | null; routes: EdgeRoutes | null; error: boolean };

/** Large graphs route off the main thread. A busy worker receives at most one
 * latest snapshot after its current job; outdated results never replace routes. */
export function useEdgeRoutes(layout: GraphLayout | null, boxes: NodeBox[]): {
  routes: EdgeRoutes | null; error: boolean; deferred: boolean;
} {
  const deferred = !!layout && layout.nodes.length > DEFER_ROUTING_ABOVE;
  const session = useRef<Session | null>(null);
  const [result, setResult] = useState<Result>({ layout: null, routes: null, error: false });

  useEffect(() => {
    setResult({ layout, routes: null, error: false });
    if (!deferred || !layout) return;
    let worker: Worker | undefined;
    let disposed = false;
    let failed = false;
    let latest = 0;
    let active: number | null = null;
    let pending: Extract<RoutingRequest, { type: "route" }> | null = null;
    const fail = () => {
      if (disposed || failed) return;
      failed = true;
      active = null;
      pending = null;
      worker?.terminate();
      setResult({ layout, routes: null, error: true });
    };
    const dispatch = () => {
      if (disposed || failed || !worker || active !== null || !pending) return;
      const request = pending;
      pending = null;
      active = request.requestId;
      try { worker.postMessage(request); } catch { fail(); }
    };
    const current: Session = { enqueue: (snapshot) => {
      if (disposed || failed) return;
      pending = { type: "route", requestId: ++latest, boxes: snapshot };
      dispatch();
    } };
    session.current = current;
    try {
      worker = new Worker(new URL("./routing.worker.ts", import.meta.url), { type: "module" });
      worker.onerror = fail;
      worker.onmessageerror = fail;
      worker.onmessage = ({ data }: MessageEvent<RoutingReply>) => {
        if (disposed || failed || data.requestId !== active) return;
        active = null;
        if (data.requestId === latest) {
          if (data.type === "error") { fail(); return; }
          setResult({ layout, routes: data.routes, error: false });
        }
        dispatch();
      };
      worker.postMessage({ type: "init", layout } satisfies RoutingRequest);
    } catch { fail(); }
    return () => {
      disposed = true;
      pending = null;
      worker?.terminate();
      if (session.current === current) session.current = null;
    };
  }, [layout, deferred]);

  useEffect(() => {
    if (deferred) session.current?.enqueue(boxes);
  }, [layout, boxes, deferred]);

  // Do not expose the preceding layout's geometry during an effect transition.
  return deferred && result.layout === layout
    ? { routes: result.routes, error: result.error, deferred }
    : { routes: null, error: false, deferred };
}
