"""Fail-closed access control shared by custom and built-in API routes."""

from __future__ import annotations

import asyncio
import re
import secrets
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import parse_qs

import jwt
import requests
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from agent import config as cfg

#: Ключ scope, под которым middleware оставляет проверенного пользователя:
#: роуты не проверяют подпись второй раз и не ходят за ключами из цикла событий.
PRINCIPAL = "orbita.principal"
#: Пути без токена — их зовут до входа. Первый говорит браузеру, куда вести на вход.
AUTH_CONFIG_PATH = "/api/auth/config"
#: Второй меняет код на токены и обновляет их. Браузер не ходит в Keycloak сам:
#: `connect-src` документа называет только API (web/build/csp.ts), а Keycloak
#: живёт на своём домене — прямой запрос политика заблокировала бы уже после
#: ввода пароля. Адрес Keycloak при этом настройка сервера, а политика
#: собирается вместе с фронтендом.
AUTH_TOKEN_PATH = "/api/auth/token"
#: Что браузер вправе передать Keycloak: код с PKCE или refresh-токен. Клиента
#: подставляет сервер, остальные поля не уходят.
_TOKEN_GRANTS = {
    "authorization_code": ("code", "redirect_uri", "code_verifier"),
    "refresh_token": ("refresh_token",),
}
#: Код, verifier и refresh-токен Keycloak — единицы килобайт.
_TOKEN_BODY_BYTES = 16 * 1024
#: Метрики сервера LangGraph и Orbita (metrics.py). Кроме обычного входа их
#: открывает METRICS_TOKEN — и больше он не открывает ничего.
METRICS_PATH = "/metrics"
#: Загрузка файла в чат (`PUT /api/chats/{thread_id}/files`): единственный путь,
#: где тело — не JSON служебного API, а сам файл, и потолок у него свой.
_UPLOAD_PATH = re.compile(r"/api/chats/[^/]+/files")
# Только асимметричные: с HS256 ключом стал бы публичный JWKS.
_ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384"]
_JWKS: dict[str, jwt.PyJWKClient] = {}


#: Владелец всего, что сделано админ-токеном: healthcheck, скрипты, CI и
#: интерфейс без OIDC. У пользователя Keycloak `sub` — UUID, совпасть не может.
SERVICE_SUBJECT = "service"


@dataclass(frozen=True)
class Principal:
    """Кто прислал запрос.

    `admin` — правит сервер: настройки `.env` и журнал. Это роль, а не
    доступ к чужому: треды и личные подключения администратора так же
    личные, как у всех. `service` — админ-токен, а не человек: ему видно
    всё, и Jira он читает общим токеном из `.env`.
    """

    subject: str
    name: str
    admin: bool = False
    service: bool = False


SERVICE = Principal(SERVICE_SUBJECT, "API_ADMIN_TOKEN", admin=True, service=True)

#: Тот же пользователь для кода, до которого scope не доходит: вызовов Jira из
#: роутов, уехавших в поток (`asyncio.to_thread` копирует контекст).
_CURRENT: ContextVar[Principal | None] = ContextVar("orbita_principal", default=None)


def current() -> Principal | None:
    """Пользователь текущего HTTP-запроса; вне запроса — None."""
    return _CURRENT.get()


def principal_of(scope: Scope) -> Principal | None:
    found = scope.get(PRINCIPAL)
    return found if isinstance(found, Principal) else None


def _unauthorized(message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=401, headers={"WWW-Authenticate": "Bearer"})


def auth_config() -> dict:
    """Что нужно браузеру для входа. Пути — соглашение Keycloak."""
    issuer = cfg.oidc_issuer()
    if not issuer:
        return {"enabled": False}
    endpoint = f"{issuer}/protocol/openid-connect"
    # Только адреса для переходов страницы: их политика документа не ограничивает.
    # Токены браузер получает через AUTH_TOKEN_PATH.
    return {
        "enabled": True,
        "issuer": issuer,
        "client_id": cfg.oidc_client_id(),
        "authorization_endpoint": f"{endpoint}/auth",
        "end_session_endpoint": f"{endpoint}/logout",
    }


def _token_url(issuer: str) -> str:
    """Token endpoint для запроса сервера.

    Серверу realm бывает виден не так, как браузеру (Keycloak из Compose). Тогда
    OIDC_JWKS_URL уже ведёт на внутренний адрес, и token endpoint лежит рядом с
    ключами по тому же соглашению Keycloak.
    """
    jwks = (cfg.oidc_jwks_url() or "").rstrip("/")
    if jwks.endswith("/protocol/openid-connect/certs"):
        return jwks.removesuffix("certs") + "token"
    return f"{issuer}/protocol/openid-connect/token"


def exchange_tokens(body: bytes, content_type: str) -> tuple[int, dict]:
    """Обмен кода на токены или обновление — запросом сервера в Keycloak.

    Ходит по сети, поэтому из асинхронного кода зовётся через поток. Ответ
    Keycloak возвращается как есть, вместе с его кодом: отказ `invalid_grant`
    браузер показывает сам.
    """
    issuer = cfg.oidc_issuer()
    if not issuer:
        return 404, {"error": "вход через OIDC на сервере не включён"}
    if content_type.partition(";")[0].strip().lower() != "application/x-www-form-urlencoded":
        return 415, {"error": "ожидается application/x-www-form-urlencoded"}
    try:
        form = parse_qs(body.decode("ascii"), keep_blank_values=True, strict_parsing=True)
    except (UnicodeDecodeError, ValueError):
        return 400, {"error": "тело запроса токена не разобрано"}
    grant = form.get("grant_type", [])
    fields = _TOKEN_GRANTS.get(grant[0]) if len(grant) == 1 else None
    if fields is None:
        return 400, {"error": "поддерживаются только authorization_code и refresh_token"}
    sent = {"grant_type": grant[0], "client_id": cfg.oidc_client_id()}
    for name in fields:
        values = form.get(name, [])
        if len(values) != 1 or not values[0]:
            return 400, {"error": f"в запросе токена нет поля {name}"}
        sent[name] = values[0]
    url = _token_url(issuer)
    try:
        response = requests.post(
            url,
            data=sent,
            timeout=10,
            allow_redirects=False,
            headers={"Accept": "application/json"},
        )
    except requests.RequestException:
        # Не 401, как и с ключами подписи: вход тут ни при чём, лежит Keycloak.
        return 503, {"error": f"сервер не достучался до Keycloak: {url}"}
    try:
        payload = response.json()
    except ValueError:
        payload = None
    status = response.status_code
    if not isinstance(payload, dict) or not (200 <= status < 300 or 400 <= status < 500):
        return 502, {"error": f"Keycloak ответил {status}"}
    return response.status_code, payload


def _jwks(url: str) -> jwt.PyJWKClient:
    # Набор ключей кешируется на пять минут; незнакомый `kid` (ротация в
    # Keycloak) клиент перезапрашивает сам.
    client = _JWKS.get(url)
    if client is None:
        client = _JWKS[url] = jwt.PyJWKClient(url, lifespan=300, timeout=5)
    return client


def _roles(claims: dict, client_id: str) -> set[str]:
    realm = claims.get("realm_access") or {}
    client = (claims.get("resource_access") or {}).get(client_id) or {}
    return {str(role) for role in [*realm.get("roles", []), *client.get("roles", [])]}


def _user(token: str) -> Principal | JSONResponse:
    issuer = cfg.oidc_issuer()
    client_id = cfg.oidc_client_id()
    jwks_url = cfg.oidc_jwks_url() or f"{issuer}/protocol/openid-connect/certs"
    try:
        key = _jwks(jwks_url).get_signing_key_from_jwt(token)
    except jwt.PyJWKClientConnectionError:
        # Не 401: иначе интерфейс уводил бы на вход по кругу, пока Keycloak лежит.
        return JSONResponse(
            {"error": f"сервер не получил ключи подписи OIDC с {jwks_url}"}, status_code=503
        )
    except jwt.PyJWTError:
        return _unauthorized("токен входа не принят: войдите заново")
    try:
        claims = jwt.decode(
            token,
            key.key,
            algorithms=_ALGORITHMS,
            issuer=issuer,
            leeway=30,
            options={"require": ["exp", "iat", "iss", "sub"], "verify_aud": False},
        )
    except jwt.PyJWTError:
        return _unauthorized("токен входа не принят: войдите заново")
    # Аудитория access-токена Keycloak по умолчанию — `account`, а клиент,
    # которому он выдан, лежит в `azp`. ID-токен того же клиента помечен
    # `typ: ID` и вместо access-токена не годится.
    if claims.get("azp") != client_id or claims.get("typ", "Bearer") != "Bearer":
        return _unauthorized("токен выдан не веб-интерфейсу Orbita")
    roles = _roles(claims, client_id)
    role = cfg.oidc_required_role()
    if role and role not in roles:
        return JSONResponse({"error": f"у пользователя нет роли {role}"}, status_code=403)
    name = claims.get("preferred_username") or claims.get("name") or claims["sub"]
    return Principal(str(claims["sub"]), str(name), admin=cfg.oidc_admin_role() in roles)


def authenticate(headers: Headers) -> Principal | JSONResponse:
    """Кто прислал запрос — или готовый отказ.

    Может сходить по сети за ключами Keycloak, поэтому из асинхронного кода
    зовётся через поток.
    """
    expected = cfg.api_admin_token()
    if not expected or not expected.isascii() or not 32 <= len(expected) <= 256:
        return JSONResponse(
            {"error": "задайте на сервере API_ADMIN_TOKEN: 32–256 ASCII-символов"},
            status_code=503,
        )
    values = headers.getlist("authorization")
    scheme, _, provided = (values[0] if len(values) == 1 else "").partition(" ")
    if scheme.lower() == "bearer" and provided.isascii():
        if secrets.compare_digest(provided, expected):
            return SERVICE
        if cfg.oidc_issuer() and provided.count(".") == 2:
            return _user(provided)
    if cfg.oidc_issuer():
        return _unauthorized("API требует вход через OIDC или Bearer-токен из API_ADMIN_TOKEN")
    return _unauthorized("API требует Bearer-токен из API_ADMIN_TOKEN")


def metrics_scraper(headers: Headers) -> bool:
    """Запрос пришёл с METRICS_TOKEN — токеном Prometheus."""
    expected = cfg.metrics_token()
    values = headers.getlist("authorization")
    scheme, _, provided = (values[0] if len(values) == 1 else "").partition(" ")
    return bool(
        expected
        and scheme.lower() == "bearer"
        and secrets.compare_digest(provided.encode(), expected.encode())
    )


def auth_error(headers: Headers, scope: Scope | None = None) -> JSONResponse | None:
    if scope is not None and isinstance(scope.get(PRINCIPAL), Principal):
        return None
    result = authenticate(headers)
    return result if isinstance(result, JSONResponse) else None


def admin_error(scope: Scope) -> JSONResponse | None:
    """Отказ всем, кроме администратора. Вход уже проверен middleware."""
    principal = principal_of(scope)
    if principal is None:
        return _unauthorized("API требует вход")
    if principal.admin:
        return None
    return JSONResponse(
        {
            "error": f"это правит администратор Orbita (роль {cfg.oidc_admin_role()})",
            "error_code": "admin_required",
        },
        status_code=403,
    )


async def _read_body(
    headers: Headers, receive: Receive, limit: int, limit_name: str
) -> bytes | tuple[int, str] | None:
    """Тело запроса целиком — или отказ (статус, текст). None — клиент ушёл."""
    lengths = headers.getlist("content-length")
    if lengths:
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdecimal():
            return 400, "некорректный Content-Length"
        if len(lengths[0]) > 10 or int(lengths[0]) > limit:
            return 413, f"тело запроса превышает {limit_name}"
    body = bytearray()
    try:
        async with asyncio.timeout(30):
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return None
                body.extend(message.get("body", b""))
                if len(body) > limit:
                    return 413, f"тело запроса превышает {limit_name}"
                if not message.get("more_body", False):
                    break
    except TimeoutError:
        return 408, "истекло время чтения запроса"
    if lengths and int(lengths[0]) != len(body):
        return 400, "Content-Length не соответствует телу запроса"
    return bytes(body)


def _body_limit(scope: Scope) -> tuple[int, str]:
    """Потолок тела запроса и имя настройки, которая его задаёт."""
    if scope.get("method") == "PUT" and _UPLOAD_PATH.fullmatch(scope.get("path", "")):
        return cfg.chat_file_max_bytes(), "CHAT_FILE_MAX_BYTES"
    return cfg.api_max_request_bytes(), "API_MAX_REQUEST_BYTES"


class ApiSecurityMiddleware:
    """LangGraph imports this middleware from http.app for the entire server.

    Buffer bounded request bodies before dispatch so a streaming/chunked body
    cannot bypass the size limit or cause partial mutations before rejection.
    Response streams are forwarded without buffering.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
            await send(message)

        async def reject(status, message):
            await JSONResponse({"error": message}, status_code=status)(scope, receive, secure_send)

        if scope.get("path") == AUTH_CONFIG_PATH and scope.get("method") in ("GET", "HEAD"):
            await JSONResponse(auth_config())(scope, receive, secure_send)
            return
        headers = Headers(scope=scope)
        if scope.get("path") == AUTH_TOKEN_PATH and scope.get("method") == "POST":
            body = await _read_body(headers, receive, _TOKEN_BODY_BYTES, "16 КБ")
            if isinstance(body, bytes):
                status, payload = await asyncio.to_thread(
                    exchange_tokens, body, headers.get("content-type", "")
                )
                await JSONResponse(payload, status_code=status)(scope, receive, secure_send)
            elif body is not None:
                await reject(*body)
            return
        # Пользователя у сборщика нет, и роут метрик его не спрашивает: он
        # встроенный и стоит вне авторизации LangGraph (`auth.py`).
        if (
            scope.get("path") == METRICS_PATH
            and scope.get("method") == "GET"
            and metrics_scraper(headers)
        ):
            await self.app(scope, receive, secure_send)
            return
        principal = await asyncio.to_thread(authenticate, headers)
        if isinstance(principal, JSONResponse):
            await principal(scope, receive, secure_send)
            return
        scope[PRINCIPAL] = principal
        body = await _read_body(headers, receive, *_body_limit(scope))
        if body is None:
            return
        if isinstance(body, tuple):
            await reject(*body)
            return
        delivered = False

        async def buffered_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        # Пользователь виден только внутри этого запроса. Задачи, которые он
        # успел породить, копию контекста уносят с собой — поэтому код прогона
        # берёт пользователя из конфига прогона, а не отсюда (`credentials`).
        token = _CURRENT.set(principal)
        try:
            await self.app(scope, buffered_receive, secure_send)
        finally:
            _CURRENT.reset(token)
