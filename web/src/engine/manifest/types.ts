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
  redaction?: Array<{ path: string; mode: string }>;
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
