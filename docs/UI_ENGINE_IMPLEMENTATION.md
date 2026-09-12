# Declarative UI engine: эксплуатация и расширение

[Документация](README.md) / [Архитектура](ARCHITECTURE.md) / Контракты UI

Это техническое руководство по реализованному движку. Пользовательские действия описаны в [руководстве по интерфейсу](USER_GUIDE.md), запуск и проверки — в [руководстве разработчика](DEVELOPMENT.md).

Реализация следует контракту [`UI_ENGINE_SPEC.md`](UI_ENGINE_SPEC.md) и не
заменяет scheduler/checkpointer LangGraph. LangGraph остаётся источником истины
для assistants, threads, runs, topology, state и stream; Orbita UI API добавляет
только UI-семантику, capability negotiation, безопасные resources и
валидацию действий.

## Компоненты

- `src/agent/ui_engine/models.py` — Python transport types;
- `manifests.py` — limits, compatibility, canonical JSON, ETag и topology hash;
- `registry.py` — compile-time allowlist manifests и resource adapters;
- `graph_manifests/` — версии UI для `agent`, `drawio`, `jira`, `prep`, `audit`, `update`;
- `redaction.py` — backend `remove`, `mask`, `truncate`, `metadata_only`, `role`;
- `events.py` — per-run sequence, deduplication и bounded replay primitives;
- `schemas/` — JSON Schema manifest/event v1;
- `web/src/engine/` — недоверчивый manifest resolver, reducer runtime,
  reconciliation, canvas, timeline, surfaces, registry, built-ins и dispatcher.

Манифест не содержит JavaScript, URL запросов или expressions. Widget registry
статический; неизвестный widget попадает в `unknown` с JSON preview и Error
Boundary. `SafeWidgetContext` не отдаёт token, raw fetch или SDK client.

## HTTP API

Все `/api/ui/*` используют тот же обязательный Bearer flow `API_ADMIN_TOKEN`, что и
остальной служебный API. Manifest/capabilities поддерживают `ETag`,
`If-None-Match`, `Cache-Control: no-cache` и безопасные error codes.

| Метод и путь | Назначение |
| :--- | :--- |
| `GET /api/settings` | Описание настроек, значения с маскированием секретов |
| `PUT /api/settings` | Сохранение допустимых изменений в `.env` |
| `GET /api/inputs` | Папки задач и состав файлов |
| `POST /api/inputs` | Создание папки задачи; не загрузка файлов |
| `GET /api/inputs/{task}/file` | Предпросмотр файла задачи |
| `GET /api/published` | Список файловых результатов |
| `GET /api/published/file` | Чтение локального результата |
| `GET /api/ui/capabilities` | Реализованные возможности и ограничения движка |
| `GET /api/ui/graphs/{graph_id}/manifest` | Манифест конкретного графа |
| `GET /api/ui/assistants/{assistant_id}/bundle` | UI bundle для выбранного assistant |
| `GET, POST /api/ui/resources/{resource_id}` | Операции разрешённых ресурсных адаптеров |
| `POST /api/ui/actions/validate` | Валидация действия перед передачей в runtime |
| `GET /api/ui/runs/{run_id}/events` | Доступные события адаптера; не гарантия долговременного replay |

Точные параметры и ошибки заданы в [`api.py`](../src/agent/api.py), разрешённые ресурсы — в [`registry.py`](../src/agent/ui_engine/registry.py). Создание тредов, запуск, streaming и resume выполняет стандартный API LangGraph; служебные endpoints его не заменяют.

Capability endpoint сообщает `node_lifecycle=true`: установленный SDK 1.10
поддерживает stream mode `tasks`, из которого frontend получает точные live
`node.started`, `node.completed`, `node.failed` и execution id. При этом
`event_replay=false`: долговременные времена и события после закрытия stream не
гарантированы. `updates` используются как `node.update`, а `values` всегда
сверяет/заменяет вычисленный state.

## Как добавить новый граф без изменения frontend

1. Создать Python graph, например `src/agent/my_graph.py:graph`.
2. Добавить его в `langgraph.json`.
3. Создать `src/agent/ui_engine/graph_manifests/my_graph.py` с обязательными
   `schema_version`, `manifest_version`, `graph_id`, `title`.
4. Добавить manifest в `graph_manifests/__init__.py:MANIFESTS`; `ui_engine/registry.py` регистрирует этот список. При новых ресурсах добавить разрешённые адаптеры в registry.
5. Запустить проверки ниже.

Frontend-файлы менять не нужно: input, topology, nodes, state и timeline
появляются после четырёх backend-шагов выше. Проверяет это не отдельный
демонстрационный граф, а тест `test_every_registered_graph_has_a_manifest`:
каждый граф из `langgraph.json` обязан иметь manifest, и наоборот.

Manifest подписывается под топологией, а не под тем, что бывает у графов
вообще, и это тоже проверка, а не договорённость:
`test_manifest_nodes_match_the_compiled_graph` сверяет имена узлов манифеста
с именами узлов скомпилированного графа, в обе стороны. Состав узлов при этом
не объявляется руками: `pipeline_nodes()` выводит его из описания конвейера —
узел `tools` есть тогда, когда есть роль с `reads_files`, `no_input` — когда
объявлена проверка входа, `source`/`ticket`/`create` — когда объявлены
прелюдия и постлюдия (`pipeline.Stage`). Раньше это приезжало флагами
`admission=True` и `tools_description=None`, то есть теми же фактами, сказанными
второй раз. Что функции для объявленных узлов сборщику действительно передали,
проверяет он сам и падает при сборке, а не показывает пустую коробку на экране.

Поле ввода, список которого зависит от другого поля, объявляет эту связь в
манифесте: `options.depends_on` — id соседнего поля, значение приходит виджету
через `context.inputs`. Так сделано поле источника (`file-picker`): по списку
выбранной папки оно сверяет, что выбранные файлы в ней ещё лежат, и знания о
том, что папка называется `task`, во фронтенде нет. Чем поле наполняется,
говорит `options.kind`: `diagram` — схемы графа `drawio`, `text` — документы
остальных конвейеров; сколько имён поле держит — `options.multiple`.

Само поле каталог не рисует: оно показывает выбранное и `[×]` рядом с каждым
именем. Список файлов уже стоит деревом выше, и второй такой же — не выбор, а
шум, в котором выбранное ничем не выделено.

Обратная сторона той же связи — `context.setInput`: виджет пишет значение
в соседнее поле по его id из манифеста. Ровно один случай, и он про то, что
выбор делается там, где на него смотрят: `task-picker` с `options.document_input`
отдаёт клик по файлу в поле документа, а не открывает файл на просмотр
(просмотр уехал на соседнюю кнопку). Что этому полю годится и сколько имён в
него влезает, сказано там же — `options.document_kind` и
`options.document_multiple`: у схемы draw.io выбор один, у комплекта
документации клик добавляет и убирает. Поля без `document_input` ведут себя как
раньше: клик по файлу открывает документ, потому что класть выбор некуда.

Папка вывода в этом не отличается от папки задачи: её файлы выбираются
источником так же. Готовый документ — конец одного конвейера и вход другого,
и `configurable.input_file` у них общий.

Из полей ввода в `configurable` прогона значения собирает `configurableOf()`
(`engine/manifest/bindings.ts`) — по `input[].target`, без единого зашитого
имени. Пустая строка не уезжает: у полей выбора она значит «не выбрано», и
разницу видит уже граф.

Если manifest не зарегистрирован или имеет неизвестную major-версию, graph всё
равно открывается: title/id, topology, chat/JSON input, JSON state и generic
interrupt доступны в fallback mode; неподтверждённые capabilities скрыты.
Ворота при этом остаются на месте: `fallback_manifest()` на сервере и
`fallbackManifest()` на клиенте объявляют один и тот же минимальный набор
действий — `thread.create`, `run.start`, `run.stop`, `interrupt.resume`, —
и всё, чего в нём нет, отклоняется как `ui_action_forbidden`.

Ответ на остановку адресуется двумя идентификаторами: `interrupt_rule_id` —
это правило из `interrupts[]`, по нему берётся `resume_schema`;
`interrupt_id` — настоящий идентификатор остановки LangGraph, его несёт
`Command(resume={interrupt_id: payload})`. Совпадать они не обязаны и почти
никогда не совпадают. Если правило не подошло и на клиенте, обе стороны
проверяют одно и то же: что ответ вообще объект.

Повторность ответа считает LangGraph, а не интерфейс: адресованный resume
применяется только к своей остановке, поэтому старый ответ не подтверждает
следующую. `/api/ui/actions/validate` ничего не выполняет и потому ничего не
помечает выполненным — после оборванной отправки ту же валидацию можно
повторить. `idempotency_key` остаётся обязательным, но только как сквозной
идентификатор для журнала: по нему запись валидации сходится с запросом,
который породил клик оператора.

Остановку интерфейс берёт из `stream.interrupts` — массива, — а не из
`stream.interrupt`: последний объявлен одной остановкой, но при нескольких
возвращает массив, и `?.id` на нём молча даёт `undefined`. Сейчас адресуется
первая остановка; очередь карточек из раздела 13.2 спецификации не построена.

Ролей у служебного API нет — есть один общий bearer-токен, — поэтому и полей
роли в адаптерах ресурсов нет. Границу держит сам allowlist: чего в нём не
записано, того endpoint не выполнит.

## Версионирование

- `schema_version=1.0` — контракт engine;
- `manifest_version` меняет автор при изменении UI-семантики;
- новая minor schema может добавлять необязательные поля;
- неизвестная major schema отклоняется frontend и включает fallback;
- ETag считается по canonical UTF-8 JSON;
- topology hash не зависит от порядка nodes/edges.

Новый run получает текущий manifest. Полноценный historical manifest/event
store не рекламируется capability до появления серверного durable storage.

## Проверки

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-run
cd web
npm.cmd run test:engine
npm.cmd run build
```

`tests/test_ui_engine.py` покрывает manifests, schema artifacts, compatibility,
registry, topology hash, redaction, event sequence/dedup и границу памяти,
ETag/304, auth, resource allowlist, resume schema, fallback-ворота, границы
preflight и семантику адресованного resume на настоящем графе LangGraph.
Схемы из `schemas/` не лежат мёртвым грузом: отдельный тест сверяет их версию,
паттерны и обязательные поля с обоими валидаторами — python и TypeScript, —
которые контракт на самом деле применяют.

`web/tests/engine.test.ts` покрывает порядок событий, окно нового прогона в том
же треде, устойчивость reconcile к равным снимкам, повтор неудавшейся отправки
ответа, live-адаптер, раскладку input-биндингов по поверхностям и набор
действий fallback mode. Production build проверяет единый TypeScript contract
и создаёт bundle.

`web/tests/browser_regressions.py` поднимает собранный интерфейс в Chromium
поверх фикстур: Markdown без выполнения скриптов, фокус и Escape в модальном
окне, гонка сохранения настроек, отказы публикаций и потеря связи. Модели,
ключей и записи настоящих настроек там нет.

Все три команды стоят в CI (`.github/workflows/ci.yml`): тест, который
запускается только руками, не запускается.

## Upgrade и rollback

Upgrade:

1. Сначала развернуть backend с совместимой minor schema и новыми manifests.
2. Проверить `/api/ui/capabilities` и manifest ETag.
3. Развернуть frontend; открытые runs продолжают со своей загруженной версией.
4. После проверки удалить только действительно замещённые legacy adapters.

Rollback:

1. Вернуть предыдущий frontend build — backend v1 сохраняет совместимые поля.
2. Если откатывается backend, вернуть предыдущие `graph_manifests` и registry
   вместе с Python graph commit.
3. Не удалять threads/checkpoints: UI engine ими не владеет.
4. При несовпадении версий оставить manifest endpoint недоступным — fallback
   безопаснее, чем выдача stale manifest с запрещённым action.

Процесс-local idempotency ledger и bounded event adapter не являются durable
audit/event store. Для multi-process production их следует заменить общим
append-only хранилищем до включения `event_replay`, `historical_manifest` и
полного audit capability.
