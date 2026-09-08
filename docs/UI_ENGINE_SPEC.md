# Техническое задание: декларативный UI-движок для LangGraph

> [!NOTE]
> Проектная спецификация. Она включает целевые возможности сверх текущего MVP. Реализованный контракт и ограничения: [UI engine](UI_ENGINE_IMPLEMENTATION.md); пользовательские действия: [руководство](USER_GUIDE.md).

**Проект:** Orbita  
**Статус документа:** проект ТЗ для реализации  
**Версия:** 1.0  
**Дата:** 2026-08-30  
**Целевая аудитория:** product owner, архитектор, backend- и frontend-разработчики, QA, DevOps

---

## 1. Назначение документа

Документ описывает требования к полноценному UI-движку, который строит рабочий интерфейс для LangGraph-графов на основании:

1. топологии графа, полученной от LangGraph;
2. декларативного UI-манифеста;
3. схем входного и выходного состояния;
4. потока событий выполнения;
5. зарегистрированных универсальных виджетов.

После внедрения нового графа не должна требоваться отдельная реализация страницы на React. Без доработки фронтенда должны автоматически появляться топология, запуск, состояние узлов, timeline, стандартные входы, результаты, ошибки и interrupts. Уникальный интерфейс должен добавляться через новый переиспользуемый виджет, а не через отдельную страницу конкретного графа.

ТЗ составлено для текущего стека проекта: Python, LangGraph, Starlette, React, TypeScript и `@langchain/langgraph-sdk`.

---

## 2. Контекст и текущее состояние

В проекте уже реализована часть будущего движка:

- список графов получается через LangGraph assistants API;
- топология получается через `GET /assistants/{assistant_id}/graph`;
- frontend сам рассчитывает раскладку узлов и рёбер;
- выполнение запускается и возобновляется через LangGraph SDK;
- `values` и `updates` поступают потоково;
- пройденные узлы, циклы и текущий `interrupt()` отображаются в UI;
- графы `agent`, `drawio` и `jira` выбираются без отдельных страниц.

Оставшиеся ручные связи:

- сведения о графах находятся в `GRAPH_INFO` на frontend;
- названия и описания узлов находятся в `NODE_INFO` на frontend;
- панели `Chat`, `Docs`, `Cost`, `Files`, `Settings` подключены вручную;
- форма interrupt определяется ручными TypeScript type guards;
- маршрут текущего запуска восстанавливается только из событий открытого соединения;
- завершение `update` используется как приближение к lifecycle узла;
- отсутствует единая модель событий, timeline, история запусков и общий fallback для неизвестного state.

Движок должен развить существующий код, а не заменить LangGraph собственным оркестратором.

---

## 3. Термины

| Термин | Определение |
|---|---|
| Граф | Скомпилированный LangGraph с узлами, рёбрами и схемой состояния. |
| Assistant | Серверный экземпляр/конфигурация графа, доступный через LangGraph API. |
| Thread | Долгоживущий контекст выполнения и checkpoint-состояние. |
| Run | Один запуск или продолжение графа внутри thread. |
| Node execution | Одно выполнение узла. В цикле один узел может иметь несколько executions. |
| Topology | Набор узлов и рёбер графа без UI-представления. |
| UI-манифест | Версионированное декларативное описание графа, полей, действий, layout hints и виджетов. |
| Runtime adapter | Слой, преобразующий LangGraph topology/state/stream в стабильную внутреннюю модель UI. |
| Widget | Компонент для отображения или редактирования типизированного значения. |
| Surface | Область интерфейса: canvas, sidebar, main, bottom, modal, timeline. |
| Interrupt | Приостановка графа, требующая ответа оператора через `Command(resume=...)`. |
| Snapshot | Полное нормализованное состояние UI на определённый момент. |
| Event | Упорядоченное изменение runtime-состояния с идентификатором и sequence number. |

---

## 4. Цели

### 4.1. Основные цели

1. Новый LangGraph-граф получает базовый рабочий UI без изменений frontend-кода.
2. Топология, состояние и выполнение обновляются автоматически.
3. Один runtime одинаково обслуживает новый запуск, resume после interrupt, reconnect и просмотр истории.
4. Интерфейс неизвестного графа остаётся функциональным благодаря универсальным fallback-виджетам.
5. Семантика графа хранится рядом с backend-кодом и версионируется вместе с ним.
6. Специфические элементы UI подключаются через безопасный реестр виджетов.
7. Существующие графы `agent`, `drawio`, `jira` мигрируют без потери функций.

### 4.2. Метрики успеха

- новый граф с валидным манифестом отображается без правок в `web/src`;
- неизвестный граф без манифеста отображает топологию, ввод, JSON state, timeline и interrupts общего вида;
- не менее 95% стандартных сценариев покрываются встроенными виджетами;
- после reconnect UI восстанавливает фактическое состояние thread/run без повторного запуска;
- событие отображается не позднее 300 мс после получения браузером;
- граф до 200 узлов открывается не дольше 2 секунд при локальной сети и готовом сервере;
- изменение topology/manifest не требует сборки frontend;
- ручные `GRAPH_INFO`, `NODE_INFO` и проверки формата конкретных interrupts удалены после миграции.

---

## 5. Не входит в объём первой полной версии

Движок не должен:

- заменять LangGraph scheduler, checkpointer, assistants, threads или runs;
- позволять произвольный backend-код или React-код внутри JSON-манифеста;
- автоматически создавать качественный предметный UI только по произвольному JSON без метаданных;
- быть визуальным редактором Python-графа;
- менять topology работающего run;
- гарантировать точный DAG layout для графов произвольного размера свыше установленных лимитов;
- давать неавторизованному пользователю доступ к полному state, prompt, secrets или служебным stack traces;
- выполнять произвольные HTTP-запросы, команды или JavaScript, объявленные манифестом.

Визуальный конструктор графов, marketplace внешних виджетов и совместное редактирование могут быть отдельными последующими продуктами.

---

## 6. Пользователи и права

### 6.1. Роли

| Роль | Возможности |
|---|---|
| Viewer | Просмотр разрешённых графов, topology, завершённых run и безопасных результатов. |
| Operator | Создание thread/run, остановка run, ответы на interrupts, повтор разрешённых этапов. |
| Editor | Изменение доступных входов и редактируемых artifacts, если это разрешено манифестом. |
| Admin | Настройки, диагностика, регистрация манифестов и виджетов, просмотр технических данных. |

В однопользовательском развёртывании роли могут отображаться в один локальный профиль, но проверки прав должны оставаться на backend.

### 6.2. Основные пользовательские сценарии

1. Пользователь открывает приложение и выбирает граф.
2. Движок получает topology, манифест и capability-набор сервера.
3. Пользователь заполняет автоматически построенную форму запуска.
4. Run начинается, узлы и рёбра меняют статус в реальном времени.
5. Timeline показывает начало, завершение, длительность, ошибки и повторы узлов.
6. State-панели обновляются по мере получения данных.
7. При interrupt движок выбирает подходящий виджет и показывает форму ответа.
8. После ответа тот же run/thread продолжается без потери истории.
9. После обновления страницы состояние восстанавливается с сервера.
10. Пользователь открывает прошлый run и видит его topology, события и финальное состояние в read-only режиме.
11. Если манифест содержит неизвестный виджет, UI показывает безопасный fallback и диагностическое сообщение.

---

## 7. Архитектурные принципы

1. **LangGraph — источник истины выполнения.** Движок не дублирует thread/run state machine.
2. **Backend — источник UI-семантики.** Названия, описания, visibility и bindings не хранятся в коде конкретной React-страницы.
3. **Манифест декларативен.** В нём разрешены данные и ограниченные выражения, но не исполняемый код.
4. **Frontend недоверчив.** Любое значение с сервера валидируется до использования.
5. **Progressive enhancement.** Отсутствие манифеста или специализированного виджета не делает граф неработоспособным.
6. **Snapshot + ordered events.** Snapshot обеспечивает восстановление, события — быстрые обновления.
7. **Immutable run context.** Run сохраняет версию topology и манифеста, с которыми был запущен.
8. **Backward compatibility.** Новая minor-версия манифеста не ломает старый renderer.
9. **Separation of concerns.** Layout, runtime, manifest resolution, widgets и domain adapters разделены.
10. **Server-side authorization.** Скрытие кнопки во frontend не считается защитой операции.

---

## 8. Целевая архитектура

```text
Python graphs + UI manifests
            │
            ├── LangGraph API: assistants / threads / runs / topology
            └── Orbita UI API: manifests / capabilities / event snapshots
                                │
                         Runtime Adapter
                                │
          ┌─────────────────────┼──────────────────────┐
          │                     │                      │
    Runtime Store        Manifest Resolver      Widget Registry
          │                     │                      │
          └──────────────┬──────┴──────────────┬───────┘
                         │                     │
                    Graph Canvas        Generated Surfaces
                    + Timeline          + Actions/Interrupts
```

### 8.1. Backend-компоненты

- `ui_manifest` — Python-модели и загрузка деклараций;
- `ui_registry` — привязка `graph_id` к манифесту;
- `ui_api` — выдача манифеста, версии и capabilities;
- `event_adapter` — нормализация доступных LangGraph lifecycle-событий;
- `redaction` — фильтрация секретных/скрытых путей state;
- `manifest_validator` — проверка схемы при старте и в CI;
- опциональный `event_store` — долговременный timeline, если LangGraph API не обеспечивает достаточную историю lifecycle.

### 8.2. Frontend-компоненты

- `GraphRuntimeClient` — взаимодействие с LangGraph SDK и UI API;
- `RuntimeStore` — нормализованное состояние текущего graph/thread/run;
- `ManifestStore` — загрузка, валидация, кеш и совместимость версий;
- `GraphCanvas` — topology и статусы executions;
- `Timeline` — упорядоченная история событий;
- `SurfaceRenderer` — размещение панелей по манифесту;
- `WidgetRegistry` — встроенные и локально зарегистрированные виджеты;
- `ActionDispatcher` — разрешённые операции запуска, stop, resume, retry;
- `FallbackRenderer` — JSON, text, table, error и unknown widget.

---

## 9. Модель UI-манифеста

### 9.1. Общие требования

- формат передачи: JSON UTF-8;
- серверное представление допускается в Python через Pydantic/dataclasses;
- обязательна JSON Schema;
- обязательны `schema_version`, `manifest_version`, `graph_id`;
- размер одного манифеста по умолчанию не более 512 КБ;
- неизвестные необязательные поля игнорируются с warning;
- неизвестная major-версия отклоняется с понятной ошибкой;
- манифест должен быть детерминированным и не зависеть от текущего thread;
- персональные данные и secrets в манифесте запрещены.

### 9.2. Верхнеуровневая структура

```ts
type UiManifest = {
  schema_version: "1.0";
  manifest_version: string;
  graph_id: string;
  title: string;
  description?: string;
  icon?: string;
  tags?: string[];
  capabilities?: ManifestCapabilities;
  input?: InputBinding[];
  nodes?: Record<string, NodeManifest>;
  state?: StateBinding[];
  interrupts?: InterruptBinding[];
  surfaces?: SurfaceManifest[];
  actions?: ActionManifest[];
  redaction?: RedactionRule[];
  theme?: ThemeHints;
};
```

### 9.3. Пример манифеста Orbita

```json
{
  "schema_version": "1.0",
  "manifest_version": "2026.08.30.1",
  "graph_id": "agent",
  "title": "Архитектурная аналитика",
  "description": "Материалы задачи превращаются в требования, API, данные и архитектуру",
  "capabilities": {
    "new_thread": true,
    "stop_run": true,
    "resume_interrupt": true,
    "history": true,
    "retry_node": false
  },
  "input": [
    {
      "id": "question",
      "target": "messages",
      "widget": "chat-input",
      "required": true
    },
    {
      "id": "task",
      "target": "configurable.input_dir",
      "widget": "task-picker",
      "source": "/api/inputs"
    }
  ],
  "nodes": {
    "requirements": {
      "title": "Требования",
      "description": "Формирует однозначные требования и state machine",
      "group": "analysis",
      "output": {
        "path": "artifacts.requirements",
        "widget": "markdown"
      }
    },
    "approve": {
      "title": "Подтверждение публикации",
      "kind": "approval"
    }
  },
  "state": [
    {
      "id": "cost",
      "path": "cost",
      "title": "Стоимость",
      "widget": "cost-summary",
      "surface": "right",
      "order": 20
    },
    {
      "id": "artifacts",
      "path": "artifacts",
      "title": "Документы этапов",
      "widget": "artifact-list",
      "surface": "left",
      "order": 30
    }
  ],
  "interrupts": [
    {
      "id": "stage-approval",
      "match": { "path": "action", "equals": "stage" },
      "widget": "approval",
      "resume_schema": {
        "type": "object",
        "required": ["decision"],
        "properties": {
          "decision": { "enum": ["approved", "rejected"] },
          "reason": { "type": "string", "maxLength": 4000 }
        }
      }
    }
  ],
  "redaction": [
    { "path": "messages.*.response_metadata.raw_prompt", "mode": "remove" }
  ]
}
```

### 9.4. Описание узла

```ts
type NodeManifest = {
  title?: string;
  description?: string;
  icon?: string;
  kind?: "task" | "router" | "tool" | "approval" | "system";
  group?: string;
  color?: "neutral" | "info" | "success" | "warning" | "danger";
  hidden?: boolean;
  output?: WidgetBinding;
  badges?: ValueBinding[];
  details?: WidgetBinding[];
  layout?: {
    rank?: number;
    order?: number;
    collapsed?: boolean;
  };
};
```

Если node отсутствует в манифесте, renderer использует технический `id`, общий тип `task`, автоматическую позицию и JSON details.

### 9.5. Binding значения

Binding не должен содержать JavaScript. Поддерживаются:

- JSON Pointer или ограниченный dot-path;
- `default`;
- форматирование из белого списка;
- простые условия `exists`, `equals`, `in`, `not`, `all`, `any`;
- явный widget и его проверенные options.

```ts
type WidgetBinding = {
  path: string;
  widget: string;
  title?: string;
  visible_when?: Condition;
  options?: Record<string, JsonValue>;
  empty?: "hide" | "placeholder" | "show";
};
```

Произвольные expressions, `eval`, шаблоны с доступом к глобальному окружению и сетевые функции запрещены.

---

## 10. Получение и версионирование манифеста

### 10.1. API

Добавить служебные read-only endpoints:

```http
GET /api/ui/capabilities
GET /api/ui/graphs/{graph_id}/manifest
GET /api/ui/assistants/{assistant_id}/bundle
```

`bundle` возвращает согласованный набор для первичного открытия:

```json
{
  "assistant": { "assistant_id": "...", "graph_id": "agent" },
  "topology": { "nodes": [], "edges": [] },
  "manifest": {},
  "etag": "sha256:...",
  "generated_at": "2026-08-30T10:00:00Z"
}
```

Если дублирование topology в пользовательском endpoint нежелательно, `bundle` может содержать URL и ETag topology. Главное требование — frontend должен обнаруживать несовместимую пару topology/manifest.

### 10.2. HTTP-поведение

- поддержать `ETag` и `If-None-Match`;
- `Cache-Control` настраивается; для dev допустим `no-cache`;
- неизвестный graph: `404`;
- невалидный манифест: `500` с безопасным `error_code`, подробности только в server log;
- недостаточно прав: `403`;
- ответ включает поддержанную major/minor schema version.

### 10.3. Version policy

- `schema_version` описывает контракт движка;
- `manifest_version` меняется автором при изменении семантики UI;
- `topology_hash` считается по нормализованным nodes/edges;
- каждый run snapshot хранит `schema_version`, `manifest_version`, `topology_hash`;
- просмотр старого run использует его сохранённый manifest snapshot либо совместимый архивный манифест;
- изменение манифеста не должно менять уже открытый исторический run.

---

## 11. Нормализованная runtime-модель

Frontend не должен распространять формат SDK по всем компонентам. Runtime adapter формирует единое состояние:

```ts
type RuntimeSnapshot = {
  assistantId: string;
  graphId: string;
  threadId: string | null;
  runId: string | null;
  topologyHash: string;
  manifestVersion: string;
  connection: "idle" | "connecting" | "live" | "reconnecting" | "offline";
  runStatus: "idle" | "queued" | "running" | "interrupted" | "completed" | "failed" | "cancelled";
  state: Record<string, unknown>;
  executions: Record<string, NodeExecution>;
  executionOrder: string[];
  interrupts: RuntimeInterrupt[];
  events: RuntimeEvent[];
  lastSequence: number;
  error?: RuntimeError;
};
```

```ts
type NodeExecution = {
  executionId: string;
  nodeId: string;
  attempt: number;
  status: "queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted";
  startedAt?: string;
  finishedAt?: string;
  durationMs?: number;
  update?: unknown;
  error?: RuntimeError;
};
```

Один `nodeId` может иметь несколько `NodeExecution` из-за циклов, retry и повторного resume.

---

## 12. Событийная модель

### 12.1. Требования

Каждое нормализованное событие содержит:

```ts
type EventEnvelope<T> = {
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
};
```

- `eventId` уникален в пределах системы;
- `sequence` строго возрастает в пределах run;
- повторно доставленное событие не применяется второй раз;
- события с пропуском sequence помещают runtime в `reconnecting` и вызывают snapshot reconciliation;
- server timestamp хранится отдельно от client receive time;
- payload проходит redaction до передачи пользователю.

### 12.2. Обязательные типы событий

```text
thread.created
run.created
run.queued
run.started
node.queued
node.started
node.update
node.completed
node.failed
interrupt.created
interrupt.resolved
state.snapshot
run.completed
run.failed
run.cancelled
connection.warning
```

Дополнительные типы:

```text
tool.started / tool.completed / tool.failed
llm.started / llm.token / llm.completed
artifact.created / artifact.updated
publication.started / publication.completed
metric.recorded
log.created
```

Token events выключены по умолчанию для timeline и могут агрегироваться.

### 12.3. Источник событий

Runtime adapter должен использовать доступные штатные stream modes и lifecycle events LangGraph SDK. Если установленная серверная версия не даёт отдельные `node.started` и replay, допускается backend adapter/event store.

Запрещено считать `updates` полноценной долговременной историей: они могут сообщить о результате узла, но не гарантируют восстановление времени старта, ошибок и пропущенных событий.

### 12.4. Reconciliation

При открытии, reconnect или обнаружении gap:

1. получить thread/run status;
2. получить последний доступный state snapshot;
3. получить события после `lastSequence`, если replay поддержан;
4. удалить optimistic flags, не подтверждённые сервером;
5. пересчитать executions, active nodes и interrupts;
6. продолжить live stream.

Snapshot имеет приоритет над локально вычисленным state, а подтверждённые server events — над optimistic UI.

---

## 13. Жизненный цикл run

### 13.1. Новый запуск

1. Движок валидирует input по манифесту.
2. Создаёт thread, если он отсутствует.
3. Формирует LangGraph input, configurable/context из bindings.
4. Добавляет клиентский idempotency key.
5. Запускает stream.
6. Блокирует несовместимые действия до server acknowledgement.
7. Показывает очередь и выполнение.

### 13.2. Interrupt и resume

- interrupt выбирается первым совпавшим правилом `interrupts` с учётом `priority`;
- payload показывается только разрешёнными bindings;
- resume-форма строится по `resume_schema`;
- ответ валидируется frontend для UX и повторно backend для безопасности;
- повторная отправка одного ответа должна быть идемпотентной;
- при конфликте `interrupt уже разрешён` UI обновляет snapshot, а не предлагает отправить снова;
- при нескольких interrupts отображается очередь или набор карточек согласно server semantics.

### 13.3. Stop/cancel

- stop доступен только при соответствующей capability и праве;
- перед отправкой показывается подтверждение, если run выполняет side-effect node;
- после cancel UI не помечает активную ноду как completed;
- неизвестный результат отмены приводит к reconciliation.

### 13.4. Retry

Retry node/stage не включается только визуально. Он доступен, если backend предоставляет безопасную семантику checkpoint/fork/replay.

Минимальные варианты:

- повтор всего run с тем же input;
- fork thread от checkpoint;
- retry failed execution через поддержанный сервером механизм.

Манифест только объявляет доступность и подпись; разрешение и исполнение остаются на backend.

---

## 14. Graph Canvas

### 14.1. Функции

- автоматическая отрисовка nodes/edges;
- поддержка условных, обратных и дальних рёбер;
- поддержка циклов и счётчика executions;
- отображение параллельно выполняющихся узлов;
- статусы `idle`, `queued`, `running`, `completed`, `interrupted`, `failed`, `cancelled`;
- визуальное различие node kinds;
- выбор узла и открытие details;
- центрирование, zoom, pan, fit-to-screen;
- minimap для больших графов;
- поиск узла;
- группировка узлов;
- сворачивание служебных групп;
- режимы `topology`, `live run`, `historical run`;
- экспорт изображения и текстового представления;
- доступная табличная/списочная альтернатива canvas.

### 14.2. Layout

Движок предоставляет минимум два layout engine:

1. существующий компактный ASCII layout для терминального стиля Orbita;
2. SVG/HTML layout для масштабирования и интерактивности.

Layout должен:

- быть детерминированным для одинаковой topology;
- учитывать `rank/order/group` hints, но работать без них;
- не менять резко позиции узлов при обычном state update;
- вычисляться в Web Worker при превышении 50 узлов;
- кешироваться по `topology_hash + layout_options`;
- иметь лимиты времени и fallback на простой layered layout.

### 14.3. Выбор узла

Details узла включают:

- title, id, description, kind;
- входящие/исходящие рёбра и branch labels;
- список executions;
- duration и attempt;
- разрешённые input/output bindings;
- безопасную ошибку;
- связанные artifacts и logs;
- доступные actions.

---

## 15. Timeline и история

Timeline должен:

- показывать события в server sequence;
- группировать технический шум по node execution;
- отображать длительности и параллельность;
- фильтровать по node, status, event type и времени;
- связывать запись с node на canvas;
- различать retry, цикл и resume;
- показывать interrupts и ответ оператора без раскрытия скрытых значений;
- иметь виртуализацию после 500 элементов;
- экспортировать разрешённый JSON для диагностики;
- работать в live и historical режиме.

История должна позволять:

- список threads/runs с пагинацией;
- фильтры по graph, status, датам и текстовому безопасному title;
- открытие final state;
- просмотр версии манифеста/topology;
- продолжение только актуального interrupted thread;
- fork/retry при наличии capability;
- сравнение двух artifacts как отдельный виджет.

---

## 16. Widget Registry

### 16.1. Контракт

```ts
type WidgetDefinition = {
  type: string;
  version: string;
  modes: Array<"view" | "edit" | "input" | "interrupt">;
  valueSchema?: JsonSchema;
  optionsSchema?: JsonSchema;
  component: React.ComponentType<WidgetProps>;
  errorBoundary?: React.ComponentType;
};
```

```ts
type WidgetProps = {
  value: unknown;
  binding: WidgetBinding;
  mode: "view" | "edit" | "input" | "interrupt";
  readonly: boolean;
  context: SafeWidgetContext;
  onChange?: (value: unknown) => void;
  onAction?: (action: WidgetAction) => void;
};
```

`SafeWidgetContext` не должен содержать raw auth token, произвольный fetch или прямой SDK client.

### 16.2. Обязательные встроенные виджеты

| Widget | Назначение |
|---|---|
| `text` | Короткий текст. |
| `markdown` | Безопасный Markdown. |
| `code` | Код с языком и копированием. |
| `json` | Сворачиваемое дерево JSON. |
| `table` | Массив объектов с сортировкой и пагинацией. |
| `key-value` | Компактный объект. |
| `number` | Число с форматированием. |
| `money` | Денежное значение и валюта. |
| `progress` | Значение/лимит. |
| `status` | Статус с tone. |
| `messages` | История сообщений. |
| `chat-input` | Новый human message. |
| `form` | Форма из JSON Schema. |
| `approval` | Подтверждение/отклонение с причиной; показывает черновики из тела остановки. |
| `draft-list` | Черновики будущих объектов: страница или задача телом, полями запроса и пометкой «создать»/«перезаписать». |
| `artifact-list` | Результаты этапов. |
| `document-preview` | Просмотр документа. |
| `document-diff` | Сравнение версий. |
| `file-list` | Список входных файлов. |
| `task-picker` | Выбор task/input directory через контролируемый source adapter. |
| `cost-summary` | Токены, вызовы, стоимость, budget. |
| `publication` | Итог публикации и страницы. |
| `published-list` | Список опубликованных документов задачи. |
| `jira-project` | Выбор проекта Jira: справочник из трекера плюс ручной ввод ключа. |
| `issue-list` | Заведённые задачи: ключ, заголовок, ссылка, отказы и предупреждения. |
| `error` | Безопасное отображение ошибки. |
| `unknown` | Fallback с типом и JSON preview. |

### 16.3. Регистрация

- встроенные widgets регистрируются статически при сборке;
- проектные widgets подключаются TypeScript-модулем в allowlist;
- манифест не может указать URL удалённого JavaScript;
- имя неизвестного widget приводит к `unknown`, warning и telemetry;
- конфликт двух регистраций одного `type@major` является build error;
- widget обязан иметь Error Boundary.

---

## 17. Формы и действия

### 17.1. Генерация форм

Формы строятся по безопасному подмножеству JSON Schema:

- `string`, `number`, `integer`, `boolean`, `object`, `array`;
- `enum`, `const`, `default`;
- `required`, `min/max`, `minLength/maxLength`, `pattern`;
- `format`: `date`, `date-time`, `uri`, `multiline`, `markdown`;
- вложенность не более 10;
- массив по умолчанию не более 1000 элементов;
- ошибки показываются рядом с полем и в summary.

### 17.2. Action model

Разрешённые системные actions:

```text
thread.create
run.start
run.stop
run.retry
thread.fork
interrupt.resume
artifact.open
artifact.edit
artifact.compare
publication.open
resource.refresh
```

Action содержит `id`, `kind`, label, permission, confirmation policy, input schema и visibility condition. Frontend отправляет action только через `ActionDispatcher`, который проверяет capability, текущий runtime status и CSRF/auth requirements.

Произвольный URL разрешается только для навигации и только после server-side allowlist/normalization. Произвольный HTTP method из манифеста запрещён.

---

## 18. State, обновления и bindings

### 18.1. Правила state

- frontend хранит последний server-confirmed snapshot;
- updates применяются как patch согласно семантике adapter, а не универсальным shallow merge;
- reducers LangGraph не воспроизводятся во frontend, если сервер уже присылает `values`;
- `values` периодически сверяет вычисленный state;
- каждое отображаемое поле имеет размерный лимит;
- большие artifacts загружаются по ссылке/endpoint лениво;
- бинарные значения не встраиваются в state JSON;
- отсутствующий path отличается от `null` и пустой строки.

### 18.2. Visibility и redaction

Redaction выполняется на backend до сериализации. Frontend дополнительно соблюдает manifest visibility, но это UX, а не security boundary.

Поддерживаются режимы:

- `remove` — удалить поле;
- `mask` — заменить фиксированной маской;
- `truncate` — ограничить длину;
- `metadata_only` — оставить тип/размер без содержимого;
- `role` — показывать только указанным ролям.

Системные defaults должны скрывать authorization headers, cookies, API keys, raw provider request/response и потенциальные prompt secrets.

---

## 19. Автоматическое обновление

### 19.1. Runtime updates

- live state обновляется через stream без polling при здоровом соединении;
- reconnect использует exponential backoff с jitter: 0.5, 1, 2, 5, 10 секунд, максимум 30 секунд;
- вкладка показывает состояние соединения;
- после возвращения сети выполняется reconciliation;
- background tab может снижать частоту визуальных перерисовок, не теряя события;
- updates группируются в один render frame при высокой частоте.

### 19.2. Topology и manifest updates

- при смене assistant выполняется загрузка нового bundle;
- в dev-режиме допустима автоматическая проверка ETag;
- в production проверка выполняется при открытии, смене assistant и новом run;
- running/historical run продолжает использовать зафиксированную версию;
- новая версия применяется к следующему run или после явного подтверждения reload;
- несовпадение topology/manifest показывается как warning, неизвестные nodes всё равно отображаются.

---

## 20. Работа без манифеста и деградация

Если манифест отсутствует:

1. title берётся из assistant name или graph id;
2. topology рисуется полностью;
3. node title равен node id;
4. input представлен базовым chat/JSON input;
5. state показывается JSON tree;
6. interrupt показывается JSON Schema inference/form fallback либо JSON textarea с подтверждением;
7. неизвестные updates попадают в timeline;
8. опасные действия, не подтверждённые capabilities, скрываются.

Ошибки отдельных widgets не должны ронять приложение. На их месте отображаются widget name, binding path, correlation id и кнопка повторного render.

---

## 21. API и интеграция с LangGraph

### 21.1. Принцип разделения API

Штатный LangGraph API продолжает обслуживать:

- assistants;
- topology;
- threads;
- runs;
- state/checkpoints;
- stream;
- interrupt resume.

Orbita UI API обслуживает только отсутствующую UI-семантику:

- manifests;
- capabilities;
- безопасные resource adapters;
- event replay/snapshots, если их нельзя получить штатно;
- redacted diagnostics.

### 21.2. Capability negotiation

`GET /api/ui/capabilities` возвращает:

```json
{
  "engine": "orbita-ui",
  "engine_version": "1.0.0",
  "manifest_versions": ["1.0"],
  "event_versions": ["1.0"],
  "features": {
    "event_replay": true,
    "node_lifecycle": true,
    "historical_manifest": true,
    "thread_fork": false,
    "node_retry": false
  },
  "limits": {
    "manifest_bytes": 524288,
    "state_preview_bytes": 1048576,
    "timeline_page_size": 200
  }
}
```

Frontend не должен определять возможность операции только по версии пакета.

### 21.3. Resource adapters

Для task picker, файлов, публикаций и будущих источников манифест ссылается на зарегистрированный `resource_id`, а не на произвольный URL:

```json
{
  "resource_id": "orbita.tasks",
  "operation": "list"
}
```

Backend registry связывает это с конкретным endpoint и permission. Так сохраняются SSRF-защита, аудит и стабильная типизация.

---

## 22. Frontend store и управление состоянием

Store разделяется на slices:

```text
catalog       assistants, graph metadata
manifest      schema, bindings, surfaces, cache
runtime       thread, run, connection, current state
executions    node executions and indexes
timeline      paginated events
ui            selection, zoom, opened panels, filters
drafts        unsent form/interrupt values
permissions   capabilities and granted actions
```

Требования:

- transitions runtime реализуются чистыми reducer-функциями;
- применение события покрывается unit tests;
- derived data вычисляются selectors;
- большие state fragments не копируются при каждом token event;
- draft не смешивается с server-confirmed state;
- смена graph очищает несовместимый thread runtime;
- URL может хранить `assistant`, `thread`, `run`, выбранный node/tab без секретов;
- localStorage используется только для пользовательских предпочтений и последних безопасных идентификаторов.

---

## 23. UI surfaces и responsive layout

Стандартные surfaces:

- `header` — выбор графа, run status, connection;
- `left` — inputs/resources/artifacts;
- `main` — graph canvas и primary widget;
- `right` — details, metrics, cost;
- `bottom` — timeline/logs;
- `modal` — interrupt, preview, destructive confirmation;
- `drawer` — mobile details.

Манифест задаёт предпочтительное размещение и order, но renderer адаптирует layout:

- desktop: три колонки + timeline;
- tablet: canvas + drawers;
- mobile: последовательные tabs;
- пользователь может скрыть/изменить размер панелей;
- настройка layout сохраняется по `graph_id` и schema major;
- обязательный interrupt нельзя спрятать за закрытой панелью.

---

## 24. Ошибки и диагностика

### 24.1. Модель ошибки

```ts
type RuntimeError = {
  code: string;
  message: string;
  category: "validation" | "auth" | "network" | "server" | "graph" | "widget" | "unknown";
  retryable: boolean;
  correlationId?: string;
  nodeId?: string;
  executionId?: string;
};
```

### 24.2. UX ошибок

- validation error показывается у поля;
- node error привязывается к node и timeline;
- connection error не стирает последний snapshot;
- auth error запускает существующий безопасный auth flow;
- manifest error включает fallback mode;
- raw stack trace доступен только admin в диагностике;
- retry предлагается только при `retryable=true` и capability;
- каждая server error имеет correlation id.

---

## 25. Безопасность

### 25.1. Обязательные требования

1. Все UI API endpoints используют ту же или более строгую авторизацию, что и защищаемые данные.
2. Permission проверяется backend для каждого mutating action.
3. Markdown и HTML санитизируются; inline script, event handlers и опасные URL запрещены.
4. XHTML/Confluence storage по умолчанию показывается как текст или через отдельный sanitizer, не вставляется напрямую через `dangerouslySetInnerHTML`.
5. Манифест не загружает внешний JS/CSS.
6. Widget options проходят JSON Schema validation.
7. Resume payload валидируется backend.
8. State redaction применяется до логирования и отправки.
9. Экспорт событий учитывает permissions и redaction.
10. URL из publication проверяется по разрешённым схемам (`https`, при необходимости контролируемый `http`); `javascript:`, `data:` и локальные file paths блокируются.
11. Ограничиваются размеры manifest, state, events, forms и documents.
12. Content Security Policy запрещает eval и неподтверждённые источники.
13. Mutating actions защищены от CSRF согласно выбранной модели auth.
14. Audit log фиксирует start, stop, resume, reject, retry, edit и administrative changes.
15. Secrets никогда не сохраняются в localStorage/session replay/error telemetry.

### 25.2. Threat cases

Тестами должны быть покрыты:

- XSS через Markdown, node title, error, artifact и manifest;
- prototype pollution через state/manifest keys;
- path traversal через resource binding;
- SSRF через source/action URL;
- повтор resume-запроса;
- просмотр чужого thread/run;
- обход hidden field через прямой API;
- oversized JSON/event flood;
- stale manifest, предлагающий запрещённое действие;
- утечка bearer token в лог/URL.

---

## 26. Нефункциональные требования

### 26.1. Производительность

- initial JS bundle движка: целевой gzip не более 350 КБ без lazy widgets;
- тяжёлые widgets загружаются lazy;
- до 200 nodes и 500 edges — интерактивная работа;
- до 10 000 timeline events — работа с виртуализацией и пагинацией;
- не более одной визуальной перерисовки на animation frame;
- JSON preview не раскрывает объекты глубже 3 уровней до действия пользователя;
- documents более 1 МБ загружаются отдельно и предупреждают о размере;
- layout не блокирует main thread более 50 мс;
- memory не растёт бесконечно на token events: применяется агрегация/retention.

### 26.2. Надёжность

- duplicate events безопасны;
- out-of-order events обрабатываются буфером или reconciliation;
- reload не запускает новый run;
- navigation не отправляет повторный resume;
- frontend crash отдельного widget изолирован;
- API timeout настраиваем и имеет retry только для идемпотентных запросов;
- graceful fallback при несовместимости minor features.

### 26.3. Доступность

- WCAG 2.1 AA для основных сценариев;
- весь граф доступен с клавиатуры;
- статусы различаются не только цветом;
- canvas имеет текстовую альтернативу;
- focus после появления interrupt переходит в диалог и возвращается после закрытия;
- live updates используют ненавязчивый `aria-live`;
- prefers-reduced-motion отключает анимации;
- масштаб 200% не ломает основные действия.

### 26.4. Локализация

- UI strings не хранятся внутри generic widgets как русские литералы;
- manifest title/description допускают строку или словарь locale;
- fallback locale — `ru`, затем первая доступная строка;
- даты, числа и деньги форматируются через Intl;
- технические ids не переводятся.

---

## 27. Наблюдаемость

### 27.1. Backend metrics

- manifest load/validation duration и failures;
- active streams;
- reconnect/replay count;
- event lag;
- event store write/read latency;
- redaction failures;
- action success/failure по kind;
- payload size distributions;
- dropped/aggregated events.

### 27.2. Frontend telemetry

- time to graph visible;
- time to first live event;
- stream disconnects;
- reconciliation count/result;
- unknown widgets;
- binding resolution errors;
- widget render failures;
- layout duration;
- пользовательские действия без содержимого документов и prompts.

Все записи содержат correlation id, graph id, run id и версии контракта, если это разрешено политикой данных.

---

## 28. Тестирование

### 28.1. Backend unit tests

- manifest validation;
- schema version compatibility;
- registry resolution;
- topology hash;
- ETag/304;
- capability calculation;
- redaction rules;
- permissions;
- event normalization и sequence;
- idempotency start/resume;
- limits и безопасные errors.

### 28.2. Frontend unit tests

- runtime reducers для каждого event type;
- duplicate/gap/out-of-order events;
- snapshot reconciliation;
- binding resolution;
- visibility conditions;
- widget selection/fallback;
- form validation;
- interrupt matching;
- status derivation;
- layout stability;
- redaction assumptions не используются как security.

### 28.3. Contract tests

- JSON Schema манифеста;
- Python model ↔ JSON Schema ↔ TypeScript types;
- LangGraph adapter fixtures для установленной версии SDK;
- topology всех трёх текущих графов;
- interrupt payload `stage` и `publish`;
- state `cost`, `publication`, `artifacts`, `messages`;
- старый minor manifest открывается новым frontend;
- неизвестные fields/widgets корректно деградируют.

### 28.4. Integration/E2E

Минимальная матрица:

1. новый run до completion;
2. run с tools cycle;
3. stage interrupt → approve;
4. stage interrupt → reject;
5. publish interrupt → approve;
6. cancel running run;
7. node error;
8. disconnect/reconnect во время узла;
9. reload на interrupt;
10. reload после completion;
11. переключение `agent` → `drawio` → `jira`;
12. граф без manifest;
13. неизвестный widget;
14. manifest/topology mismatch;
15. historical run со старым manifest;
16. параллельные nodes;
17. цикл одного node несколько раз;
18. security payloads XSS/path traversal/oversize.

### 28.5. Visual tests

- small/medium/large topology;
- long node titles;
- обратные и условные рёбра;
- все statuses;
- desktop/tablet/mobile;
- light/dark/high contrast при наличии тем;
- reduced motion;
- кириллица и длинные английские строки.

---

## 29. Миграция текущего приложения

### Этап 0. Контракты и фиксация поведения

- добавить contract fixtures существующих topology, state и interrupts;
- зафиксировать E2E основных трёх графов;
- определить поддерживаемые возможности установленного LangGraph SDK/server;
- выбрать источник replay/lifecycle events.

**Результат:** новая архитектура может сравниваться с текущим UI.

### Этап 1. Manifest foundation

- реализовать Python-модель и JSON Schema манифеста;
- добавить registry для `agent`, `drawio`, `jira`;
- добавить manifest/capabilities endpoints;
- перенести `GRAPH_INFO` и `NODE_INFO` в backend manifests;
- реализовать frontend ManifestStore и fallback.

**Критерий:** изменение подписи узла не требует frontend build.

### Этап 2. Runtime Store

- обернуть `useStream` адаптером;
- ввести RuntimeSnapshot, reducers и selectors;
- перенести path/active/waitingAt из `App.tsx` в store;
- поддержать duplicate/gap/reconciliation;
- сохранить текущую GraphView как первый renderer.

**Критерий:** reload/reconnect восстанавливает фактический run status.

### Этап 3. Widget Registry и surfaces

- оформить текущие Chat, Cost, Docs, Files и Approval как widgets/adapters;
- создать JSON/Markdown/form/error fallbacks;
- реализовать SurfaceRenderer;
- удалить ручное подключение domain panels из App.

**Критерий:** состав панелей `agent` определяется manifest.

### Этап 4. Lifecycle events и Timeline

- реализовать нормализованные events;
- при необходимости добавить event replay/store;
- добавить executions и timeline;
- корректно показать cycles, parallel nodes, errors, interrupts;
- добавить historical view.

**Критерий:** активная нода, длительности и история не выводятся из эвристики `updates`.

### Этап 5. Интерактивный canvas

- SVG/HTML renderer, pan/zoom/search/minimap;
- node details и связь с timeline;
- Web Worker layout;
- ASCII renderer оставить как selectable/export mode.

**Критерий:** граф до 200 узлов соответствует performance/accessibility требованиям.

### Этап 6. Продвинутое управление

- run history;
- retry/fork при поддержке backend;
- artifact edit/diff/versioning;
- audit log;
- production observability и hardening.

**Критерий:** оператор управляет полным жизненным циклом без чтения server logs.

---

## 30. Предлагаемая структура кода

```text
src/agent/ui_engine/
  models.py
  registry.py
  manifests.py
  capabilities.py
  redaction.py
  events.py
  api.py
  schemas/
    ui-manifest-v1.schema.json
    runtime-event-v1.schema.json
  graph_manifests/
    agent.py
    drawio.py
    jira.py

web/src/engine/
  api/
    client.ts
    langgraphAdapter.ts
  manifest/
    types.ts
    validate.ts
    bindings.ts
  runtime/
    types.ts
    reducer.ts
    selectors.ts
    reconciliation.ts
  canvas/
    GraphCanvas.tsx
    AsciiGraphCanvas.tsx
    layout.worker.ts
  timeline/
    Timeline.tsx
  surfaces/
    SurfaceRenderer.tsx
  widgets/
    registry.ts
    builtins/
  actions/
    dispatcher.ts
  security/
    safeMarkdown.ts
    safeUrl.ts
```

Точное разбиение может меняться, но runtime не должен зависеть от конкретных Orbita panels, а widgets не должны напрямую управлять LangGraph SDK.

---

## 31. Критерии приёмки полноценного движка

### 31.1. Автоматическое подключение графа

Дано: в `langgraph.json` добавлен новый граф `demo`, backend выдаёт валидный manifest.  
Когда: пользователь открывает приложение.  
Тогда:

- `demo` появляется в каталоге;
- topology отображается автоматически;
- input строится из manifest;
- запуск работает;
- статусы узлов и timeline обновляются live;
- outputs отображаются указанными widgets;
- frontend source не изменялся.

### 31.2. Граф без манифеста

- открывается без fatal error;
- topology и технические ids видны;
- запуск возможен через generic input, если это разрешено сервером;
- state и interrupt доступны через безопасный fallback;
- UI сообщает о fallback mode.

### 31.3. Reconnect

- после разрыва соединения последний snapshot остаётся виден;
- UI показывает reconnect status;
- после восстановления не дублируются executions;
- пропущенные изменения появляются через replay/snapshot;
- completed run не запускается повторно.

### 31.4. Interrupt

- подходящий widget определяется manifest rule;
- invalid resume не отправляется;
- повторный submit не выполняет действие дважды;
- reload сохраняет ожидающий interrupt;
- после resume canvas/timeline продолжают тот же thread/run context.

### 31.5. Версионность

- изменение current manifest применяется к новым runs;
- historical run отображается со своей сохранённой версией;
- неизвестная major version включает fallback и понятную ошибку;
- неизвестное optional поле minor version не ломает UI.

### 31.6. Безопасность

- XSS-набор не выполняет script ни в одном встроенном widget;
- пользователь без права не может выполнить action прямым API-запросом;
- redacted field отсутствует в network response;
- внешний JavaScript из manifest загрузить невозможно;
- audit содержит mutating action и actor, но не secret payload.

---

## 32. Definition of Done

Функция считается завершённой, если:

- реализованы backend и frontend contracts;
- JSON Schema опубликована в репозитории;
- есть миграция/manifest для трёх текущих графов;
- unit, contract, integration и обязательные security tests проходят в CI;
- TypeScript build и Python lint/test проходят;
- endpoints документированы;
- есть upgrade note и rollback procedure;
- добавлены metrics/logging без секретов;
- проверены reconnect, reload и historical view;
- пройдена accessibility-проверка основных сценариев;
- удалены замещённые ручные mappings и эвристики;
- README содержит пример подключения четвёртого графа без изменения frontend;
- владелец продукта принял критерии раздела 31.

---

## 33. Риски и меры

| Риск | Последствие | Мера |
|---|---|---|
| SDK не отдаёт полный lifecycle/replay | Нет точного active/timeline | Capability negotiation; backend event adapter/store. |
| Manifest превращается в язык программирования | Сложность и уязвимости | Ограниченные bindings/conditions; custom widget для сложной логики. |
| State слишком велик | Медленный UI и память | Lazy artifacts, limits, pagination, preview metadata. |
| Граф изменился во время run | Неверная визуализация истории | Immutable topology/manifest snapshot на run. |
| Custom widgets ломают весь UI | Потеря управления run | Registry validation, lazy loading, Error Boundary, fallback. |
| Frontend повторяет reducers LangGraph неверно | Рассинхронизация state | `values`/snapshot как источник истины, reconciliation. |
| Автоматический UI раскрывает чувствительные поля | Утечка данных | Backend redaction и allowlist отображаемого state. |
| Retry вызывает повтор side effects | Дубли публикаций/записей | Backend capability, idempotency, checkpoint semantics, confirmation. |
| Большой граф плохо раскладывается | Нечитаемый canvas | Groups, search, minimap, worker, list fallback. |

---

## 34. Решения, которые необходимо подтвердить до реализации

Ниже приведены рекомендуемые defaults. Они не блокируют прототип, но должны быть зафиксированы архитектурным решением.

1. **Хранение manifest:** Python-модели рядом с графами с выдачей как JSON. Рекомендуется для типизации и совместной версии с backend.
2. **Lifecycle history:** сначала проверить штатный replay установленного LangGraph server; при нехватке добавить append-only event store.
3. **Canvas:** сохранить ASCII как стиль/экспорт, для полноценного UX добавить SVG/HTML renderer.
4. **Store:** использовать небольшой reducer-based store; конкретная библиотека выбирается после прототипа нагрузки.
5. **Validation:** одна JSON Schema, из которой проверяются Python output и TypeScript input; не поддерживать две независимо написанные схемы.
6. **Historical manifest:** сохранять snapshot на уровне run metadata/event store, а не пытаться реконструировать его из текущего кода.
7. **Custom widgets:** только compile-time allowlist в первой версии; remote plugins не поддерживать.
8. **Authorization:** распространить текущую bearer/session модель на UI API, затем перейти к полноценным ролям при многопользовательском режиме.

---

## 35. Минимальный вертикальный прототип

До полной реализации рекомендуется сделать один сквозной prototype за ограниченный срок:

1. manifest endpoint для `agent`;
2. перенос `GRAPH_INFO` и пяти `NODE_INFO`;
3. bindings для `cost`, `artifacts`, `messages`;
4. registry из `json`, `markdown`, `cost-summary`, `approval`;
5. RuntimeStore поверх текущего `useStream`;
6. восстановление interrupted thread после reload;
7. простой timeline `node.completed` на базе updates с явной пометкой ограниченного lifecycle;
8. один synthetic graph, добавляемый без изменения frontend.

Prototype считается успешным, если synthetic graph отображается и запускается только после добавления Python graph + manifest + `langgraph.json`, а существующий `agent` сохраняет чат, стоимость и approval.

После prototype отдельно принимается решение о backend event store на основании фактических возможностей установленного LangGraph API, а не предположений.

