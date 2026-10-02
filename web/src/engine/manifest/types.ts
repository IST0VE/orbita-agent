import type { ComponentType, ReactNode } from "react";
import type { RuntimeSnapshot } from "../runtime/types";

export type JsonPrimitive = null | boolean | number | string;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type LocalizedText = string | Record<string, string>;

export type Condition = {
  path?: string;
  exists?: boolean;
  equals?: JsonValue;
  in?: JsonValue[];
  not?: Condition;
  all?: Condition[];
  any?: Condition[];
};

export type WidgetBinding = {
  id?: string;
  path: string;
  widget: string;
  title?: string;
  surface?: SurfaceId;
  order?: number;
  visible_when?: Condition;
  options?: Record<string, JsonValue>;
  empty?: "hide" | "placeholder" | "show";
};

export type InputBinding = {
  id: string;
  target: string;
  widget: string;
  title?: string;
  required?: boolean;
  source?: { resource_id: string; operation: string };
  options?: Record<string, JsonValue>;
};

export type NodeManifest = {
  title?: string;
  description?: string;
  icon?: string;
  kind?: "task" | "router" | "tool" | "approval" | "system";
  group?: string;
  color?: "neutral" | "info" | "success" | "warning" | "danger";
  hidden?: boolean;
  output?: WidgetBinding;
  badges?: WidgetBinding[];
  details?: WidgetBinding[];
  layout?: { rank?: number; order?: number; collapsed?: boolean };
};

export type JsonSchema = {
  type?: "string" | "number" | "integer" | "boolean" | "object" | "array" | "null";
  enum?: JsonValue[];
  const?: JsonValue;
  default?: JsonValue;
  required?: string[];
  properties?: Record<string, JsonSchema>;
  items?: JsonSchema;
  additionalProperties?: boolean;
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  minItems?: number;
  maxItems?: number;
  pattern?: string;
  format?: "date" | "date-time" | "uri" | "multiline" | "markdown";
  title?: string;
  description?: string;
};

export type InterruptBinding = {
  id: string;
  priority?: number;
  match?: Condition;
  widget: string;
  resume_schema: JsonSchema;
  bindings?: WidgetBinding[];
};

/** Правило очистки: путь с `*` по спискам и режим из `redaction.py`. */
export type RedactionRule = {
  path: string;
  mode: "remove" | "mask" | "truncate" | "metadata_only" | "role";
  /** Для `truncate`: сколько символов остаётся. */
  max_length?: number;
  /** Для `role`: кому значение видно. */
  roles?: string[];
};

export type SurfaceId = "header" | "left" | "main" | "right" | "bottom" | "modal" | "drawer";
export type SurfaceManifest = {
  id: SurfaceId;
  title?: string;
  order?: number;
  widgets?: string[];
  collapsible?: boolean;
};

export type ActionKind =
  | "thread.create"
  | "run.start"
  | "run.stop"
  /**
   * Пауза: остановить прогон, не отменяя его.
   *
   * Не то же самое, что `run.stop`. Отмена обрывает ход, и начатый этап
   * оплачивается заново; пауза оставляет заявку, граф встаёт на ближайшей
   * границе шага и ждёт оператора — с готовым состоянием и возможностью
   * дописать в него то, чего не хватило.
   */
  | "run.pause"
  | "run.retry"
  | "thread.fork"
  | "interrupt.resume"
  | "artifact.open"
  | "artifact.edit"
  | "artifact.compare"
  | "publication.open"
  | "resource.refresh";

export type ActionManifest = {
  id: string;
  kind: ActionKind;
  label: string;
  permission?: string;
  confirm?: boolean;
  input_schema?: JsonSchema;
  visible_when?: Condition;
};

export type UiManifest = {
  schema_version: "1.0";
  manifest_version: string;
  graph_id: string;
  title: LocalizedText;
  description?: LocalizedText;
  icon?: string;
  tags?: string[];
  capabilities?: {
    new_thread?: boolean;
    stop_run?: boolean;
    pause_run?: boolean;
    resume_interrupt?: boolean;
    history?: boolean;
    retry_node?: boolean;
  };
  input?: InputBinding[];
  nodes?: Record<string, NodeManifest>;
  state?: Array<WidgetBinding & { id: string }>;
  interrupts?: InterruptBinding[];
  surfaces?: SurfaceManifest[];
  actions?: ActionManifest[];
  redaction?: RedactionRule[];
  theme?: Record<string, JsonValue>;
};

export type EngineCapabilities = {
  engine: string;
  engine_version: string;
  manifest_versions: string[];
  event_versions: string[];
  features: Record<string, boolean>;
  limits: Record<string, number>;
};

export type WidgetMode = "view" | "edit" | "input" | "interrupt";
export type WidgetAction = {
  kind: ActionKind;
  payload?: unknown;
  /** Настоящий ID остановки LangGraph: ответ адресуется только ей. */
  interruptId?: string;
  /** Идентификатор правила `interrupts[]`: по нему сервер берёт resume_schema. */
  ruleId?: string;
};

export type SafeWidgetContext = {
  locale: string;
  graphId: string;
  runtime: RuntimeSnapshot;
  /**
   * Значения полей ввода по их id из манифеста.
   *
   * Нужны полю, список которого зависит от другого поля: схемы показываются
   * из выбранной папки задачи, а не из всех сразу. Имя соседнего поля виджет
   * узнаёт из своего же binding (`options.depends_on`), а не зашивает: связь
   * объявлена в манифесте, и знать про `task` по имени фронтенду незачем.
   */
  inputs: Record<string, unknown>;
  /**
   * Записать значение в соседнее поле ввода по его id.
   *
   * Ровно один случай: выбор делается там, где оператор на него смотрит, а
   * хранится там, где объявлен. Файл выбирается кликом в дереве папки задачи,
   * а уезжает он в поле `document` — своё, со своим `target`. Без этого клик
   * в дереве мог бы только открыть файл на просмотр, и выбрать источник было
   * бы негде: единственным способом сказать «работай по этому документу»
   * оставалось назвать его словами в запросе.
   *
   * Имя поля виджет берёт из манифеста, а не знает по имени, — как и в
   * `inputs`. Записать он может только то, что в манифесте объявлено:
   * несуществующий id просто никуда не уедет (см. `configurableOf`).
   */
  setInput: (id: string, value: unknown) => void;
  resource: (
    resourceId: string,
    operation: string,
    params?: Record<string, string>,
  ) => Promise<unknown>;
  mutateResource: (
    resourceId: string,
    operation: string,
    payload: Record<string, unknown>,
  ) => Promise<unknown>;
  /**
   * Файлы текущего чата. Нет — интерфейс собран без чатов (тесты движка).
   *
   * Отдельно от `resource`: файл загружается телом запроса, а не JSON, и
   * список один на весь экран — его показывают и панель файлов, и строка
   * выбранного, и композер. Держать его в каждом виджете значило бы трижды
   * спрашивать сервер и трижды расходиться после загрузки.
   */
  chat?: ChatFilesContext;
  /**
   * Разговор открытого чата: черновик поля задачи, вопрос по выделенному,
   * правка отправленного запроса и его версии. Нет — интерфейс собран без
   * чатов (тесты движка), и виджеты работают как раньше.
   */
  conversation?: ConversationContext;
};

/** Запрос оператора в показанной ветке чата (`/api/chats/{thread}/turns`). */
export type ChatTurn = {
  message_id: string;
  /** Текст для правки: вопрос без того, что дописала к нему нода контекста. */
  question: string;
  /** Какая версия показана, с единицы. */
  version: number;
  versions: number;
  /** Чекпоинт, с которого прогон пойдёт заново при правке. */
  fork: string;
};

export type ConversationContext = {
  /** Ключ черновика поля задачи в `localStorage` (`lib/drafts.ts`). */
  draftKey: string;
  /** Цитата для поля задачи; `nonce` растёт с каждой новой. */
  quote: { text: string; nonce: number } | null;
  /** Запросы оператора по id сообщения: версии и место для правки. */
  turns: Record<string, ChatTurn>;
  /** Править и переключать нельзя: идёт прогон или запрос уже ушёл. */
  locked: boolean;
  /** Отправить исправленный запрос: прогон пойдёт заново с этого места новой веткой. */
  edit: (messageId: string, text: string) => void;
  /** Показать другую версию запроса. */
  switchVersion: (messageId: string, version: number) => void;
};

export type ChatFileEntry = {
  name: string;
  size: number;
  text?: boolean;
  diagram?: boolean;
};

export type ChatLibrary = {
  examples: Array<{ name: string; title: string; files: ChatFileEntry[] }>;
  published: Array<{ name: string; title: string; size: number }>;
};

export type ChatFilesContext = {
  /** Тред чата; null — чат ещё не заведён, он появится с первым файлом или прогоном. */
  threadId: string | null;
  /** null — список ещё не пришёл. */
  files: ChatFileEntry[] | null;
  limits?: { max_bytes: number; max_files: number; suffixes: string[] };
  loading: boolean;
  error: string;
  upload: (files: File[]) => Promise<void>;
  remove: (name: string) => Promise<void>;
  read: (name: string) => Promise<{ name: string; text: string }>;
  library: () => Promise<ChatLibrary>;
  attach: (source: { source: "examples" | "published"; name: string; example?: string }) => Promise<void>;
};

export type WidgetProps = {
  value: unknown;
  binding: WidgetBinding;
  mode: WidgetMode;
  readonly: boolean;
  context: SafeWidgetContext;
  onChange?: (value: unknown) => void;
  onAction?: (action: WidgetAction) => void;
};

export type WidgetDefinition = {
  type: string;
  version: string;
  modes: WidgetMode[];
  valueSchema?: JsonSchema;
  optionsSchema?: JsonSchema;
  component: ComponentType<WidgetProps>;
  errorBoundary?: ComponentType<{ children: ReactNode }>;
  /** Виджет рисует заголовок сам — surface не добавляет свой h3. */
  ownHeader?: boolean;
};
