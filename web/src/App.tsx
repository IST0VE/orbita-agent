/** Declarative LangGraph UI assembled from topology, manifest and runtime events. */

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { useStream } from "@langchain/langgraph-sdk/react";
import type { Message } from "@langchain/langgraph-sdk";

import {
  API_URL,
  attachToChat,
  createChat,
  deleteChat,
  deleteChatFile,
  loadAssistants,
  loadChatFiles,
  loadLibrary,
  loadMe,
  moveChat,
  readChatFile,
  renameChat,
  searchChats,
  titleOf,
  uploadChatFile,
  type Assistant,
  type Chat,
  type ChatFiles,
  type Me,
} from "./api";
import { authorizedFetch } from "./auth";
import { currentUser } from "./oidc";
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
  ChatFilesContext,
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
import { ChatPanel, type ChatScroll } from "./app/ChatPanel";
import { ChatSidebar, chatTitle } from "./app/ChatSidebar";
import { GlobalHeader } from "./app/GlobalHeader";
import { Inspector, type RightTab } from "./app/inspector/Inspector";
import { graphInfo, rememberScenario, sortAssistants } from "./app/scenarios";
import { hasResultContent } from "./app/result";
import type { AppSection, SettingsGroupId, WorkspaceView } from "./app/sections";
import { TaskComposer } from "./app/TaskComposer";
import { Workspace, type OpenDocument } from "./app/Workspace";

type StateType = { messages: Message[] } & OrbitaState & Record<string, unknown>;

const GRAPH_KEY = "orbita.graph";
const DEFAULT_GRAPH = "agent";
const apiUrl = API_URL || window.location.origin;

/*
 * Последний открытый чат — один на пользователя, а не по чату на сценарий:
 * список чатов общий, и вернуться надо туда, где был, а не в последний чат
 * того сценария, что стоит в шапке. Свой у каждого пользователя: на общем
 * компьютере следующий вошедший не должен открывать тред предыдущего —
 * сервер его всё равно не отдаст.
 */
const owner = () => currentUser()?.subject;
const chatKey = () => (owner() ? `orbita.chat.${owner()}` : "orbita.chat");
/** Запись прежнего вида: по треду на сценарий. Читается, пока новой нет. */
const legacyThreadKey = (graphId: string) =>
  owner() ? `orbita.thread.${owner()}.${graphId}` : `orbita.thread.${graphId}`;

function rememberChat(thread: string | null, graphId: string) {
  try {
    localStorage.setItem(chatKey(), JSON.stringify({ thread, graph: graphId }));
  } catch {
    // Приватный режим запрещает запись: вернуться в чат после перезагрузки
    // — удобство, и падать из-за него нельзя.
  }
}

/** Тред, который открыть в этом сценарии при загрузке. */
function savedThread(graphId: string): string | null {
  const raw = localStorage.getItem(chatKey());
  if (raw === null) return localStorage.getItem(legacyThreadKey(graphId)) || null;
  try {
    const saved = JSON.parse(raw) as { thread?: unknown; graph?: unknown } | null;
    if (saved?.graph === graphId && typeof saved.thread === "string") return saved.thread;
  } catch {
    // Испорченная запись — то же, что новый чат.
  }
  return null;
}

function pickAssistant(list: Assistant[], wanted: string): Assistant | undefined {
  return list.find((item) => item.assistant_id === wanted || item.graph_id === wanted) ?? list[0];
}

function waitingNode(interrupt: { ns?: string[] } | undefined): string | undefined {
  const namespace = interrupt?.ns;
  return namespace?.length ? namespace[namespace.length - 1].split(":")[0] || undefined : undefined;
}

/**
 * Поля, в которых хранится отметка файлов чата.
 *
 * Отметка относится к файлам одного чата: в соседнем чате файлов с такими
 * именами может не быть, и уехавшее имя обернулось бы отказом «файла нет».
 * Поэтому при смене чата эти поля очищаются, а прочие параметры — нет.
 */
function fileInputs(manifest: UiManifest): string[] {
  return (manifest.input ?? []).filter((input) => input.widget === "file-picker").map((input) => input.id);
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
  const [inputs, setInputs] = useState<Record<string, unknown>>({});
  /** Чаты всех сценариев: треды пользователя, свежие сверху. */
  const [chats, setChats] = useState<Chat[]>([]);
  const [chatsLoading, setChatsLoading] = useState(false);
  const [chatsError, setChatsError] = useState("");
  /** Файлы открытого чата; `thread_id` в ответе — чей это список. */
  const [chatFiles, setChatFiles] = useState<ChatFiles | null>(null);
  const [chatFilesError, setChatFilesError] = useState("");
  const [rightTab, setRightTab] = useState<RightTab>("chat");
  const online = useServerStatus();
  /** Кто вошёл с точки зрения сервера: от этого зависят настройки и журнал. */
  const [me, setMe] = useState<Me | null>(null);
  useEffect(() => {
    if (online !== "ok" || me) return;
    let live = true;
    loadMe().then((value) => live && setMe(value)).catch(() => undefined);
    return () => { live = false; };
  }, [online, me]);
  /** Где находится оператор: рабочая область, настройки или журнал. */
  const [section, setSection] = useState<AppSection>("workspace");
  /** На что он смотрит внутри рабочей области. */
  const [view, setView] = useState<WorkspaceView>("graph");
  const [animated, setAnimated] = useState(() => localStorage.getItem("orbita.animation") !== "0");
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  /** Открытый документ занимает главную область вместо схемы. */
  const [openDoc, setOpenDoc] = useState<OpenDocument | null>(null);
  const left = useColumnWidth("left");
  const right = useColumnWidth("right");
  const resetLayout = useCallback(() => { left.reset(); right.reset(); }, [left.reset, right.reset]);
  const columns = useColumns();
  const { openInspector, closeSidebar, closeInspector } = columns;
  const graph = useGraphView();
  /**
   * Раскрыты ли файлы и параметры в колонке чата. `null` — решает чат: пока
   * разговора нет, раскрыты. С началом прогона они сворачиваются сами: во
   * время хода смотрят на разговор, а не на список файлов.
   */
  const [materialsOpen, setMaterialsOpen] = useState<boolean | null>(null);
  /** Растёт, когда события прогона попросили показать. */
  const [eventsFocus, setEventsFocus] = useState(0);
  const [scrollRequest, setScrollRequest] = useState<ChatScroll>(null);
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
  /**
   * Номер выбора чата: растёт с каждым переходом в другой чат или сценарий.
   * По нему поздний ответ узнаёт, что оператор уже не там, где его ждали.
   */
  const chatChoice = useRef(0);
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
      rememberChat(value, graphId);
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
          setThreadId(savedThread(selected.graph_id));
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

  /*
   * Список чатов — один на все сценарии. Сервер отдаёт только свои треды
   * (`auth.py`), а сценарий чата приезжает в его `graph_id`: открыть чат
   * другого сценария значит переключить и сценарий (`openChat`).
   *
   * Номер запроса отсекает поздние ответы: список, прочитанный раньше,
   * не должен лечь поверх прочитанного позже.
   */
  const chatsRequest = useRef(0);
  const refreshChats = useCallback(() => {
    const request = ++chatsRequest.current;
    setChatsLoading(true);
    return searchChats()
      .then((items) => {
        if (request !== chatsRequest.current) return;
        setChats(items);
        setChatsError("");
      })
      .catch((error: Error) => request === chatsRequest.current && setChatsError(error.message))
      .finally(() => request === chatsRequest.current && setChatsLoading(false));
  }, []);
  useEffect(() => {
    if (online !== "ok") return;
    void refreshChats();
  }, [online, refreshChats]);

  /* Файлы открытого чата. У чата без треда их нет: он появится с первым файлом. */
  const filesRequest = useRef(0);
  const refreshFiles = useCallback((thread: string | null) => {
    const request = ++filesRequest.current;
    setChatFilesError("");
    if (!thread) {
      setChatFiles(null);
      return Promise.resolve();
    }
    return loadChatFiles(thread)
      .then((value) => request === filesRequest.current && setChatFiles(value))
      .catch((error: Error) => {
        if (request !== filesRequest.current) return;
        if (error.name === "chat_not_found") {
          // Браузер помнил чат, которого больше нет: удалили в другой вкладке
          // или сервер разработки потерял треды. Открываем новый черновик.
          forgetThread.current(thread);
          return;
        }
        setChatFilesError(error.message);
      });
  }, []);
  const forgetThread = useRef<(thread: string) => void>(() => undefined);
  useEffect(() => {
    if (online !== "ok") return;
    void refreshFiles(threadId);
  }, [online, threadId, refreshFiles]);

  // Тред появился — первым прогоном или первым файлом, — а в списке его ещё
  // нет: список перечитывается, и новый чат встаёт сверху.
  const knownChats = useRef(chats);
  knownChats.current = chats;
  useEffect(() => {
    if (threadId && !knownChats.current.some((chat) => chat.thread_id === threadId)) void refreshChats();
  }, [threadId, refreshChats]);
  // Прогон закончился — у чата новое время и новый статус.
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !stream.isLoading) void refreshChats();
    wasRunning.current = stream.isLoading;
  }, [stream.isLoading, refreshChats]);

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
   * Прогон начался — ход прогона показывается сам: колонка чата открывается
   * на вкладке «Чат», а файлы и параметры сворачиваются в строку. Раньше для
   * этого была нижняя консоль, и её открывали и закрывали руками.
   *
   * В узком окне колонка выдвигается поверх схемы, и выдвигать её без спроса
   * значит закрыть то, что как раз оживает, — там её открывает оператор.
   */
  const { narrow } = columns;
  const showRun = useCallback(() => {
    setRightTab("chat");
    setMaterialsOpen(false);
    if (!narrow) openInspector();
  }, [narrow, openInspector]);

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
      showRun();
      dispatch({ type: "event", event: factory.startRun() });
      // Первый запрос даёт чату название. Нового треда ещё нет — SDK заведёт
      // его сам, и метаданные уедут вместе с ним; тред, заведённый загрузкой
      // файла, названия не имеет — даём его здесь же.
      const title = titleOf(question);
      const thread = threadRef.current;
      if (thread && !knownChats.current.find((chat) => chat.thread_id === thread)?.title) {
        renameChat(thread, title).then(() => refreshChats()).catch(() => undefined);
      }
      return stream.submit(
        { messages: [{ type: "human", content: question.trim() }] },
        {
          config: { configurable: configurableOf(manifest, inputs) },
          metadata: thread ? undefined : { graph_id: graphId, title },
          streamMode: ["values", "updates", "tasks"],
        },
      );
    },
    [assistantId, graphId, inputs, manifest, refreshChats, showRun, stream],
  );

  /*
   * Открыть чат этого же сценария: существующий тред или новый черновик (null).
   *
   * Экран сбрасывается целиком: схема, карточка узла и отметки файлов
   * относятся к прежнему чату. Черновик заводится на сервере не сразу, а с
   * первым файлом или первым прогоном: пустых чатов в списке не бывает.
   */
  const openThread = useCallback((next: string | null) => {
    if (stream.isLoading || actionPending.current) return;
    // Повторный клик по открытому чату ничего не меняет. Сброс ниже очистил
    // бы список файлов, а перечитывается он по смене треда — которой нет:
    // вместо файлов навсегда оставалось «…».
    if (next !== null && next === threadRef.current) return;
    chatChoice.current += 1;
    setOpenDoc(null);
    setSelectedNode(null);
    setActionError(null);
    setView("graph");
    setMaterialsOpen(null);
    runStarted.current = false;
    runFailed.current = false;
    runCancelled.current = false;
    wasLoading.current = false;
    setKnownThread(next);
    eventFactory.current = null;
    setChatFiles(null);
    const picked = fileInputs(manifest);
    setInputs((previous) => Object.fromEntries(
      Object.entries(previous).filter(([id]) => !picked.includes(id)),
    ));
    dispatch({
      type: "reset",
      assistantId,
      graphId,
      manifestVersion: manifest.manifest_version,
      topologyHash: bundle?.topologyHash,
    });
  }, [assistantId, bundle?.topologyHash, graphId, manifest, setKnownThread, stream.isLoading]);

  const newThread = useCallback(() => openThread(null), [openThread]);
  forgetThread.current = (thread: string) => {
    if (threadRef.current === thread) openThread(null);
  };

  const removeChat = useCallback(async (thread: string) => {
    await deleteChat(thread);
    if (thread === threadRef.current) openThread(null);
    await refreshChats();
  }, [openThread, refreshChats]);

  /*
   * Тред для файла, который загружают в ещё не заведённый чат.
   *
   * Одно обещание на черновик: несколько файлов, брошенных разом, иначе
   * завели бы по чату на каждый. Но только на свой черновик — файл,
   * брошенный в следующий, уехал бы в чужой чат.
   *
   * Заведённый чат открывается, только если оператор всё ещё на том
   * черновике. Ушёл в другой чат, пока сервер отвечал, — поздний ответ
   * переключал его обратно. Файлы всё равно уезжают в заведённый чат:
   * бросали их туда, и найти их можно в списке чатов.
   */
  const pendingThread = useRef<{ choice: number; thread: Promise<string> } | null>(null);
  const ensureThread = useCallback(async (): Promise<string> => {
    if (threadRef.current) return threadRef.current;
    const choice = chatChoice.current;
    if (pendingThread.current?.choice !== choice) {
      const thread = createChat(graphId)
        .then((chat) => {
          // Тред черновику мог успеть завести и прогон: тогда он и открыт.
          if (chatChoice.current === choice && !threadRef.current) {
            setKnownThread(chat.thread_id);
            threadRef.current = chat.thread_id;
          } else {
            void refreshChats();
          }
          return chat.thread_id;
        })
        .finally(() => {
          if (pendingThread.current?.thread === thread) pendingThread.current = null;
        });
      pendingThread.current = { choice, thread };
    }
    return pendingThread.current.thread;
  }, [graphId, refreshChats, setKnownThread]);

  const uploadFiles = useCallback(async (files: File[]) => {
    const thread = await ensureThread();
    const failed: string[] = [];
    let latest: ChatFiles | null = null;
    // По одному: у каждого файла свой ответ, и отказ одного не отменяет остальные.
    for (const file of files) {
      try {
        latest = await uploadChatFile(thread, file);
      } catch (error) {
        failed.push((error as Error).message);
      }
    }
    // Экран трогается, только если на нём всё ещё этот чат. Перечитывание
    // чужого списка отменило бы загрузку списка того чата, что открыт.
    if (threadRef.current === thread) {
      if (latest) setChatFiles(latest);
      else await refreshFiles(thread);
      setRightTab("chat");
    }
    if (failed.length) throw new Error(failed.join("; "));
  }, [ensureThread, refreshFiles]);

  const openDocument = useCallback((payload: unknown) => {
    const document = (payload ?? {}) as { title?: unknown; text?: unknown; file?: unknown };
    setOpenDoc({
      title: String(document.title ?? "документ"),
      text: String(document.text ?? ""),
      // Имя файла есть у того, что Orbita написала сама: документа этапа и
      // публикации. У файла чата его нет — это загрузка человека, и
      // предпросмотр .drawio, скачанный «файлом», был бы уже не схемой.
      ...(typeof document.file === "string" && document.file ? { file: document.file } : {}),
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

  /*
   * Файлы открытого чата для виджетов. Список один на экран: его видят и
   * панель файлов, и строка выбранного, и композер, — и после загрузки он
   * обновляется у всех сразу.
   */
  const chatContext = useMemo<ChatFilesContext>(() => {
    const own = chatFiles && chatFiles.thread_id === threadId ? chatFiles : null;
    return {
      threadId,
      // У черновика файлов нет, и это известно без запроса.
      files: threadId ? own?.files ?? null : [],
      limits: own?.limits,
      loading: Boolean(threadId) && !own,
      error: chatFilesError,
      upload: uploadFiles,
      remove: async (name: string) => {
        const thread = threadRef.current;
        if (!thread) return;
        setChatFiles(await deleteChatFile(thread, name));
      },
      read: (name: string) => {
        const thread = threadRef.current;
        if (!thread) return Promise.reject(new Error("в чате ещё нет файлов"));
        return readChatFile(thread, name);
      },
      library: loadLibrary,
      attach: async (source) => {
        const thread = await ensureThread();
        const result = await attachToChat(thread, source);
        if (threadRef.current === thread) setChatFiles(result);
      },
    };
  }, [chatFiles, chatFilesError, ensureThread, threadId, uploadFiles]);

  const safeContext = useMemo<SafeWidgetContext>(
    () => ({
      locale: navigator.language || "ru",
      graphId,
      runtime,
      inputs,
      setInput: updateInput,
      resource: resourceAdapter,
      mutateResource: resourceMutationAdapter,
      chat: chatContext,
    }),
    [chatContext, graphId, inputs, resourceAdapter, resourceMutationAdapter, runtime, updateInput],
  );

  /*
   * Перейти в другой сценарий и открыть в нём чат (или черновик — null).
   *
   * Чат другого сценария открывается только вместе с его сценарием: у треда
   * состояние своего графа, и чужая схема прочитала бы его как своё.
   */
  const switchScenario = (next: Assistant, thread: string | null) => {
    chatChoice.current += 1;
    setOpenDoc(null);
    setBundle(null);
    setBundleError(null);
    setSection("workspace");
    setView("graph");
    setMaterialsOpen(null);
    dispatch({ type: "reset", assistantId: next.assistant_id, graphId: next.graph_id, manifestVersion: "0.0.fallback" });
    setAssistantId(next.assistant_id);
    setThreadId(thread);
    rememberChat(thread, next.graph_id);
    // Поля ввода — другого сценария. Файлы — того чата, что откроется: у
    // чата, переведённого в новый сценарий, они те же, и прятать их незачем.
    setInputs({});
    if (thread !== threadRef.current) setChatFiles(null);
    setSelectedNode(null);
    setActionError(null);
    runStarted.current = false;
    runFailed.current = false;
    runCancelled.current = false;
    wasLoading.current = false;
    eventFactory.current = null;
  };

  /** Открыть чат из общего списка: своего сценария или чужого. */
  const openChat = (chat: Chat) => {
    if (stream.isLoading || actionPending.current) return;
    const target = chat.graph_id
      ? assistants.find((item) => item.graph_id === chat.graph_id)
      : undefined;
    // Чат без сценария — заведённый до того, как сценарий стали записывать:
    // открывается там, где открывался раньше, в текущем.
    if (!chat.graph_id || target?.assistant_id === assistantId) {
      openThread(chat.thread_id);
      return;
    }
    if (!target) {
      setActionError(`Сценарий этого чата («${chat.graph_id}») на сервере не найден.`);
      return;
    }
    switchScenario(target, chat.thread_id);
  };

  /*
   * Сменить сценарий в шапке.
   *
   * Чат без запросов — это только файлы, и сценарий у него ещё не выбран по
   * сути: он уходит в новый сценарий вместе с файлами. Так можно сначала
   * положить материалы, а потом решить, что с ними делать. Чат с прогоном
   * остаётся в своём сценарии и в списке, а в новом открывается черновик.
   */
  const chooseAssistant = (id: string) => {
    if (id === assistantId || stream.isLoading || actionPending.current) return;
    const next = assistants.find((item) => item.assistant_id === id);
    if (!next) return;
    const thread = threadRef.current;
    const known = knownChats.current.find((chat) => chat.thread_id === thread);
    const values = stream.values as StateType | undefined;
    // Был ли в чате запрос, знает список: состояние треда сразу после
    // перехода ещё может быть прежним. Чат, которого в списке пока нет, заведён
    // только что — о нём говорит его состояние. События есть только у чата,
    // в котором прогон шёл на этом экране.
    const started = runtime.events.length > 0
      || (known ? known.started : Boolean(values?.messages?.length));
    if (thread && !started) {
      switchScenario(next, thread);
      moveChat(thread, next.graph_id)
        .then(() => refreshChats())
        .catch((error: Error) => setActionError(`Чат не перешёл в сценарий: ${error.message}`));
      return;
    }
    switchScenario(next, null);
  };

  /** Выбор узла открывает инспектор: подробности приходят к тому, кто их спросил. */
  const selectNode = useCallback((nodeId: string | null) => {
    setSelectedNode(nodeId);
    if (nodeId) {
      setRightTab("details");
      openInspector();
    }
  }, [openInspector]);

  /** Показать файлы открытого чата: правая колонка, вкладка «Чат», материалы раскрыты. */
  const showMaterials = useCallback(() => {
    setSection("workspace");
    setRightTab("chat");
    setMaterialsOpen(true);
    openInspector();
    setScrollRequest((previous) => ({ target: "input", nonce: (previous?.nonce ?? 0) + 1 }));
  }, [openInspector]);

  /** События прогона: вкладка «Прогон» правой колонки, раздел раскрыт. */
  const openEvents = useCallback(() => {
    setSection("workspace");
    setSelectedNode(null);
    setRightTab("details");
    openInspector();
    setEventsFocus((value) => value + 1);
  }, [openInspector]);

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
  /** Подпись сценария у чата в общем списке. */
  const scenarioName = useCallback(
    (graph: string) =>
      graph ? graphInfo(assistants.find((item) => item.graph_id === graph), graph, manifestInfo).label : "",
    [assistants, manifestInfo],
  );
  const activeChat = chats.find((chat) => chat.thread_id === threadId);
  const activeChatTitle = threadId ? (activeChat ? chatTitle(activeChat) : "Чат") : "Новый чат";
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
      leftWidth={left.width}
      rightWidth={right.width}
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
          admin={me?.admin ?? false}
          service={me?.service ?? false}
          alerts={alerts}
          onOpenAlerts={openEvents}
        />
      }
      context={
        <ContextBar
          chat={activeChatTitle}
          fileCount={chatContext.files?.length ?? 0}
          onShowChat={showMaterials}
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
        <ChatSidebar
          chats={chats}
          loading={chatsLoading}
          error={chatsError}
          activeId={threadId}
          draftScenario={label.label}
          draftGraph={graphId}
          scenarioName={scenarioName}
          locked={locked}
          onOpen={openChat}
          onDelete={removeChat}
          onRetry={() => void refreshChats()}
          startResize={left.startResize}
          resetWidth={left.reset}
          drawer={columns.narrow}
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
          chat={chatContext}
        />
      }
      inspector={
        <Inspector
          tab={rightTab}
          onTab={setRightTab}
          fileCount={chatContext.files?.length ?? null}
          materials={
            <ChatPanel
              manifest={manifest}
              runtime={runtime}
              context={safeContext}
              inputs={inputs}
              onInput={updateInput}
              onAction={handleAction}
              surfaceKey={assistantId}
              scrollRequest={scrollRequest}
              ready={Boolean(bundle)}
              materialsOpen={materialsOpen}
              onMaterialsOpen={setMaterialsOpen}
            />
          }
          manifest={manifest}
          runtime={runtime}
          context={safeContext}
          inputs={inputs}
          onInput={updateInput}
          onAction={handleAction}
          topology={bundle?.topology ?? null}
          selectedNode={selectedNode}
          onClearNode={() => setSelectedNode(null)}
          onSelectNode={selectNode}
          eventsFocus={eventsFocus}
          scenarioTitle={label.label}
          threadId={threadId}
          startResize={right.startResize}
          resetWidth={right.reset}
          drawer={columns.narrow}
        />
      }
    >
      <SettingsPage
        open={section === "settings"}
        group={settingsGroup}
        onClose={() => setSection("workspace")}
        online={online}
        animated={animated}
        onToggleAnimation={toggleAnimation}
        onResetLayout={resetLayout}
        admin={me?.admin ?? false}
        service={me?.service ?? false}
      />
      <JournalPage
        open={section === "journal"}
        onClose={() => setSection("workspace")}
        context={journalContext}
        serverLog={me?.admin ?? false}
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
