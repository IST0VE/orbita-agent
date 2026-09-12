"""
Свои HTTP-роуты поверх сервера LangGraph.

Приложение монтируется в тот же процесс через `langgraph.json` → `http.app`,
а не поднимается вторым сервером: второй процесс — это второй порт, второй
CORS, вторая точка отказа и лишняя память ради трёх обработчиков.

Здесь ровно то, чего нет в API LangGraph: настройки из `.env` и файлы задач.
Всё, что касается графа, тредов и прогонов, остаётся у самого сервера —
дублировать его роуты незачем.

Роуты живут под `/api/*`, чтобы не пересекаться с `/assistants`, `/threads`,
`/runs` и `/info` — пользовательские маршруты стоят в цепочке раньше
встроенных и молча перекрыли бы их.

Докстроки обработчиков заканчиваются строкой `---`. Это разделитель Starlette:
всё после него он разбирает как YAML для OpenAPI, всё до — считает обычным
текстом. Без разделителя под YAML уходит весь текст целиком, и русская проза
с двоеточиями роняет разбор схемы при старте сервера.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from agent import config as cfg
from agent import inputs, jira_writer, publishers, settings_io
from agent.security import ApiSecurityMiddleware, auth_error
from agent.ui_engine.capabilities import capabilities
from agent.ui_engine.events import events
from agent.ui_engine.forms import FormValidationError, validate_value
from agent.ui_engine.idempotency import IdempotencyConflict, actions
from agent.ui_engine.manifests import content_etag, fallback_manifest
from agent.ui_engine.registry import ManifestNotFound, registry

# Значение, целиком состоящее из маски: браузер вернул то, что мы ему сами
# показали. Записывать это в `.env` — значит стереть настоящий ключ.
_MASKED = re.compile(r"^.{0,8}\*{4,}$")
_LOG = logging.getLogger(__name__)


class RequestBodyTooLarge(ValueError):
    """The request exceeded the configured administrative API limit."""


def _error(
    message: str,
    status: int = 400,
    *,
    headers: dict | None = None,
    error_code: str | None = None,
) -> JSONResponse:
    payload = {"error": message}
    if error_code:
        payload["error_code"] = error_code
    return JSONResponse(payload, status_code=status, headers=headers)


def _auth_error(request: Request) -> JSONResponse | None:
    return auth_error(request.headers)


async def _json_body(request: Request) -> dict:
    """JSON-объект с ограничением размера, включая запросы без Content-Length."""
    limit = cfg.api_max_request_bytes()
    declared = request.headers.get("content-length")
    if declared:
        try:
            declared_size = int(declared)
        except ValueError as exc:
            raise ValueError("некорректный Content-Length") from exc
        if declared_size < 0:
            raise ValueError("некорректный Content-Length")
        if declared_size > limit:
            raise RequestBodyTooLarge(f"тело запроса больше {limit} байт")

    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise RequestBodyTooLarge(f"тело запроса больше {limit} байт")
        chunks.append(chunk)
    try:
        body = json.loads(b"".join(chunks))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("тело запроса не разбирается как JSON") from exc
    if not isinstance(body, dict):
        raise ValueError("тело запроса должно быть JSON-объектом")
    return body


# Чтение `.env` и обход папки задач — это блокирующий ввод-вывод, а обработчики
# здесь асинхронные: сервер один на все прогоны, и остановленный на файловой
# операции цикл событий подвешивает вместе с настройками ещё и чужие ходы.
# Поэтому вся работа с диском уезжает в поток.
async def get_settings(request: Request) -> JSONResponse:
    if denied := _auth_error(request):
        return denied
    return JSONResponse(await asyncio.to_thread(settings_io.describe))


def _env_name_error(name: object) -> str | None:
    """
    Почему это имя нельзя записать в `.env`.

    Форма имени и запрет — разные отказы: первое означает опечатку, второе —
    что переменная меняет поведение процесса, а не агента, и правится только
    руками на сервере.
    """
    if not isinstance(name, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", name):
        return f"{name!r}: имя переменной окружения так не выглядит"
    if not settings_io.can_edit(name):
        return f"{name}: имя меняет поведение процесса и правится только на сервере"
    return None


async def put_settings(request: Request) -> JSONResponse:
    """
    Записать присланные значения в `.env`.

    Приходит только то, что оператор действительно правил: нетронутое поле
    интерфейс не присылает вообще. Поэтому пустая строка здесь означает
    «стереть значение», а не «оператор не заполнил».

    Кроме значений принимаются комментарии: `{"comments": {ИМЯ: текст}}`.
    Имя, которого в `.env` ещё нет, заводится новой строкой — интерфейс умеет
    добавлять переменные, а не только менять заготовленные.

    Маска до файла не доезжает ни при какой ошибке на фронте: значение,
    похожее на неё, отбивается здесь.
    ---
    """
    if denied := _auth_error(request):
        return denied
    try:
        body = await _json_body(request)
    except RequestBodyTooLarge as exc:
        return _error(str(exc), 413)
    except ValueError as exc:
        return _error(str(exc))

    # Оба поля необязательны по отдельности: подписать переменную, не трогая
    # значения, — такая же законная правка, как и наоборот.
    values = body.get("values") if body.get("values") is not None else {}
    if not isinstance(values, dict):
        return _error("ожидается объект `values` вида {ПЕРЕМЕННАЯ: значение}")
    comments = body.get("comments") if body.get("comments") is not None else {}
    if not isinstance(comments, dict):
        return _error("ожидается объект `comments` вида {ПЕРЕМЕННАЯ: текст}")

    updates: dict[str, str] = {}
    for name, value in values.items():
        if refusal := _env_name_error(name):
            return _error(refusal)
        text = "" if value is None else str(value).strip()
        if "\n" in text or "\r" in text:
            return _error(f"{name}: перенос строки в значении сломал бы файл")
        if settings_io.is_secret(name) and text and _MASKED.match(text):
            return _error(f"{name}: пришла маска вместо значения — поле не сохранено")
        updates[name] = text

    notes: dict[str, str] = {}
    for name, text in comments.items():
        if refusal := _env_name_error(name):
            return _error(refusal)
        notes[name] = "" if text is None else str(text)

    if not updates and not notes:
        return JSONResponse({"saved": [], "path": ".env", "restart_required": []})

    try:
        result = await asyncio.to_thread(settings_io.save, updates, notes)
    except (OSError, ValueError) as exc:
        return _error(f"настройки не сохранены: {exc}", 500)
    # `.env` загружается в окружение процесса один раз. Все изменения требуют
    # перезапуска; прежний ответ только про LLM_/PRICE_ обещал live-reload,
    # которого на самом деле в процессе нет.
    result["restart_required"] = sorted(updates)
    return JSONResponse(result)


def _inputs_snapshot() -> dict:
    return {"tasks": inputs.list_tasks()}


async def get_inputs(request: Request) -> JSONResponse:
    if denied := _auth_error(request):
        return denied
    return JSONResponse(await asyncio.to_thread(_inputs_snapshot))


async def post_inputs(request: Request) -> JSONResponse:
    if denied := _auth_error(request):
        return denied
    try:
        body = await _json_body(request)
    except RequestBodyTooLarge as exc:
        return _error(str(exc), 413)
    except ValueError as exc:
        return _error(str(exc))
    try:
        created = await asyncio.to_thread(inputs.create_task, str(body.get("name", "")))
    except (inputs.InputError, OSError) as exc:
        return _error(str(exc))
    return JSONResponse(created)


async def get_input_file(request: Request) -> JSONResponse:
    """
    Содержимое файла задачи — для предпросмотра в интерфейсе.
    ---
    """
    if denied := _auth_error(request):
        return denied
    task = request.path_params["task"]
    name = request.query_params.get("name", "")
    if not name:
        return _error("не указан параметр `name`")
    try:
        # `preview`, а не `read`: оператору показывается и схема .drawio,
        # которую роль получает разобранной и потому не читает как текст.
        text = await asyncio.to_thread(inputs.preview, task, name)
    except inputs.InputError as exc:
        return _error(str(exc), 404)
    return JSONResponse({"task": task, "name": name, "text": text})


def _published_snapshot() -> dict:
    return {
        "target": publishers.resolve(),
        # Через прямые слэши: путь уезжает в интерфейс как текст, и разбирать
        # его там по разделителю, который зависит от системы сервера, незачем.
        "dir": publishers.directory().as_posix(),
        "documents": publishers.documents(),
    }


async def get_published(request: Request) -> JSONResponse:
    """
    Что уже опубликовано файловой целью.

    Роут нужен из-за адреса: страница на диске приезжает в состояние треда
    как `file://`, а такую ссылку вкладка браузера не открывает. Confluence
    отдаёт настоящий https и в этом списке не нуждается — поле `target`
    говорит интерфейсу, что показывать.
    ---
    """
    if denied := _auth_error(request):
        return denied
    return JSONResponse(await asyncio.to_thread(_published_snapshot))


async def get_published_file(request: Request) -> JSONResponse:
    """
    Содержимое опубликованного документа — для предпросмотра в интерфейсе.
    ---
    """
    if denied := _auth_error(request):
        return denied
    name = request.query_params.get("name", "")
    if not name:
        return _error("не указан параметр `name`")
    try:
        text = await asyncio.to_thread(publishers.read_document, name)
    except publishers.DocumentError as exc:
        return _error(str(exc), 404)
    except OSError as exc:
        return _error(f"документ не прочитан: {exc}", 500)
    return JSONResponse({"name": name, "text": text})


# ---------------------------------------------------------------------------
# Declarative UI engine
# ---------------------------------------------------------------------------


async def get_ui_capabilities(request: Request) -> JSONResponse:
    """Return feature flags and hard limits instead of package-version guesses."""
    if denied := _auth_error(request):
        return denied
    payload = capabilities()
    etag = content_etag(payload)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
    return JSONResponse(payload, headers={"ETag": etag, "Cache-Control": "no-cache"})


async def get_ui_manifest(request: Request) -> JSONResponse:
    """Resolve a validated, versioned manifest registered beside a graph."""
    if denied := _auth_error(request):
        return denied
    graph_id = request.path_params["graph_id"]
    try:
        resolved = registry.resolve(graph_id)
    except ManifestNotFound:
        return _error("UI manifest not found", 404, error_code="ui_manifest_not_found")
    if request.headers.get("if-none-match") == resolved.etag:
        return Response(
            status_code=304,
            headers={
                "ETag": resolved.etag,
                "Cache-Control": "no-cache",
                "X-UI-Schema-Version": resolved.value["schema_version"],
            },
        )
    headers = {
        "ETag": resolved.etag,
        "Cache-Control": "no-cache",
        "X-UI-Schema-Version": resolved.value["schema_version"],
    }
    if resolved.warnings:
        headers["Warning"] = f'299 Orbita "{resolved.warnings[0]}"'
    return JSONResponse(resolved.value, headers=headers)


async def get_ui_bundle(request: Request) -> JSONResponse:
    """Return a consistent assistant/manifest pair and a versioned topology URL.

    The LangGraph server owns assistant UUID -> graph mappings.  The catalog
    already supplies ``graph_id`` to the browser, which sends it as a query
    parameter; graph-id assistants used by tests/dev also work without it.
    """
    if denied := _auth_error(request):
        return denied
    assistant_id = request.path_params["assistant_id"]
    graph_id = request.query_params.get("graph_id") or assistant_id
    try:
        resolved = registry.resolve(graph_id)
    except ManifestNotFound:
        return _error("UI manifest not found", 404, error_code="ui_manifest_not_found")
    topology_url = f"/assistants/{assistant_id}/graph"
    payload = {
        "assistant": {"assistant_id": assistant_id, "graph_id": graph_id},
        "topology": {"url": topology_url},
        "manifest": resolved.value,
        "manifest_etag": resolved.etag.strip('"'),
        "etag": "",
        "generated_at": datetime.now(UTC).isoformat(),
    }
    payload["etag"] = content_etag(
        {
            "assistant": payload["assistant"],
            "topology": payload["topology"],
            "manifest_etag": payload["manifest_etag"],
        }
    ).strip('"')
    etag = f'"{payload["etag"]}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
    return JSONResponse(payload, headers={"ETag": etag, "Cache-Control": "no-cache"})


async def get_ui_resource(request: Request) -> JSONResponse:
    """Execute a compile-time registered resource adapter."""
    if denied := _auth_error(request):
        return denied
    resource_id = request.path_params["resource_id"]
    operation = request.query_params.get("operation", "list")
    try:
        registry.resource(resource_id, operation)
    except ManifestNotFound:
        return _error("resource operation not found", 404, error_code="ui_resource_not_found")
    if resource_id == "orbita.tasks" and operation == "list":
        return JSONResponse(await asyncio.to_thread(_inputs_snapshot))
    if resource_id == "orbita.tasks" and operation == "read":
        task = request.query_params.get("task", "")
        name = request.query_params.get("name", "")
        if not task or not name:
            return _error("task and name are required", error_code="ui_resource_invalid")
        try:
            text = await asyncio.to_thread(inputs.preview, task, name)
        except inputs.InputError as exc:
            return _error(str(exc), 404, error_code="ui_resource_not_found")
        return JSONResponse({"task": task, "name": name, "text": text})
    if resource_id == "orbita.tasks" and operation == "create":
        if request.method != "POST":
            return _error("resource operation requires POST", 405, error_code="ui_resource_method")
        try:
            body = await _json_body(request)
        except RequestBodyTooLarge as exc:
            return _error(str(exc), 413, error_code="ui_resource_too_large")
        except ValueError as exc:
            return _error(str(exc), error_code="ui_resource_invalid")
        key = str(body.get("idempotency_key", ""))
        if not key:
            return _error(
                "idempotency_key is required",
                400,
                error_code="ui_idempotency_missing",
            )
        try:
            fresh = actions.register(
                f"resource:orbita.tasks:create:{key}",
                {"name": body.get("name")},
            )
        except IdempotencyConflict as exc:
            return _error(str(exc), 409, error_code="ui_idempotency_conflict")
        if not fresh:
            return JSONResponse({"duplicate": True, **await asyncio.to_thread(_inputs_snapshot)})
        try:
            created = await asyncio.to_thread(inputs.create_task, str(body.get("name", "")))
        except (inputs.InputError, OSError) as exc:
            return _error(str(exc), error_code="ui_resource_invalid")
        return JSONResponse({"duplicate": False, "created": created})
    if resource_id == "orbita.publications" and operation == "list":
        return JSONResponse(await asyncio.to_thread(_published_snapshot))
    if resource_id == "orbita.publications" and operation == "read":
        name = request.query_params.get("name", "")
        if not name:
            return _error("name is required", error_code="ui_resource_invalid")
        try:
            text = await asyncio.to_thread(publishers.read_document, name)
        except publishers.DocumentError as exc:
            return _error(str(exc), 404, error_code="ui_resource_not_found")
        except OSError:
            return _error(
                "document could not be read",
                500,
                error_code="ui_resource_unavailable",
            )
        return JSONResponse({"name": name, "text": text})
    if resource_id == "orbita.jira" and operation == "projects":
        # Ненастроенная Jira — не ошибка запроса: интерфейс показывает поле
        # ключа проекта и подсказку, а не красный экран. Ошибкой отвечает
        # только сам трекер, и тогда её видно как есть.
        absent = await asyncio.to_thread(jira_writer.missing_vars)
        if absent:
            return JSONResponse(
                {
                    "projects": [],
                    "reason": "не заданы " + ", ".join(absent),
                    "default": "",
                }
            )
        try:
            found = await asyncio.to_thread(jira_writer.projects)
        except jira_writer.JiraError as exc:
            return _error(str(exc), 502, error_code="ui_resource_unavailable")
        return JSONResponse(
            {"projects": found, "default": await asyncio.to_thread(cfg.jira_project_key)}
        )
    return _error("resource operation not implemented", 501, error_code="ui_resource_unavailable")


def _resume_schema(manifest: dict, rule_id: str) -> dict | None:
    """
    Схема ответа на остановку.

    Правило приходит своим идентификатором из `interrupts[]`, а не рантайм-id
    остановки: второй у каждой остановки свой и ни с чем в манифесте не
    совпадёт. Пустой идентификатор — это универсальная форма: правило под эту
    остановку не подошло и на клиенте, поэтому проверяется только то, что
    ответ вообще объект.
    """
    if not rule_id:
        return {"type": "object"}
    rule = next(
        (item for item in manifest.get("interrupts", []) if item.get("id") == rule_id),
        None,
    )
    return None if rule is None else rule.get("resume_schema")


async def validate_ui_action(request: Request) -> JSONResponse:
    """Validate an action before SDK dispatch, without consuming its execution."""
    if denied := _auth_error(request):
        return denied
    try:
        body = await _json_body(request)
    except RequestBodyTooLarge as exc:
        return _error(str(exc), 413, error_code="ui_action_too_large")
    except ValueError as exc:
        return _error(str(exc), error_code="ui_action_invalid")

    graph_id = str(body.get("graph_id", ""))
    kind = str(body.get("kind", ""))
    key = str(body.get("idempotency_key", ""))
    if not key:
        return _error(
            "idempotency_key is required",
            400,
            error_code="ui_idempotency_missing",
        )
    if len(key) > 200:
        return _error("invalid idempotency key", 400, error_code="ui_action_invalid")
    try:
        manifest = registry.resolve(graph_id).value
    except ManifestNotFound:
        # Граф без манифеста открыт в fallback mode, а не закрыт: ворота
        # остаются, но с минимальным набором действий.
        manifest = fallback_manifest(graph_id)
    rule = next(
        (action for action in manifest.get("actions", []) if action.get("kind") == kind),
        None,
    )
    if rule is None:
        return _error("action is not allowed by manifest", 403, error_code="ui_action_forbidden")
    schema = rule.get("input_schema")
    error_code = "ui_action_invalid"
    if kind == "interrupt.resume":
        interrupt_id = str(body.get("interrupt_id", ""))
        thread_id = str(body.get("thread_id", ""))
        if not thread_id or not interrupt_id:
            return _error(
                "thread_id and interrupt_id are required for interrupt resume",
                400,
                error_code="ui_interrupt_context_missing",
            )
        schema = _resume_schema(manifest, str(body.get("interrupt_rule_id", "")))
        if schema is None:
            return _error("interrupt rule not found", 400, error_code="ui_interrupt_unknown")
        error_code = "ui_resume_invalid"
    if isinstance(schema, dict):
        try:
            validate_value(body.get("payload"), schema)
        except FormValidationError as exc:
            return _error(str(exc), 422, error_code=error_code)
    # Validation cannot prove that the following SDK request was executed.
    # Consuming a key here would permanently block retries after a lost request.
    # Resume commands address the actual LangGraph interrupt ID; execution and
    # stale-answer handling belong to LangGraph, not this preflight endpoint.
    # The key therefore stays required but purely as an audit correlation id:
    # it ties this record to the SDK request the operator's click produced.
    _LOG.info(
        "ui action validated",
        extra={
            "ui_graph_id": graph_id,
            "ui_action_kind": kind,
            "ui_idempotency_key": key,
        },
    )
    return JSONResponse({"valid": True, "idempotency_key": key})


async def get_ui_events(request: Request) -> JSONResponse:
    """Bounded replay window for events emitted through the optional adapter."""
    if denied := _auth_error(request):
        return denied
    run_id = request.path_params["run_id"]
    try:
        after = max(0, int(request.query_params.get("after", "0")))
        limit = max(1, min(int(request.query_params.get("limit", "200")), 1000))
    except ValueError:
        return _error("after and limit must be integers", error_code="ui_events_invalid_query")
    result = events.replay(run_id, after=after, limit=limit)
    return JSONResponse({"events": result, "next_sequence": result[-1]["sequence"] if result else after})


app = Starlette(
    middleware=[Middleware(ApiSecurityMiddleware)],
    routes=[
        Route("/api/settings", get_settings, methods=["GET"]),
        Route("/api/settings", put_settings, methods=["PUT"]),
        Route("/api/inputs", get_inputs, methods=["GET"]),
        Route("/api/inputs", post_inputs, methods=["POST"]),
        Route("/api/inputs/{task}/file", get_input_file, methods=["GET"]),
        Route("/api/published", get_published, methods=["GET"]),
        Route("/api/published/file", get_published_file, methods=["GET"]),
        Route("/api/ui/capabilities", get_ui_capabilities, methods=["GET"]),
        Route("/api/ui/graphs/{graph_id}/manifest", get_ui_manifest, methods=["GET"]),
        Route("/api/ui/assistants/{assistant_id}/bundle", get_ui_bundle, methods=["GET"]),
        Route("/api/ui/resources/{resource_id}", get_ui_resource, methods=["GET", "POST"]),
        Route("/api/ui/actions/validate", validate_ui_action, methods=["POST"]),
        Route("/api/ui/runs/{run_id}/events", get_ui_events, methods=["GET"]),
    ]
)
