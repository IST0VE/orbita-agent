/** Declarative LangGraph UI assembled from topology, manifest and runtime events. */

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { useStream } from "@langchain/langgraph-sdk/react";
import type { Message } from "@langchain/langgraph-sdk";

import { API_URL, loadAssistants, type Assistant } from "./api";
import { authorizedFetch } from "./auth";
import { ActionDispatcher } from "./engine/actions/dispatcher";
import {
  cancelPause,
  loadCapabilities,
  loadManifest,
  loadResource,
  loadUiBundle,
  mutateResource,
  requestPause,
  validateAction,
  type UiBundle,
} from "./engine/api/client";
import { LiveEventFactory, runErrorMessage } from "./engine/api/langgraphAdapter";
import { useGraphView } from "./engine/canvas/useGraphView";
import { configurableOf } from "./engine/manifest/bindings";
import { redactor } from "./engine/manifest/redaction";
import type {
  EngineCapabilities,
  SafeWidgetContext,
  UiManifest,
  WidgetAction,
} from "./engine/manifest/types";
import { localized } from "./engine/manifest/validate";
import { createRuntimeSnapshot, runtimeReducer } from "./engine/runtime/reducer";
import { useColumns } from "./hooks/useColumns";
import { useColumnWidth } from "./hooks/useColumnWidth";
import { useServerStatus } from "./hooks/useServerStatus";
import { InterruptSurface } from "./engine/surfaces/SurfaceRenderer";
import type { OrbitaState } from "./lib/orbita";
import { SettingsPage } from "./panels/settings/SettingsPage";
import { JournalPage, type JournalContext } from "./panels/journal/JournalPage";
import { describeError, reportClient } from "./lib/clientLog.ts";
import { AppShell } from "./app/AppShell";
import { ContextBar } from "./app/ContextBar";
import { ExecutionConsole, type ConsoleTab } from "./app/ExecutionConsole";
import { FileSidebar, type SidebarScroll } from "./app/FileSidebar";
import { GlobalHeader } from "./app/GlobalHeader";
import { Inspector } from "./app/inspector/Inspector";
import { graphInfo, rememberScenario, sortAssistants } from "./app/scenarios";
import { hasResultContent } from "./app/result";
import type { AppSection, SettingsGroupId, WorkspaceView } from "./app/sections";
import { TaskComposer } from "./app/TaskComposer";
import { Workspace, type OpenDocument } from "./app/Workspace";

type StateType = { messages: Message[] } & OrbitaState & Record<string, unknown>;

const GRAPH_KEY = "orbita.graph";
const TASK_KEY = "orbita.task";
const DEFAULT_GRAPH = "agent";
const apiUrl = API_URL || window.location.origin;
const threadKey = (graphId: string) => `orbita.thread.${graphId}`;

function pickAssistant(list: Assistant[], wanted: string): Assistant | undefined {
  return list.find((item) => item.assistant_id === wanted || item.graph_id === wanted) ?? list[0];
}

function waitingNode(interrupt: { ns?: string[] } | undefined): string | undefined {
  const namespace = interrupt?.ns;
  return namespace?.length ? namespace[namespace.length - 1].split(":")[0] || undefined : undefined;
}

/**
 * Папка задачи для строки контекста.
 *
 * Берётся из того поля ввода, которое объявлено деревом папок: какое это поле
 * и как оно называется, знает манифест, а не фронтенд. У сценария обновления
 * документа таких полей два — в строке контекста показывается первое, то же,
 * что стоит первым и в колонке материалов.
 */
function currentTask(manifest: UiManifest, inputs: Record<string, unknown>): string {
  const field = (manifest.input ?? []).find((input) => input.widget === "task-picker");
  const value = field ? inputs[field.id] : "";
  return typeof value === "string" ? value : "";
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
  const online = useServerStatus();
  /** Где находится оператор: рабочая область, настройки или журнал. */
  const [section, setSection] = useState<AppSection>("workspace");
  /** На что он смотрит внутри рабочей области. */
  const [view, setView] = useState<WorkspaceView>("graph");
  const [animated, setAnimated] = useState(() => localStorage.getItem("orbita.animation") !== "0");
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  /** Открытый документ занимает главную область вместо схемы. */
  const [openDoc, setOpenDoc] = useState<OpenDocument | null>(null);
  const { width: leftWidth, startResize, reset: resetLeftWidth } = useColumnWidth();
  const columns = useColumns();
  const { openInspector, openSidebar, closeSidebar, closeInspector } = columns;
  const graph = useGraphView();
  /** Консоль выполнения: до первого прогона её нет. */
  const [consoleOpen, setConsoleOpen] = useState(false);
  const [consoleTab, setConsoleTab] = useState<ConsoleTab>("stream");
  const [scrollRequest, setScrollRequest] = useState<SidebarScroll>(null);
  const [settingsGroup, setSettingsGroup] = useState<SettingsGroupId | undefined>();
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
  /**
   * Заявка на паузу оставлена, но ещё не взята.
   *
   * Отдельно от состояния прогона: прогон в этот момент идёт как шёл, и
   * сказать про него нечего, кроме того, что он остановится на ближайшей
   * границе шага. Между нажатием и остановкой проходит целый этап, и молчать
   * всё это время — значит оставить оператора с кнопкой, которая как будто
   * не сработала.
   */
  const [pauseRequested, setPauseRequested] = useState(false);
  const wasLoading = useRef(false);
  /** Восстановленный тред открывает консоль один раз, а не при каждом кадре. */
  const restored = useRef(false);

  const current = assistants.find((item) => item.assistant_id === assistantId);
  const graphId = current?.graph_id ?? bundle?.manifest.graph_id ?? DEFAULT_GRAPH;
  const manifest: UiManifest = bundle?.manifest ?? {
    schema_version: "1.0",
    manifest_version: "0.0.fallback",
    graph_id: graphId,
    title: current?.name || graphId,
  };

  /*
   * Единственное место, где данные графа входят в интерфейс.
   *
   * Правила `redaction` объявлены в манифесте, но применял их только серверный
   * нормализатор событий, мимо которого идёт SDK. Здесь они применяются к тому
   * каналу, которым данные приходят на самом деле: к снимку состояния и к
   * полезной нагрузке событий, из которых потом собираются карточка узла,
   * консоль и её выгрузка.
   *
   * Это не замена серверной очистке: к моменту вызова данные уже в браузере.
   */
  const clean = useMemo(() => redactor(manifest.redaction), [manifest.redaction]);

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
        dispatch({ type: "event", event: factory.nodeUpdated(nodeId, clean(update)) });
      }
    },
    onCreated: (run) => {
      const factory = eventFactory.current;
      if (!factory) return;
      // SDK объявляет `{run_id, thread_id}`, но часть его путей зовёт callback
      // с `{runId}`. Читаем обе записи: иначе событие о принятом ходе приходит
      // с пустым идентификатором, и в журнале вместо run_id сервера остаётся
      // локальный `live-…`.
      const meta = run as unknown as {
        run_id?: string;
        thread_id?: string;
        runId?: string;
        threadId?: string;
      };
      dispatch({
        type: "event",
        event: factory.created(
          meta.run_id ?? meta.runId ?? "",
          meta.thread_id ?? meta.threadId ?? threadRef.current ?? "",
        ),
      });
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
          event: factory.nodeStarted(task.name, task.id, clean(task.input)),
        });
      } else if (task.error !== null && task.error !== undefined) {
        dispatch({
          type: "event",
          event: factory.nodeFailed(task.name, task.id, clean(task.error)),
        });
      } else if (Object.prototype.hasOwnProperty.call(task, "result")) {
        dispatch({
          type: "event",
          event: factory.nodeCompleted(task.name, task.id, clean(task.result)),
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

  /*
   * Заявка на паузу живёт ровно столько, сколько идёт прогон.
   *
   * Кончился он остановкой с вопросом — заявку взял узел; кончился сам —
   * брать её стало некому, и следующий прогон встал бы на первом же этапе,
   * хотя просили остановить не его. Снимает заявку на сервере тот, кто её
   * взял; здесь снимается только отметка на экране.
   */
  useEffect(() => {
    if (!stream.isLoading) setPauseRequested(false);
  }, [stream.isLoading]);

  useEffect(() => {
    if (current) {
      localStorage.setItem(GRAPH_KEY, current.graph_id);
      rememberScenario(current.graph_id);
    }
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
        state: clean(values ?? {}),
        threadId,
        runStatus: stream.isLoading
          ? "running"
          : serverInterrupt
            ? "interrupted"
            : runtimeRef.current.runStatus,
      },
    });
  }, [stream.values, serverInterrupt, stream.isLoading, threadId, bundle, assistantId, clean]);

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

  /**
   * Консоль открывается сама тогда, когда ей есть что показать: с началом
   * прогона и при возвращении к треду, в котором уже что-то происходило.
   * Свернул её оператор — она остаётся свёрнутой до следующего прогона.
   */
  useEffect(() => {
    if (runtime.runId) setConsoleOpen(true);
  }, [runtime.runId]);
  useEffect(() => {
    if (restored.current) return;
    const values = stream.values as StateType | undefined;
    if (Array.isArray(values?.messages) && values.messages.length) {
      restored.current = true;
      setConsoleOpen(true);
    }
  }, [stream.values]);

  /** Прогон закончился — итог показывается сам, если он есть. */
  useEffect(() => {
    if (runtime.runStatus !== "completed") return;
    if (hasResultContent(manifest, runtime)) setView((value) => (value === "graph" ? "result" : value));
  }, [runtime.runStatus, runtime.state, manifest]);

  const startRun = useCallback(
    (payload: unknown) => {
      const question =
        typeof payload === "object" && payload
          ? String((payload as { value?: unknown }).value ?? "")
          : String(payload ?? "");
      // Двойное нажатие и отправка во время прогона — не ошибка, а гонка: её
      // здесь и гасили молчанием.
      if (stream.isLoading) return;
      // Запуск без текста — это несогласованный контракт виджета, а не выбор
      // оператора: поле задачи пустое не отправляет. Молчаливый возврат делал
      // такое расхождение невидимым — кнопка срабатывала, ход не начинался и
      // ошибки не было.
      if (!question.trim()) {
        throw new Error(
          "Запуск без текста задачи: виджет прислал payload без строкового `value`.",
        );
      }
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
      setView("graph");
      setConsoleOpen(true);
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
    setView("graph");
    setConsoleOpen(false);
    restored.current = true;
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

  const openDocument = useCallback((payload: unknown) => {
    const document = (payload ?? {}) as { title?: unknown; text?: unknown };
    setOpenDoc({
      title: String(document.title ?? "документ"),
      text: String(document.text ?? ""),
    });
    setSection("workspace");
    setView("document");
  }, []);

  const dispatcher = useMemo(
    () =>
      new ActionDispatcher(manifest, capabilities, () => runtimeRef.current, {
        "thread.create": newThread,
        "run.start": startRun,
        "run.stop": () => {
          runCancelled.current = true;
          stream.stop();
        },
        // Пауза графа не касается: она оставляет заявку на сервере, а
        // остановку берёт сам узел перед следующим обращением к модели.
        // Поэтому здесь нет ни `stream.stop`, ни `stream.submit` — прогон
        // продолжает идти и кончится сам, остановкой с вопросом.
        "run.pause": async () => {
          const thread = runtimeRef.current.threadId ?? threadRef.current;
          if (!thread) {
            throw new Error("Пауза адресуется треду: дождитесь, пока прогон его создаст.");
          }
          setPauseRequested((await requestPause(apiUrl, thread)).pending);
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
        "publication.open": openDocument,
        "resource.refresh": () => undefined,
      }, (body) => validateAction(apiUrl, body)),
    [capabilities, manifest, newThread, openDocument, startRun, stream],
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
    setSection("workspace");
    setView("graph");
    setConsoleOpen(false);
    restored.current = false;
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

  /** Выбор узла открывает инспектор: подробности приходят к тому, кто их спросил. */
  const selectNode = useCallback((nodeId: string | null) => {
    setSelectedNode(nodeId);
    if (nodeId) openInspector();
  }, [openInspector]);

  const showMaterials = useCallback(() => {
    setSection("workspace");
    openSidebar();
    setScrollRequest((previous) => ({ target: "input", nonce: (previous?.nonce ?? 0) + 1 }));
  }, [openSidebar]);

  const openConsole = useCallback((tab: ConsoleTab) => {
    setSection("workspace");
    setConsoleTab(tab);
    setConsoleOpen(true);
  }, []);

  const openSettings = useCallback((group?: SettingsGroupId) => {
    setSection("settings");
    // Раздел настроек знает, какую группу открыть: меню профиля ведёт либо к
    // модели, либо к оформлению, и промахиваться мимо не должно.
    if (group) setSettingsGroup(group);
  }, []);

  const openJournal = useCallback(() => setSection("journal"), []);

  const toggleAnimation = useCallback(() => {
    setAnimated((value) => {
      localStorage.setItem("orbita.animation", value ? "0" : "1");
      return !value;
    });
  }, []);

  /** О чём стоит сказать в шапке: отказы узлов и неудачный прогон. */
  const alerts = useMemo(
    () =>
      Object.values(runtime.executions).filter((item) => item.status === "failed").length
      + (runtime.runStatus === "failed" ? 1 : 0),
    [runtime.executions, runtime.runStatus],
  );

  const label = graphInfo(current, graphId, manifestInfo);
  const fatalError = assistantsError || bundleError || actionError;
  const locked = stream.isLoading || busy;
  const streamError = stream.error ? runErrorMessage(stream.error) : null;

  /*
   * Каждая красная плашка оставляет запись в журнале интерфейса. Плашка
   * исчезает со следующим прогоном или сменой сценария, а пересказывать
   * разработчику ошибку по памяти — худший из способов её передать.
   */
  useEffect(() => {
    if (assistantsError) reportClient("error", "список сценариев", assistantsError);
  }, [assistantsError]);
  useEffect(() => {
    if (bundleError) reportClient("error", `сценарий ${graphId}`, bundleError);
    // Сценарий входит в текст записи, но повод для неё — только новая ошибка.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bundleError]);
  useEffect(() => {
    if (actionError) reportClient("error", "действие", actionError);
  }, [actionError]);
  useEffect(() => {
    if (!stream.error) return;
    // Плашка показывает переведённый текст, а в журнал уходит и исходная
    // ошибка SDK: по её классу и стеку разбирают, откуда она пришла.
    const raw = describeError(stream.error);
    const detail = [raw.message !== streamError ? raw.message : "", raw.detail ?? ""].filter(Boolean).join("\n");
    reportClient("error", "прогон", streamError ?? raw.message, detail || undefined);
    // Запись — одна на ошибку, а не на каждый кадр с тем же текстом.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stream.error]);

  const failures = useMemo(
    () =>
      runtime.executionOrder
        .map((id) => runtime.executions[id])
        .filter((item) => item?.status === "failed")
        .map((item) => ({
          time: item.finishedAt,
          node: item.nodeId,
          message: item.error?.message || "узел завершился ошибкой",
        })),
    [runtime.executionOrder, runtime.executions],
  );
  const journalContext = useMemo<JournalContext>(
    () => ({
      connection:
        online === "ok" ? "на связи"
          : online === "unauthorized" ? "нет доступа (токен API)"
            : online === "offline" ? "нет сервера" : "проверяется",
      scenario: label.label,
      graphId,
      threadId: runtime.threadId ?? threadId,
      runId: runtime.runId,
      runStatus: runtime.runStatus,
      runError: runtime.error?.message,
      failures,
    }),
    [online, label.label, graphId, runtime.threadId, threadId, runtime.runId, runtime.runStatus, runtime.error, failures],
  );
  const journalButton = (
    <button className="btn-ghost btn-sm alert-journal" onClick={openJournal}>
      Журнал и отчёт
    </button>
  );

  return (
    <AppShell
      section={section}
      sidebarOpen={columns.sidebar}
      inspectorOpen={columns.inspector}
      narrow={columns.narrow}
      animated={animated}
      leftWidth={leftWidth}
      onDismissDrawer={() => { closeSidebar(); closeInspector(); }}
      header={
        <GlobalHeader
          assistants={assistants}
          assistantId={assistantId}
          manifestInfo={manifestInfo}
          onSelectAssistant={chooseAssistant}
          locked={locked}
          online={online}
          onSection={setSection}
          onOpenSettings={openSettings}
          onOpenJournal={openJournal}
          alerts={alerts}
          onOpenAlerts={() => openConsole("events")}
        />
      }
      context={
        <ContextBar
          task={currentTask(manifest, inputs)}
          onPickTask={showMaterials}
          scenario={label.label}
          scenarioHint={label.hint}
          runStatus={runtime.runStatus}
          running={stream.isLoading}
          canStop={Boolean(manifest.capabilities?.stop_run)}
          onStop={() => handleAction({ kind: "run.stop" })}
          canPause={Boolean(manifest.capabilities?.pause_run)}
          pauseRequested={pauseRequested}
          onPause={() => handleAction({ kind: "run.pause" })}
          onCancelPause={() => {
            const thread = runtime.threadId ?? threadId;
            if (!thread) return;
            cancelPause(apiUrl, thread)
              .then((status) => setPauseRequested(status.pending))
              .catch((error: Error) => setActionError(error.message));
          }}
          onNewThread={newThread}
          newThreadDisabled={locked}
          events={runtime.events.length}
          consoleOpen={consoleOpen}
          onToggleConsole={() => setConsoleOpen((value) => !value)}
          sidebarOpen={columns.sidebar}
          onToggleSidebar={columns.toggleSidebar}
          inspectorOpen={columns.inspector}
          onToggleInspector={columns.toggleInspector}
        />
      }
      alerts={
        <div className="app-alerts">
          {bundle?.fallback ? (
            <div className="engine-warning" role="status">
              Упрощённый интерфейс: {bundle.warning}
            </div>
          ) : null}
          {fatalError ? (
            <div className="error" role="alert"><span>{fatalError}</span>{journalButton}</div>
          ) : null}
          {streamError ? (
            <div className="error" role="alert"><span>{streamError}</span>{journalButton}</div>
          ) : null}
        </div>
      }
      sidebar={
        <FileSidebar
          manifest={manifest}
          runtime={runtime}
          context={safeContext}
          inputs={inputs}
          onInput={updateInput}
          onAction={handleAction}
          surfaceKey={assistantId}
          startResize={startResize}
          resetWidth={resetLeftWidth}
          scrollRequest={scrollRequest}
          ready={Boolean(bundle)}
          drawer={columns.narrow}
          onClose={closeSidebar}
        />
      }
      main={
        <Workspace
          bundle={bundle}
          manifest={manifest}
          runtime={runtime}
          context={safeContext}
          inputs={inputs}
          onInput={updateInput}
          onAction={handleAction}
          selected={selectedNode}
          onSelect={selectNode}
          view={view}
          onView={setView}
          graph={graph}
          document={openDoc}
          onCloseDocument={() => { setOpenDoc(null); setView("graph"); }}
          error={fatalError}
        />
      }
      composer={
        <TaskComposer
          manifest={manifest}
          runtime={runtime}
          context={safeContext}
          inputs={inputs}
          onInput={updateInput}
          onAction={handleAction}
          onPickContext={showMaterials}
        />
      }
      inspector={
        <Inspector
          manifest={manifest}
          runtime={runtime}
          context={safeContext}
          inputs={inputs}
          onInput={updateInput}
          onAction={handleAction}
          topology={bundle?.topology ?? null}
          selectedNode={selectedNode}
          onClearNode={() => setSelectedNode(null)}
          scenarioTitle={label.label}
          threadId={threadId}
          onClose={closeInspector}
        />
      }
      console={
        consoleOpen ? (
          <ExecutionConsole
            manifest={manifest}
            runtime={runtime}
            context={safeContext}
            inputs={inputs}
            onInput={updateInput}
            onAction={handleAction}
            tab={consoleTab}
            onTab={setConsoleTab}
            onClose={() => setConsoleOpen(false)}
            onSelectNode={selectNode}
          />
        ) : null
      }
    >
      <SettingsPage
        open={section === "settings"}
        group={settingsGroup}
        onClose={() => setSection("workspace")}
        online={online}
        animated={animated}
        onToggleAnimation={toggleAnimation}
        onResetLayout={resetLeftWidth}
      />
      <JournalPage
        open={section === "journal"}
        onClose={() => setSection("workspace")}
        context={journalContext}
      />
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
    </AppShell>
  );
}
