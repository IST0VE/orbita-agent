/** Declarative LangGraph UI assembled from topology, manifest and runtime events. */

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { useStream } from "@langchain/langgraph-sdk/react";
import type { Message } from "@langchain/langgraph-sdk";

import { API_URL, checkServer, loadAssistants, type Assistant, type ServerStatus } from "./api";
import { authorizedFetch } from "./auth";
import { ActionDispatcher } from "./engine/actions/dispatcher";
import {
  loadCapabilities,
  loadManifest,
  loadResource,
  loadUiBundle,
  mutateResource,
  validateAction,
  type UiBundle,
} from "./engine/api/client";
import { LiveEventFactory, runErrorMessage } from "./engine/api/langgraphAdapter";
import { GraphCanvas } from "./engine/canvas/GraphCanvas";
import { configurableOf } from "./engine/manifest/bindings";
import type {
  EngineCapabilities,
  SafeWidgetContext,
  UiManifest,
  WidgetAction,
} from "./engine/manifest/types";
import { localized } from "./engine/manifest/validate";
import { createRuntimeSnapshot, runtimeReducer } from "./engine/runtime/reducer";
import { SafeMarkdown } from "./engine/security/safeMarkdown";
import { InterruptSurface, SurfaceRenderer } from "./engine/surfaces/SurfaceRenderer";
import { Timeline } from "./engine/timeline/Timeline";
import type { OrbitaState } from "./lib/orbita";
import { AgentPicker, graphInfo, sortAssistants } from "./panels/Agents";
import { SettingsOverlay } from "./panels/Settings";
import { Orbit, Panel, Spinner } from "./ui";

type StateType = { messages: Message[] } & OrbitaState & Record<string, unknown>;

const GRAPH_KEY = "orbita.graph";
const LEFT_WIDTH_KEY = "orbita.leftWidth";
/** Границы ширины левой колонки: уже — список нечитаем, шире — незачем. */
const LEFT_MIN = 180;
const LEFT_MAX = 640;
const TASK_KEY = "orbita.task";
const DEFAULT_GRAPH = "agent";
const RUN_LABELS = { idle: "готов к работе", queued: "в очереди", running: "выполняется", interrupted: "ждёт решения", completed: "завершён", failed: "ошибка", cancelled: "остановлен" };
const apiUrl = API_URL || window.location.origin;
const threadKey = (graphId: string) => `orbita.thread.${graphId}`;

function pickAssistant(list: Assistant[], wanted: string): Assistant | undefined {
  return list.find((item) => item.assistant_id === wanted || item.graph_id === wanted) ?? list[0];
}

function waitingNode(interrupt: { ns?: string[] } | undefined): string | undefined {
  const namespace = interrupt?.ns;
  return namespace?.length ? namespace[namespace.length - 1].split(":")[0] || undefined : undefined;
}

export function App() {
  const [assistants, setAssistants] = useState<Assistant[]>([]);
  const [assistantId, setAssistantId] = useState(
    () => localStorage.getItem(GRAPH_KEY) || DEFAULT_GRAPH,
  );
  const [assistantsError, setAssistantsError] = useState<string | null>(null);
  const [manifestInfo, setManifestInfo] = useState<
    Record<string, { label: string; hint: string }>
  >({});
  const [bundle, setBundle] = useState<UiBundle | null>(null);
  const [bundleError, setBundleError] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<EngineCapabilities | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [inputs, setInputs] = useState<Record<string, unknown>>(() => ({
    task: localStorage.getItem(TASK_KEY) || "",
  }));
  const [online, setOnline] = useState<ServerStatus | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [animated, setAnimated] = useState(() => localStorage.getItem("orbita.animation") === "1");
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  /** Открытый документ занимает главный экран вместо схемы. */
  const [openDoc, setOpenDoc] = useState<{ title: string; text: string } | null>(null);
  const [leftWidth, setLeftWidth] = useState(
    () => Number(localStorage.getItem(LEFT_WIDTH_KEY)) || 0,
  );
  const [actionError, setActionError] = useState<string | null>(null);
  const [runtime, dispatch] = useReducer(
    runtimeReducer,
    createRuntimeSnapshot(assistantId, DEFAULT_GRAPH),
  );
  const runtimeRef = useRef(runtime);
  runtimeRef.current = runtime;
  const threadRef = useRef(threadId);
  threadRef.current = threadId;
  const eventFactory = useRef<LiveEventFactory | null>(null);
  const runStarted = useRef(false);
  const runFailed = useRef(false);
  const runCancelled = useRef(false);
  const actionPending = useRef(false);
  const [busy, setBusy] = useState(false);
  const wasLoading = useRef(false);

  const current = assistants.find((item) => item.assistant_id === assistantId);
  const graphId = current?.graph_id ?? bundle?.manifest.graph_id ?? DEFAULT_GRAPH;
  const manifest: UiManifest = bundle?.manifest ?? {
    schema_version: "1.0",
    manifest_version: "0.0.fallback",
    graph_id: graphId,
    title: current?.name || graphId,
  };

  const setKnownThread = useCallback(
    (value: string | null) => {
      setThreadId(value);
      dispatch({ type: "thread", threadId: value });
      if (value) localStorage.setItem(threadKey(graphId), value);
      else localStorage.removeItem(threadKey(graphId));
    },
    [graphId],
  );

  const stream = useStream<StateType>({
    apiUrl,
    callerOptions: { fetch: authorizedFetch },
    assistantId,
    threadId,
    fetchStateHistory: false,
    throttle: 100,
    onThreadId: setKnownThread,
    onUpdateEvent: (data) => {
      const factory = eventFactory.current;
      if (!factory) return;
      for (const [nodeId, update] of Object.entries(
        (data ?? {}) as Record<string, unknown>,
      )) {
        dispatch({ type: "event", event: factory.nodeUpdated(nodeId, update) });
      }
    },
    onCreated: (run) => {
      const factory = eventFactory.current;
      if (factory) dispatch({ type: "event", event: factory.created(run.run_id, run.thread_id) });
    },
    onTaskEvent: (data) => {
      const factory = eventFactory.current;
      if (!factory) return;
      const task = data as unknown as {
        id: string;
        name: string;
        input?: unknown;
        result?: unknown;
        error?: unknown;
      };
      // Успех и отказ различаются значением `error`, а не наличием ключа:
      // сервер кладёт в событие результата оба поля сразу — заполненный
      // `result` и `error: null` рядом с ним. Проверка на наличие ключа
      // объявляла бы проваленным каждый отработавший узел.
      if (Object.prototype.hasOwnProperty.call(task, "input")) {
        dispatch({
          type: "event",
          event: factory.nodeStarted(task.name, task.id, task.input),
        });
      } else if (task.error !== null && task.error !== undefined) {
        dispatch({
          type: "event",
          event: factory.nodeFailed(task.name, task.id, task.error),
        });
      } else if (Object.prototype.hasOwnProperty.call(task, "result")) {
        dispatch({
          type: "event",
          event: factory.nodeCompleted(task.name, task.id, task.result),
        });
      }
    },
    onError: (error) => {
      runFailed.current = true;
      const factory = eventFactory.current;
      if (factory) dispatch({ type: "event", event: factory.failed(error) });
    },
    onStop: () => {
      runCancelled.current = true;
      const factory = eventFactory.current;
      if (factory) dispatch({ type: "event", event: factory.cancelled() });
    },
  });

  // `stream.interrupt` объявлен как одна остановка, но SDK отдаёт под этим
  // именем массив, когда остановок несколько: тип врёт, и `?.id` на массиве
  // молча даёт undefined — форма подтверждения исчезает, а run висит.
  // `interrupts` типизирован честно, поэтому берём остановку только отсюда.
  // Очередь из нескольких карточек пока не построена: адресуем первую.
  const serverInterrupt = stream.interrupts.at(0);

  useEffect(() => {
    let live = true;
    loadCapabilities(apiUrl).then((value) => live && setCapabilities(value)).catch(() => live && setCapabilities(null));
    loadAssistants()
      .then((items) => {
        if (!live) return;
        const ordered = sortAssistants(items);
        setAssistants(ordered);
        setAssistantsError(null);
        const selected = pickAssistant(ordered, assistantId);
        if (selected) {
          setAssistantId(selected.assistant_id);
          setThreadId(localStorage.getItem(threadKey(selected.graph_id)) || null);
        }
        Promise.all(
          ordered.map(async (assistant) => {
            try {
              const item = await loadManifest(apiUrl, assistant.graph_id);
              return [
                assistant.graph_id,
                { label: localized(item.title), hint: localized(item.description) },
              ] as const;
            } catch {
              return [
                assistant.graph_id,
                {
                  label: assistant.name || assistant.graph_id,
                  hint: assistant.description || assistant.graph_id,
                },
              ] as const;
            }
          }),
        ).then((entries) => live && setManifestInfo(Object.fromEntries(entries)));
      })
      .catch((error: Error) => live && setAssistantsError(error.message));
    return () => { live = false; };
    // Initial graph preference is intentionally read only once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let live = true;
    let checking = false;
    const controller = new AbortController();
    const check = async () => {
      if (checking || document.visibilityState === "hidden") return;
      checking = true;
      try {
        const value = await checkServer(controller.signal);
        if (live) setOnline(value);
      } finally { checking = false; }
    };
    const offline = () => setOnline("offline");
    void check();
    const timer = window.setInterval(check, 15_000);
    window.addEventListener("online", check);
    window.addEventListener("offline", offline);
    window.addEventListener("focus", check);
    document.addEventListener("visibilitychange", check);
    return () => {
      live = false;
      controller.abort();
      window.clearInterval(timer);
      window.removeEventListener("online", check);
      window.removeEventListener("offline", offline);
      window.removeEventListener("focus", check);
      document.removeEventListener("visibilitychange", check);
    };
  }, []);

  useEffect(() => {
    if (!current) return;
    let live = true;
    setBundle(null);
    setBundleError(null);
    loadUiBundle(apiUrl, current)
      .then((value) => {
        if (!live) return;
        setBundle(value);
        dispatch({
          type: "reset",
          assistantId: current.assistant_id,
          graphId: current.graph_id,
          manifestVersion: value.manifest.manifest_version,
          topologyHash: value.topologyHash,
        });
        dispatch({ type: "connection", connection: "live" });
        dispatch({ type: "thread", threadId: threadRef.current });
      })
      .catch((error: Error) => live && setBundleError(error.message));
    return () => {
      live = false;
    };
  }, [current]);

  useEffect(() => {
    dispatch({ type: "thread", threadId });
  }, [threadId]);

  useEffect(() => {
    if (current) localStorage.setItem(GRAPH_KEY, current.graph_id);
  }, [current]);
  useEffect(() => {
    localStorage.setItem(TASK_KEY, String(inputs.task ?? ""));
  }, [inputs.task]);

  useEffect(() => {
    if (!bundle || bundle.assistant.assistant_id !== assistantId) return;
    const values = stream.values as StateType | undefined;
    dispatch({
      type: "reconcile",
      snapshot: {
        state: values ?? {},
        threadId,
        runStatus: stream.isLoading
          ? "running"
          : serverInterrupt
            ? "interrupted"
            : runtimeRef.current.runStatus,
      },
    });
  }, [stream.values, serverInterrupt, stream.isLoading, threadId, bundle, assistantId]);

  useEffect(() => {
    // Only the idle SDK snapshot decides whether an approval was consumed.
    // A failed submission keeps the same server interrupt available for retry.
    if (!bundle || bundle.assistant.assistant_id !== assistantId || stream.isLoading) return;
    let factory = eventFactory.current;
    if (!factory && serverInterrupt) {
      factory = new LiveEventFactory(assistantId, graphId, () => threadRef.current ?? "pending");
      eventFactory.current = factory;
      dispatch({ type: "event", event: factory.startRun() });
    }
    if (!factory) return;
    for (const event of factory.reconcileInterrupt(runtimeRef.current, serverInterrupt, waitingNode(serverInterrupt))) {
      dispatch({ type: "event", event });
    }
  }, [assistantId, graphId, serverInterrupt, stream.isLoading, bundle, runtime.interrupts, runtime.runStatus]);

  useEffect(() => {
    if (stream.isLoading && !wasLoading.current && !runStarted.current) {
      const factory = new LiveEventFactory(
        assistantId,
        graphId,
        () => threadRef.current ?? "pending",
      );
      eventFactory.current = factory;
      dispatch({ type: "event", event: factory.startRun() });
    }
    if (!stream.isLoading && wasLoading.current && !serverInterrupt && !runFailed.current && !runCancelled.current) {
      const factory = eventFactory.current;
      if (factory) dispatch({ type: "event", event: factory.completed() });
      runStarted.current = false;
    }
    if (!stream.isLoading) {
      runStarted.current = false;
    }
    wasLoading.current = stream.isLoading;
  }, [assistantId, graphId, serverInterrupt, stream.isLoading]);

  const startRun = useCallback(
    (payload: unknown) => {
      const question =
        typeof payload === "object" && payload
          ? String((payload as { value?: unknown }).value ?? "")
          : String(payload ?? "");
      if (!question.trim() || stream.isLoading) return;
      runFailed.current = false;
      runCancelled.current = false;
      const factory = new LiveEventFactory(
        assistantId,
        graphId,
        () => threadRef.current ?? "pending",
      );
      eventFactory.current = factory;
      runStarted.current = true;
      // Ход начался — на главном экране снова нужна схема, а не документ.
      setOpenDoc(null);
      dispatch({ type: "event", event: factory.startRun() });
      return stream.submit(
        { messages: [{ type: "human", content: question.trim() }] },
        {
          config: { configurable: configurableOf(manifest, inputs) },
          streamMode: ["values", "updates", "tasks"],
        },
      );
    },
    [assistantId, graphId, inputs, manifest, stream],
  );

  const newThread = useCallback(() => {
    if (stream.isLoading || actionPending.current) return;
    setOpenDoc(null);
    setSelectedNode(null);
    setActionError(null);
    runStarted.current = false;
    runFailed.current = false;
    runCancelled.current = false;
    wasLoading.current = false;
    setKnownThread(null);
    eventFactory.current = null;
    dispatch({
      type: "reset",
      assistantId,
      graphId,
      manifestVersion: manifest.manifest_version,
      topologyHash: bundle?.topologyHash,
    });
  }, [assistantId, bundle?.topologyHash, graphId, manifest.manifest_version, setKnownThread, stream.isLoading]);

  const dispatcher = useMemo(
    () =>
      new ActionDispatcher(manifest, capabilities, () => runtimeRef.current, {
        "thread.create": newThread,
        "run.start": startRun,
        "run.stop": () => {
          runCancelled.current = true;
          stream.stop();
        },
        "interrupt.resume": (payload, action) => {
          if (!action.interruptId || action.interruptId !== serverInterrupt?.id) {
            throw new Error("Остановка изменилась. Дождитесь актуальной формы подтверждения.");
          }
          // `reject` не отменяет предыдущий run — сервер откажет новому, а
          // идущий продолжит идти. Ошибка в интерфейсе при работающем графе
          // хуже отказа заранее, поэтому во время прогона не отправляем.
          if (stream.isLoading) {
            throw new Error("Прогон ещё идёт. Дождитесь его завершения.");
          }
          runStarted.current = true;
          runFailed.current = false;
          runCancelled.current = false;
          return stream.submit(undefined, {
            command: { resume: { [action.interruptId]: payload } },
            multitaskStrategy: "reject",
            streamMode: ["values", "updates", "tasks"],
          });
        },
        "publication.open": (payload) => {
          const document = (payload ?? {}) as { title?: unknown; text?: unknown };
          setOpenDoc({
            title: String(document.title ?? "документ"),
            text: String(document.text ?? ""),
          });
        },
        "resource.refresh": () => undefined,
      }, (body) => validateAction(apiUrl, body)),
    [capabilities, manifest, newThread, startRun, stream],
  );

  const handleAction = useCallback(
    (action: WidgetAction) => {
      // Этот замок переживает пересоздание dispatcher при каждом обновлении SDK.
      const submitting = action.kind === "run.start" || action.kind === "interrupt.resume";
      if (submitting && actionPending.current) return;
      if (submitting) { actionPending.current = true; setBusy(true); }
      setActionError(null);
      dispatcher.dispatch(action).catch((error: Error) => setActionError(error.message)).finally(() => {
        if (submitting) { actionPending.current = false; setBusy(false); }
      });
    },
    [dispatcher],
  );

  const resourceAdapter = useCallback(
    (resourceId: string, operation: string, params?: Record<string, string>) =>
      loadResource(apiUrl, resourceId, operation, params),
    [],
  );
  const resourceMutationAdapter = useCallback(
    (resourceId: string, operation: string, payload: Record<string, unknown>) =>
      mutateResource(apiUrl, resourceId, operation, payload),
    [],
  );

  const updateInput = useCallback(
    (id: string, value: unknown) => setInputs((previous) => ({ ...previous, [id]: value })),
    [],
  );

  const safeContext = useMemo<SafeWidgetContext>(
    () => ({
      locale: navigator.language || "ru",
      graphId,
      runtime,
      inputs,
      setInput: updateInput,
      resource: resourceAdapter,
      mutateResource: resourceMutationAdapter,
    }),
    [graphId, inputs, resourceAdapter, resourceMutationAdapter, runtime, updateInput],
  );

  const chooseAssistant = (id: string) => {
    if (id === assistantId || stream.isLoading || actionPending.current) return;
    const next = assistants.find((item) => item.assistant_id === id);
    if (!next) return;
    setOpenDoc(null);
    setBundle(null);
    setBundleError(null);
    dispatch({ type: "reset", assistantId: id, graphId: next.graph_id, manifestVersion: "0.0.fallback" });
    setAssistantId(id);
    setThreadId(localStorage.getItem(threadKey(next.graph_id)) || null);
    setSelectedNode(null);
    setActionError(null);
    runStarted.current = false;
    runFailed.current = false;
    runCancelled.current = false;
    wasLoading.current = false;
    eventFactory.current = null;
  };

  // Ширину колонки тянет мышь, а не медиазапрос: длина имён документов у
  // каждого своя, и угадать её за оператора нельзя.
  const startResize = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const origin = event.currentTarget.parentElement?.getBoundingClientRect().left ?? 0;
    const move = (moving: PointerEvent) => {
      const next = Math.round(Math.min(LEFT_MAX, Math.max(LEFT_MIN, moving.clientX - origin)));
      setLeftWidth(next);
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  };

  useEffect(() => {
    if (leftWidth) localStorage.setItem(LEFT_WIDTH_KEY, String(leftWidth));
  }, [leftWidth]);

  const label = graphInfo(current, graphId, manifestInfo);
  const fatalError = assistantsError || bundleError || actionError;

  return (
    <div
      className="app engine-app"
      style={leftWidth ? ({ "--col-left": `${leftWidth}px` } as React.CSSProperties) : undefined}
    >
      {/* Сцена лежит за всем интерфейсом. Пока читают документ её нет: текст
          и вращение за ним не уживаются. */}
      {openDoc ? null : (
        <div className="orbit-stage">
          <Orbit
            animated={animated}
            steer="window"
            tone={runtime.runStatus === "interrupted" ? "wait" : stream.isLoading ? "run" : "idle"}
          />
        </div>
      )}
      <header className="header">
        <span className="brand">ORBITA</span>
        <AgentPicker
          assistants={assistants}
          selected={assistantId}
          onSelect={chooseAssistant}
          disabled={stream.isLoading || busy}
          info={manifestInfo}
        />
        <span className="rule">{"─".repeat(400)}</span>
        <span className="status">
          <Spinner on={stream.isLoading} />
          <span className={online === null ? "dot-idle" : online === "ok" ? "dot-ok" : "dot-bad"}>
            {online === null ? "◇ связь…" : online === "ok" ? "◆ на связи" : online === "unauthorized" ? "◆ нет доступа" : "◆ нет сервера"}
          </span>
          <span
            className={`connection connection-${runtime.connection}`}
            aria-live="polite"
          >
            {RUN_LABELS[runtime.runStatus]}
          </span>
          <button disabled={stream.isLoading || busy} onClick={newThread}>[новый диалог]</button>
          <button aria-pressed={detailsOpen} onClick={() => setDetailsOpen((value) => !value)}>[подробности]</button>
          <button aria-pressed={animated} onClick={() => {
            const next = !animated;
            setAnimated(next);
            localStorage.setItem("orbita.animation", next ? "1" : "0");
          }}>[анимация: {animated ? "вкл" : "выкл"}]</button>
          <button onClick={() => setSettingsOpen(true)}>[настройки]</button>
        </span>
      </header>

      {bundle?.fallback ? (
        <div className="engine-warning" role="status">
          Упрощённый интерфейс: {bundle.warning}
        </div>
      ) : null}
      {fatalError ? (
        <div className="error" role="alert">
          {fatalError}
        </div>
      ) : null}

      <div className="columns engine-columns">
        <div className="col-left">
          <div
            className="col-resizer"
            role="separator"
            aria-orientation="vertical"
            aria-label="ширина левой колонки"
            title="тянуть — ширина колонки"
            onPointerDown={startResize}
            onDoubleClick={() => {
              setLeftWidth(0);
              localStorage.removeItem(LEFT_WIDTH_KEY);
            }}
          />
          <Panel title="ВХОД И РЕЗУЛЬТАТЫ">
            <SurfaceRenderer
              key={assistantId}
              surface="left"
              manifest={manifest}
              runtime={runtime}
              context={safeContext}
              inputs={inputs}
              onInput={updateInput}
              onAction={handleAction}
            />
          </Panel>
        </div>
        <div className="col-center">
          {openDoc ? (
            <Panel
              title={`ДОКУМЕНТ · ${openDoc.title}`}
              right={<button onClick={() => setOpenDoc(null)}>[к схеме]</button>}
            >
              <div className="engine-document">
                <SafeMarkdown value={openDoc.text} />
              </div>
            </Panel>
          ) : (
            <Panel
              title="ГРАФ"
              right={
                <span className="hint">
                  {bundle?.topology.nodes.length ?? 0} узлов · {RUN_LABELS[runtime.runStatus]}
                </span>
              }
            >
              {bundle ? (
                <GraphCanvas
                  key={assistantId}
                  topology={bundle.topology}
                  manifest={manifest}
                  runtime={runtime}
                  selected={selectedNode}
                  onSelect={setSelectedNode}
                />
              ) : (
                <div className="hint">{fatalError ? "Не удалось загрузить граф. Проверьте подключение к серверу и обновите страницу." : "Загрузка графа…"}</div>
              )}
            </Panel>
          )}
          <Panel title="ПРОГОН">
            <SurfaceRenderer
              key={assistantId}
              surface="main"
              manifest={manifest}
              runtime={runtime}
              context={safeContext}
              inputs={inputs}
              onInput={updateInput}
              onAction={handleAction}
            />
            {stream.isLoading && manifest.capabilities?.stop_run ? (
              <button onClick={() => handleAction({ kind: "run.stop" })}>[остановить]</button>
            ) : null}
          </Panel>
        </div>
        <div className="col-right">
          <Panel title="СОСТОЯНИЕ">
            <SurfaceRenderer
              key={assistantId}
              surface="right"
              manifest={manifest}
              runtime={runtime}
              context={safeContext}
              inputs={inputs}
              onInput={updateInput}
              onAction={handleAction}
            />
          </Panel>
        </div>
      </div>

      {detailsOpen ? <Panel title="ЖУРНАЛ ВЫПОЛНЕНИЯ" className="timeline-panel">
        <Timeline runtime={runtime} onSelectNode={setSelectedNode} />
      </Panel> : null}
      <footer
        className="header"
        style={{ borderBottom: "none", borderTop: "1px solid var(--line)" }}
      >
        <span className="hint">
          {label.hint || label.label}
          {detailsOpen ? ` · manifest: ${manifest.manifest_version} · thread: ${threadId ?? "новый"}` : ""}
        </span>
        {stream.error ? (
          <span className="error" role="alert">{runErrorMessage(stream.error)}</span>
        ) : null}
      </footer>
      <SettingsOverlay open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      {/*
        Карточку решает рантайм, а не флаг загрузки. Снимать её с экрана на
        время отправки нельзя: пока идёт прогон, оператор должен видеть, что
        именно он подтвердил, — а не пустой экран. Отправку в это время
        запирает `busy`, подмену остановки — сверка id в reconcile.
      */}
      <InterruptSurface
        manifest={manifest}
        runtime={runtime}
        context={safeContext}
        onAction={handleAction}
        busy={busy || stream.isLoading}
      />
    </div>
  );
}
