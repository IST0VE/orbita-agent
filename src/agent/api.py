"""
Свои HTTP-роуты поверх сервера LangGraph.

Приложение монтируется в тот же процесс через `langgraph.json` → `http.app`,
а не поднимается вторым сервером: второй процесс — это второй порт, второй
CORS, вторая точка отказа и лишняя память ради трёх обработчиков.

Здесь ровно то, чего нет в API LangGraph: настройки из `.env`, файлы чатов,
библиотека примеров и журнал сервера для интерфейса.
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
import contextlib
import json
import logging
import re
from datetime import UTC, datetime

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from agent import (
    chat_files,
    confluence,
    credentials,
    inputs,
    jira_writer,
    logbook,
    metrics,
    pause,
    publishers,
    settings_io,
)
from agent import config as cfg
from agent.auth import delete_thread, owns_thread, thread_exists
from agent.security import ApiSecurityMiddleware, admin_error, auth_error, principal_of
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
# Журнал для страницы «Журнал» подключается здесь, при загрузке роутов: до этой
# точки uvicorn ещё переписывает обработчики корневого логгера (`logbook.install`).
_LOGBOOK = logbook.install()


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
    return auth_error(request.headers, request.scope)


def _admin_error(request: Request) -> JSONResponse | None:
    return _auth_error(request) or admin_error(request.scope)


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
    # Настройки сервера — одни на всех, и значения в них не только секреты:
    # адрес Jira, куда уйдёт токен, правит администратор, а не любой вошедший.
    if denied := _admin_error(request):
        return denied
    try:
        return JSONResponse(await asyncio.to_thread(settings_io.describe))
    except PermissionError as exc:
        # В Compose файл принадлежит хосту: на Linux `up.sh` ставит ему 600, и
        # пользователь контейнера его не читает. Пустая страница молчала бы об этом.
        return _error(f"нет доступа к файлу настроек {exc.filename}: см. docs/DEPLOYMENT.md", 503)


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
    if denied := _admin_error(request):
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
        return JSONResponse(
            {"saved": [], "path": ".env", "restart_required": [], "apply": settings_io.apply_hint()}
        )

    try:
        result = await asyncio.to_thread(settings_io.save, updates, notes)
    except (OSError, ValueError) as exc:
        return _error(f"настройки не сохранены: {exc}", 500)
    # `.env` загружается в окружение процесса один раз. Все изменения требуют
    # перезапуска; прежний ответ только про LLM_/PRICE_ обещал live-reload,
    # которого на самом деле в процессе нет.
    result["restart_required"] = sorted(updates)
    return JSONResponse(result)


async def get_me(request: Request) -> JSONResponse:
    """
    Кто вошёл и что ему можно: интерфейс прячет то, что сервер всё равно не отдаст.
    ---
    """
    if denied := _auth_error(request):
        return denied
    principal = principal_of(request.scope)
    return JSONResponse(
        {
            "subject": principal.subject,
            "name": principal.name,
            "admin": principal.admin,
            "service": principal.service,
        }
    )


# ---------------------------------------------------------------------------
# Мои подключения: личные токены Jira и Confluence (credentials.py)
# ---------------------------------------------------------------------------
_NO_PERSONAL = (
    "админ-токен работает общими токенами Jira и Confluence из .env — "
    "личные подключения есть у пользователей, вошедших через Keycloak"
)


def _personal_subject(request: Request) -> str | JSONResponse:
    if denied := _auth_error(request):
        return denied
    principal = principal_of(request.scope)
    if principal.service:
        return _error(_NO_PERSONAL, 400, error_code="connections_service")
    return principal.subject


async def _connections(subject: str, **extra) -> JSONResponse:
    try:
        described = await asyncio.to_thread(credentials.describe, subject)
    except credentials.CredentialsError as exc:
        return _error(str(exc), 503, error_code="connections_unavailable")
    return JSONResponse({**described, **extra})


async def get_connections(request: Request) -> JSONResponse:
    """
    Мои подключения: задан ли токен, e-mail, куда уйдёт токен и последняя проверка.

    Сам токен не отдаётся никогда — ни маской, ни началом.
    ---
    """
    subject = _personal_subject(request)
    if isinstance(subject, JSONResponse):
        return subject
    return await _connections(subject)


async def put_connections(request: Request) -> JSONResponse:
    """
    Записать личные значения: `{"values": {"JIRA_TOKEN": "...", "JIRA_EMAIL": ""}}`.

    Как у настроек: приходит только тронутое, пустая строка стирает значение.
    ---
    """
    subject = _personal_subject(request)
    if isinstance(subject, JSONResponse):
        return subject
    try:
        body = await _json_body(request)
    except RequestBodyTooLarge as exc:
        return _error(str(exc), 413)
    except ValueError as exc:
        return _error(str(exc))
    values = body.get("values")
    if not isinstance(values, dict):
        return _error("ожидается объект `values` вида {ПЕРЕМЕННАЯ: значение}")
    try:
        saved = await asyncio.to_thread(credentials.save, subject, values)
    except ValueError as exc:
        return _error(str(exc))
    except credentials.CredentialsError as exc:
        return _error(str(exc), 503, error_code="connections_unavailable")
    return await _connections(subject, saved=saved)


def _system(request: Request) -> str | JSONResponse:
    system = request.path_params["system"]
    if system not in credentials.SYSTEMS:
        return _error(f"{system!r}: подключения бывают " + ", ".join(credentials.SYSTEMS), 404)
    return system


async def delete_connection(request: Request) -> JSONResponse:
    """
    Отключить систему: стереть и токен, и e-mail, и прошлую проверку.
    ---
    """
    subject = _personal_subject(request)
    if isinstance(subject, JSONResponse):
        return subject
    system = _system(request)
    if isinstance(system, JSONResponse):
        return system
    try:
        await asyncio.to_thread(credentials.forget, subject, system)
    except credentials.CredentialsError as exc:
        return _error(str(exc), 503, error_code="connections_unavailable")
    return await _connections(subject)


async def check_connection(request: Request) -> JSONResponse:
    """
    Проверить подключение запросом «кто я» (`/myself`, `/user/current`) с личным токеном.

    Отказ самой Jira — не ошибка запроса: он и есть ответ, и приходит в `check`.
    ---
    """
    subject = _personal_subject(request)
    if isinstance(subject, JSONResponse):
        return subject
    system = _system(request)
    if isinstance(system, JSONResponse):
        return system
    try:
        result = await asyncio.to_thread(credentials.check, subject, system)
    except credentials.CredentialsError as exc:
        return _error(str(exc), 503, error_code="connections_unavailable")
    return await _connections(subject, check=result)


def _inputs_snapshot() -> dict:
    return {"tasks": inputs.list_tasks()}


async def get_inputs(request: Request) -> JSONResponse:
    if denied := _auth_error(request):
        return denied
    return JSONResponse(await asyncio.to_thread(_inputs_snapshot))


async def post_inputs(request: Request) -> JSONResponse:
    # Папки задач — общая библиотека примеров: заводит их администратор.
    # Пользователь кладёт файлы в свой чат (`/api/chats/{id}/files`).
    if denied := _admin_error(request):
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
    if inputs.is_chat(task):
        # Файлы чата открываются только своим роутом, где сверяется владелец:
        # здесь имя `@chat/<тред>` открыло бы чужой чат по id треда.
        return _error("файлы чата читаются через /api/chats/{thread_id}/files", 404)
    try:
        # `preview`, а не `read`: оператору показывается и схема .drawio,
        # которую роль получает разобранной и потому не читает как текст.
        text = await asyncio.to_thread(inputs.preview, task, name)
    except inputs.InputError as exc:
        return _error(str(exc), 404)
    return JSONResponse({"task": task, "name": name, "text": text})


def _publish_target() -> str:
    """
    Цель публикации для списка, или `unknown`.

    При `auto` цель зависит от личных настроек Confluence, а они лежат в базе.
    База недоступна — не повод прятать файлы, которые уже лежат на диске.
    """
    try:
        return publishers.resolve()
    except confluence.ConfluenceError:
        return "unknown"


def _published_snapshot() -> dict:
    return {
        "target": _publish_target(),
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


# --------------------------------------------------------------------------
# Файлы чата
#
# Чат — это тред LangGraph, файлы чата — папка этого треда (`chat_files.py`).
# Роуты свои, и правила `auth.py` к ним сервер не применяет, поэтому владелец
# треда сверяется в каждом. Чужой тред отвечает тем же 404, что и
# несуществующий: по ответу нельзя узнать, что тред с таким id есть.
# --------------------------------------------------------------------------
async def _own_chat(request: Request) -> str | JSONResponse:
    """Id треда из пути, если тред есть и принадлежит спросившему."""
    if denied := _auth_error(request):
        return denied
    thread_id = request.path_params["thread_id"]
    try:
        thread_id = inputs.thread_id_of(thread_id)
    except inputs.InputError:
        return _error("чат не найден", 404, error_code="chat_not_found")
    principal = principal_of(request.scope)
    if principal is None or not await owns_thread(principal, thread_id, must_exist=True):
        return _error("чат не найден", 404, error_code="chat_not_found")
    return thread_id


def _storage_error(thread_id: str, action: str, exc: OSError) -> JSONResponse:
    """
    Отказ диска при работе с файлами чата — словами, а не голым 500.

    Чаще всего это права: том `data`, заведённый ещё тогда, когда контейнер
    работал от root, принадлежит root, и процесс `orbita` не может создать
    в нём `/data/chats`. Пользователь это не починит, а администратору нужно
    знать, какая папка и что с ней делать.
    """
    _LOG.error("chat files: %s failed", action, extra={"ui_thread_id": thread_id}, exc_info=True)
    if isinstance(exc, PermissionError):
        where = exc.filename or cfg.chat_files_dir()
        return _error(
            f"{action}: у сервера нет прав на запись в {where}. Это настройка сервера, "
            "а не файла: администратору — раздел «Права доступа» в docs/DEPLOYMENT.md",
            503,
            error_code="chat_storage_forbidden",
        )
    return _error(f"{action}: {exc}", 503, error_code="chat_file_unavailable")


async def get_chat_files(request: Request) -> JSONResponse:
    """
    Файлы чата и ограничения на загрузку.
    ---
    """
    thread_id = await _own_chat(request)
    if isinstance(thread_id, JSONResponse):
        return thread_id
    return JSONResponse(await asyncio.to_thread(chat_files.listing, thread_id))


async def put_chat_file(request: Request) -> JSONResponse:
    """
    Загрузить файл в чат: тело запроса — содержимое, имя — параметр `name`.

    Не multipart: одному файлу на запрос он ничего не добавляет, а разбор
    multipart — это ещё одна зависимость и ещё один разборщик на пути
    непроверенных байтов. Потолок тела — CHAT_FILE_MAX_BYTES, его держит
    `security.ApiSecurityMiddleware` ещё до этого обработчика.
    ---
    """
    thread_id = await _own_chat(request)
    if isinstance(thread_id, JSONResponse):
        return thread_id
    name = request.query_params.get("name", "")
    if not name:
        return _error("не указан параметр `name`", error_code="chat_file_invalid")
    body = await request.body()
    try:
        saved = await asyncio.to_thread(chat_files.save, thread_id, name, body)
    except (chat_files.ChatFileError, inputs.InputError) as exc:
        return _error(str(exc), error_code="chat_file_invalid")
    except OSError as exc:
        return _storage_error(thread_id, "файл не сохранён", exc)
    return JSONResponse({"file": saved, **await asyncio.to_thread(chat_files.listing, thread_id)})


async def get_chat_file(request: Request) -> JSONResponse:
    """
    Содержимое файла чата — для предпросмотра.
    ---
    """
    thread_id = await _own_chat(request)
    if isinstance(thread_id, JSONResponse):
        return thread_id
    name = request.query_params.get("name", "")
    if not name:
        return _error("не указан параметр `name`", error_code="chat_file_invalid")
    try:
        text = await asyncio.to_thread(chat_files.preview, thread_id, name)
    except chat_files.ChatFileError as exc:
        return _error(str(exc), 404, error_code="chat_file_not_found")
    return JSONResponse({"name": name, "text": text})


async def delete_chat_file(request: Request) -> JSONResponse:
    """
    Удалить файл из чата.
    ---
    """
    thread_id = await _own_chat(request)
    if isinstance(thread_id, JSONResponse):
        return thread_id
    name = request.query_params.get("name", "")
    if not name:
        return _error("не указан параметр `name`", error_code="chat_file_invalid")
    try:
        await asyncio.to_thread(chat_files.remove, thread_id, name)
    except chat_files.ChatFileError as exc:
        return _error(str(exc), 404, error_code="chat_file_not_found")
    except OSError as exc:
        return _storage_error(thread_id, "файл не удалён", exc)
    return JSONResponse(await asyncio.to_thread(chat_files.listing, thread_id))


async def attach_chat_file(request: Request) -> JSONResponse:
    """
    Скопировать в чат пример из общей библиотеки или свой опубликованный документ.

    Тело: `{"source": "examples" | "published", "name": ..., "example": ...}`.
    ---
    """
    thread_id = await _own_chat(request)
    if isinstance(thread_id, JSONResponse):
        return thread_id
    try:
        body = await _json_body(request)
    except RequestBodyTooLarge as exc:
        return _error(str(exc), 413)
    except ValueError as exc:
        return _error(str(exc), error_code="chat_file_invalid")
    try:
        saved = await asyncio.to_thread(
            chat_files.attach,
            thread_id,
            str(body.get("source", "")),
            str(body.get("name", "")),
            str(body.get("example", "")),
        )
    except (chat_files.ChatFileError, inputs.InputError) as exc:
        return _error(str(exc), error_code="chat_file_invalid")
    except OSError as exc:
        return _storage_error(thread_id, "файл не скопирован в чат", exc)
    return JSONResponse({"file": saved, **await asyncio.to_thread(chat_files.listing, thread_id)})


async def delete_chat(request: Request) -> JSONResponse:
    """
    Удалить чат: тред вместе с его файлами.

    Удалять тред мимо этого роута можно (SDK, Studio), но тогда папка файлов
    остаётся до уборки (`_sweep_chats`). Интерфейс удаляет здесь, и файлы
    уходят сразу.
    ---
    """
    thread_id = await _own_chat(request)
    if isinstance(thread_id, JSONResponse):
        return thread_id
    await delete_thread(thread_id)
    try:
        removed = await asyncio.to_thread(chat_files.drop, thread_id)
    except OSError as exc:
        # Тред уже удалён; папку без треда подберёт уборка (`sweep_chats`).
        return _storage_error(thread_id, "чат удалён, но его файлы остались на сервере", exc)
    return JSONResponse({"deleted": thread_id, "files_removed": removed})


async def get_library(request: Request) -> JSONResponse:
    """
    Что можно добавить в чат: общие примеры и свои опубликованные документы.
    ---
    """
    if denied := _auth_error(request):
        return denied
    try:
        return JSONResponse(await asyncio.to_thread(chat_files.library))
    except OSError as exc:
        return _error(f"библиотека не прочитана: {exc}", 500)


#: Первая уборка — не на старте: сервер ещё поднимает треды из хранилища.
_SWEEP_FIRST_S = 300
_SWEEP_EVERY_S = 6 * 3600
#: Папку моложе часа не трогаем: тред мог появиться только что.
_SWEEP_GRACE_S = 3600


async def sweep_chats() -> int:
    """
    Удалить папки файлов тех чатов, тредов которых больше нет. Сколько удалено.

    Тред удаляют и мимо `/api/chats/{id}` — через SDK или сбросом хранилища
    `langgraph dev`, — и его файлы остаются на диске без владельца. Удаляется
    только папка, про которую сервер ответил «треда нет»: сбой запроса — не
    повод стирать чужие файлы.
    """
    removed = 0
    for thread_id in await asyncio.to_thread(chat_files.folders, _SWEEP_GRACE_S):
        try:
            if await thread_exists(thread_id):
                continue
        except Exception:
            _LOG.warning("chat sweep: thread lookup failed", extra={"ui_thread_id": thread_id})
            continue
        if await asyncio.to_thread(chat_files.drop, thread_id):
            removed += 1
            _LOG.info("chat sweep: files of a deleted thread removed", extra={"ui_thread_id": thread_id})
    return removed


async def _sweep_forever() -> None:
    await asyncio.sleep(_SWEEP_FIRST_S)
    while True:
        try:
            await sweep_chats()
        except Exception:
            _LOG.exception("chat sweep failed")
        await asyncio.sleep(_SWEEP_EVERY_S)


@contextlib.asynccontextmanager
async def lifespan(_app: Starlette):
    """Фоновая уборка папок удалённых чатов. LangGraph сливает её со своей."""
    task = asyncio.create_task(_sweep_forever())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def get_logs(request: Request) -> JSONResponse:
    """
    Журнал сервера: последние записи `logging` и сведения о процессе.

    Ради этого роута оператору больше не нужен доступ к `docker compose logs`,
    чтобы узнать, почему упал прогон: трассировка узла, повтор запроса к шлюзу
    модели и отказ интеграции видны на странице «Журнал» и уходят в отчёт.

    `after` — последний `next`, который клиент уже получил: опрос забирает
    только новое. `level` — нижняя граница важности, `thread_id` — записи
    одного треда. Секреты вычищены ещё при записи (`logbook.scrub`).

    Только администратору: в журнале трассировки всех прогонов, а значит и
    куски чужих задач.
    ---
    """
    if denied := _admin_error(request):
        return denied
    params = request.query_params
    try:
        after = max(0, int(params.get("after", "0")))
        limit = max(1, min(int(params.get("limit", "500")), 2000))
    except ValueError:
        return _error("after и limit должны быть целыми числами")
    level = params.get("level", "info").lower()
    if level not in logbook.LEVELS:
        return _error("level: одно из " + ", ".join(logbook.LEVELS))
    snapshot = _LOGBOOK.snapshot(
        after=after,
        level=logbook.LEVELS[level],
        thread_id=params.get("thread_id", "")[:200],
        limit=limit,
    )
    return JSONResponse({**snapshot, "server": await asyncio.to_thread(logbook.server_info)})


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
        if inputs.is_chat(task):
            return _error("chat files are served by /api/chats", 404, error_code="ui_resource_not_found")
        try:
            text = await asyncio.to_thread(inputs.preview, task, name)
        except inputs.InputError as exc:
            return _error(str(exc), 404, error_code="ui_resource_not_found")
        return JSONResponse({"task": task, "name": name, "text": text})
    if resource_id == "orbita.tasks" and operation == "create":
        if request.method != "POST":
            return _error("resource operation requires POST", 405, error_code="ui_resource_method")
        if denied := admin_error(request.scope):
            return denied
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
        try:
            absent = await asyncio.to_thread(jira_writer.missing_vars)
        except jira_writer.JiraError as exc:
            return _error(str(exc), 503, error_code="ui_resource_unavailable")
        if absent:
            return JSONResponse(
                {
                    "projects": [],
                    "reason": credentials.missing_message(absent),
                    "default": "",
                }
            )
        try:
            found = await asyncio.to_thread(jira_writer.projects)
        except jira_writer.JiraError as exc:
            return _error(str(exc), 502, error_code="ui_resource_unavailable")
        try:
            default = await asyncio.to_thread(jira_writer.jira.default_project)
        except jira_writer.JiraError as exc:
            return _error(str(exc), 503, error_code="ui_resource_unavailable")
        return JSONResponse({"projects": found, "default": default})
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


async def ui_pause(request: Request) -> JSONResponse:
    """
    Заявка на паузу: остановить идущий конвейер на ближайшей границе шага.

    Единственный роут, который вмешивается в идущий прогон, — и он не трогает
    ни граф, ни состояние треда. Он оставляет заявку на доске (`pause.py`), а
    остановку берёт сам узел перед следующим обращением к модели. Писать в
    состояние треда снаружи, пока по нему идут узлы, значило бы гонку
    с чекпоинтером; читать доску узлу — обычная проверка перед тратой денег.

    POST — попросить паузу, DELETE — передумать, GET — узнать, ждёт ли тред.
    Ответ у всех трёх одинаковый: заявка либо есть, либо нет.
    ---
    """
    if denied := _auth_error(request):
        return denied
    if request.method == "GET":
        thread_id = request.query_params.get("thread_id", "")
    else:
        try:
            body = await _json_body(request)
        except RequestBodyTooLarge as exc:
            return _error(str(exc), 413, error_code="ui_action_too_large")
        except ValueError as exc:
            return _error(str(exc), error_code="ui_action_invalid")
        thread_id = str(body.get("thread_id", ""))
    if not thread_id:
        return _error(
            "thread_id is required",
            400,
            error_code="ui_pause_context_missing",
        )
    # Роут свой, и фильтры LangGraph его не касаются: без этой сверки пауза
    # останавливала бы и чужой прогон, стоило узнать id треда.
    if not await owns_thread(principal_of(request.scope), thread_id):
        return _error("тред не найден", 404, error_code="ui_pause_not_found")
    if request.method == "GET":
        return JSONResponse(pause.board.status(thread_id))
    if request.method == "DELETE":
        return JSONResponse(pause.board.cancel(thread_id))
    try:
        status = pause.board.request(thread_id)
    except ValueError as exc:
        return _error(str(exc), 400, error_code="ui_pause_invalid")
    _LOG.info("ui pause requested", extra={"ui_thread_id": thread_id})
    return JSONResponse(status)


async def get_ui_events(request: Request) -> JSONResponse:
    """Bounded replay window for events emitted through the optional adapter.

    События адресуются прогоном, а не тредом, и владельца у них не проверить:
    поэтому окно открыто только администратору.
    """
    if denied := _admin_error(request):
        return denied
    run_id = request.path_params["run_id"]
    try:
        after = max(0, int(request.query_params.get("after", "0")))
        limit = max(1, min(int(request.query_params.get("limit", "200")), 1000))
    except ValueError:
        return _error("after and limit must be integers", error_code="ui_events_invalid_query")
    result = events.replay(run_id, after=after, limit=limit)
    return JSONResponse({"events": result, "next_sequence": result[-1]["sequence"] if result else after})


routes = [
    Route("/api/me", get_me, methods=["GET"]),
    Route("/api/me/connections", get_connections, methods=["GET"]),
    Route("/api/me/connections", put_connections, methods=["PUT"]),
    Route("/api/me/connections/{system}", delete_connection, methods=["DELETE"]),
    Route("/api/me/connections/{system}/check", check_connection, methods=["POST"]),
    Route("/api/settings", get_settings, methods=["GET"]),
    Route("/api/settings", put_settings, methods=["PUT"]),
    Route("/api/inputs", get_inputs, methods=["GET"]),
    Route("/api/inputs", post_inputs, methods=["POST"]),
    Route("/api/inputs/{task}/file", get_input_file, methods=["GET"]),
    Route("/api/chats/{thread_id}", delete_chat, methods=["DELETE"]),
    Route("/api/chats/{thread_id}/files", get_chat_files, methods=["GET"]),
    Route("/api/chats/{thread_id}/files", put_chat_file, methods=["PUT"]),
    Route("/api/chats/{thread_id}/files", delete_chat_file, methods=["DELETE"]),
    Route("/api/chats/{thread_id}/files/content", get_chat_file, methods=["GET"]),
    Route("/api/chats/{thread_id}/attach", attach_chat_file, methods=["POST"]),
    Route("/api/library", get_library, methods=["GET"]),
    Route("/api/published", get_published, methods=["GET"]),
    Route("/api/published/file", get_published_file, methods=["GET"]),
    Route("/api/logs", get_logs, methods=["GET"]),
    Route("/api/ui/capabilities", get_ui_capabilities, methods=["GET"]),
    Route("/api/ui/graphs/{graph_id}/manifest", get_ui_manifest, methods=["GET"]),
    Route("/api/ui/assistants/{assistant_id}/bundle", get_ui_bundle, methods=["GET"]),
    Route("/api/ui/resources/{resource_id}", get_ui_resource, methods=["GET", "POST"]),
    Route("/api/ui/actions/validate", validate_ui_action, methods=["POST"]),
    Route("/api/ui/pause", ui_pause, methods=["GET", "POST", "DELETE"]),
    Route("/api/ui/runs/{run_id}/events", get_ui_events, methods=["GET"]),
]

# Middleware отсюда LangGraph ставит на весь сервер, первым — внешний. Метрики
# снаружи проверки входа: отказы 401 и 403 тоже запросы, и их всплеск важен.
app = Starlette(
    middleware=[
        Middleware(metrics.HttpMetricsMiddleware, routes=routes),
        Middleware(ApiSecurityMiddleware),
    ],
    routes=routes,
    lifespan=lifespan,
)
