"""
Кому что видно во встроенном API LangGraph: тред принадлежит тому, кто его создал.

Кто прислал запрос, решает `security.ApiSecurityMiddleware`: он стоит в цепочке
раньше (`middleware_order: middleware_first`), проверяет админ-токен или токен
Keycloak и оставляет пользователя в scope. Здесь подпись второй раз не
проверяется — пользователь берётся оттуда и отдаётся серверу LangGraph, а тот
кладёт его в конфиг каждого прогона (`langgraph_auth_user_id`). По нему
`credentials` находит личный токен Jira того, кто запустил прогон.

Правила — фильтры по метаданным треда, и применяет их сам сервер: чужой тред
не найдётся ни поиском, ни по прямому id, ни запуском прогона в нём. Интерфейс
здесь ничего не прячет — он просто не получает чужого.

Запрет по умолчанию: пара «ресурс, действие» без своего правила пропускалась
бы сервером без проверки, и новый ресурс в следующей версии LangGraph открылся
бы всем молча. Поэтому общий обработчик отказывает, а разрешения перечислены.

Админ-токен (`service`) видит всё: им живут healthcheck, скрипты и интерфейс
без OIDC. Роль администратора Keycloak — нет: она про настройки сервера, а
чужие треды остаются чужими и для неё.
"""

from __future__ import annotations

import httpx
from langgraph_sdk import Auth, get_client

from agent import config as cfg
from agent.security import PRINCIPAL, Principal

auth = Auth()

#: Ключ метаданных треда с `sub` владельца.
OWNER = "owner"
_SERVICE = "service"
_ADMIN = "admin"


@auth.authenticate
async def authenticate(scope: dict) -> Auth.types.MinimalUserDict:
    principal = scope.get(PRINCIPAL)
    if not isinstance(principal, Principal):
        # Сюда запрос без пользователя не доходит: middleware отказал раньше.
        # Если дошёл — цепочку собрали иначе, и пускать молча нельзя.
        raise Auth.exceptions.HTTPException(status_code=401, detail="API требует вход")
    permissions = [_SERVICE] if principal.service else []
    if principal.admin:
        permissions.append(_ADMIN)
    return {
        "identity": principal.subject,
        "display_name": principal.name,
        "permissions": permissions,
    }


def _service(ctx: Auth.types.AuthContext) -> bool:
    return _SERVICE in ctx.permissions


def _own(ctx: Auth.types.AuthContext) -> dict[str, str] | None:
    """Фильтр «только свои»; админ-токену — без фильтра."""
    return None if _service(ctx) else {OWNER: ctx.user.identity}


def _stamp(ctx: Auth.types.AuthContext, value: dict) -> None:
    """Владелец в метаданных — всегда тот, кто прислал запрос, а не то, что он прислал."""
    metadata = value.get("metadata")
    if not isinstance(metadata, dict):
        metadata = value["metadata"] = {}
    metadata[OWNER] = ctx.user.identity


@auth.on
async def deny_by_default(ctx: Auth.types.AuthContext, value: dict) -> bool:
    return _service(ctx)


@auth.on.threads.create
async def create_thread(ctx: Auth.types.AuthContext, value: dict) -> dict | None:
    _stamp(ctx, value)
    # Фильтр и здесь: `if_exists=do_nothing` с id чужого треда вернул бы его.
    return _own(ctx)


@auth.on.threads.create_run
async def run_in_thread(ctx: Auth.types.AuthContext, value: dict) -> dict | None:
    # Метаданные прогона становятся метаданными треда, если тред создаётся
    # этим же запуском (`if_not_exists=create`).
    _stamp(ctx, value)
    return _own(ctx)


@auth.on.threads.update
async def update_thread(ctx: Auth.types.AuthContext, value: dict) -> dict | None:
    # Сменить владельца правкой метаданных нельзя: ключ переписывается.
    if isinstance(value.get("metadata"), dict) and not _service(ctx):
        value["metadata"][OWNER] = ctx.user.identity
    return _own(ctx)


@auth.on.threads.read
@auth.on.threads.search
@auth.on.threads.delete
async def own_threads(ctx: Auth.types.AuthContext, value: dict) -> dict | None:
    return _own(ctx)


@auth.on.assistants.read
@auth.on.assistants.search
async def read_assistants(ctx: Auth.types.AuthContext, value: dict) -> bool:
    # Ассистенты — это графы из langgraph.json, общие для всех.
    return True


@auth.on.assistants.create
@auth.on.assistants.update
@auth.on.assistants.delete
async def manage_assistants(ctx: Auth.types.AuthContext, value: dict) -> bool:
    return _service(ctx) or _ADMIN in ctx.permissions


# Кроны и store — только админ-токену: интерфейс ими не пользуется, а в store
# лежит долгая память агента по всем пользователям сразу. Отдельные правила не
# нужны: их закрывает запрет по умолчанию выше.


def _service_client():
    """Внутрипроцессный клиент сервера LangGraph с правами админ-токена."""
    return get_client(
        url=None, api_key=None, headers={"Authorization": f"Bearer {cfg.api_admin_token()}"}
    )


async def _thread(thread_id: str) -> dict | None:
    """Тред по id или None, если его нет."""
    try:
        return await _service_client().threads.get(thread_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (404, 422):
            return None
        raise


async def owns_thread(principal: Principal, thread_id: str, *, must_exist: bool = False) -> bool:
    """
    Тред этого пользователя — для своих роутов, которые принимают `thread_id`.

    Правила выше применяет сервер LangGraph к своим роутам, а `/api/*` он не
    проверяет. Поэтому тред читается у него же, внутрипроцессным клиентом
    админ-токеном, и владелец сверяется здесь.

    `must_exist` — и админ-токену тред обязан существовать. Так проверяют
    файлы чата: они лежат в папке треда, и загрузка в тред, которого нет,
    оставила бы папку, которую никто не откроет.
    """
    if principal.service and not must_exist:
        return True
    thread = await _thread(thread_id)
    if thread is None:
        return False
    if principal.service:
        return True
    return (thread.get("metadata") or {}).get(OWNER) == principal.subject


async def thread_record(thread_id: str) -> dict | None:
    """Тред с метаданными и последним состоянием (`values`) — для снимка при оценке."""
    return await _thread(thread_id)


async def thread_exists(thread_id: str) -> bool:
    """Есть ли тред вообще — для уборки папок удалённых чатов."""
    return await _thread(thread_id) is not None


async def delete_thread(thread_id: str) -> None:
    """Удалить тред. Владельца проверяет вызывающий: здесь права админ-токена."""
    try:
        await _service_client().threads.delete(thread_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
