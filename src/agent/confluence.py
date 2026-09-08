"""
Публикация итоговой документации в Confluence.

Модуль отдельно от графа: к агенту это относится ровно одним вызовом
`publish_page()`, всё остальное — детали REST API и авторизации, которые
меняются независимо от логики агента.

Что нужно в `.env` (полный список с описаниями — в `.env.example`, чтение —
в `config.py`):

    CONFLUENCE_BASE_URL=https://org.atlassian.net/wiki   # Cloud — вместе с /wiki
                        https://confluence.example.com   # Server / Data Center
    CONFLUENCE_TOKEN=...                  # API-токен (Cloud) или PAT (Server/DC)
    CONFLUENCE_EMAIL=you@example.com     # только Cloud: e-mail владельца токена
    CONFLUENCE_SPACE_KEY=SUP
    CONFLUENCE_SPACE_ID=98765             # только v2: числовой id пространства
    CONFLUENCE_PARENT_PAGE_ID=123456789   # необязательно: родитель для новой страницы
    CONFLUENCE_PAGE_TITLE=...             # необязательно: фиксированный заголовок
    CONFLUENCE_PUBLISH=0                  # необязательно: выключить этап совсем
    CONFLUENCE_PUBLISH_MODE=changed       # необязательно: each | changed | manual
    CONFLUENCE_API_VERSION=v1             # необязательно: v1 или v2
    CONFLUENCE_API_PATH=/rest/api/content # необязательно: база REST
    CONFLUENCE_TIMEOUT_S=30               # необязательно: таймаут запроса
    CONFLUENCE_VERSION_MESSAGE=...        # необязательно: комментарий к версии
    CONFLUENCE_MAX_TURNS=10               # необязательно: сколько ходов целиком
    CONFLUENCE_INCLUDE_TOOL_OUTPUT=0      # необязательно: ответы систем на страницу
    CONFLUENCE_MASK_PATTERNS=...          # необязательно: регулярки для маскирования

Способ авторизации выбирается по наличию CONFLUENCE_EMAIL:
  задан    -> Basic (email:token) — так устроен Confluence Cloud;
  не задан -> Bearer <token>      — так устроен personal access token в Server/DC.

По умолчанию используется REST API v1 (`/rest/api/content`) — единственная
версия, доступная и в Cloud, и в Data Center. В Cloud часть v1-эндпоинтов
помечена устаревшей; если Atlassian её выключит, база меняется через
CONFLUENCE_API_PATH, а формат тела и поиск по заголовку — в `_call()` и
`find_page()`.
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

import requests

from agent import config as cfg
from agent import request_pacing

REQUIRED_VARS = cfg.CONFLUENCE_REQUIRED_VARS


class ConfluenceError(RuntimeError):
    """Публикация не удалась: сеть, авторизация или отказ API."""


class ConfluenceBlocked(ConfluenceError):
    """Запрос отбил шлюз перед Confluence: сам сервер его не видел.

    Отдельный тип нужен вызывающему коду: такой отказ проходит сам, и попытку
    имеет смысл повторить, а не выводить оператору как ошибку публикации.
    """


@dataclass(frozen=True)
class Settings:
    """Снимок настроек на одну публикацию: дальше по коду окружение не читается."""

    base_url: str
    token: str
    space_key: str = ""
    space_id: str | None = None
    email: str | None = None
    parent_id: str | None = None
    api_path: str = "/rest/api/content"
    api_version: str = "v1"
    timeout_s: float = 30.0
    # Пауза перед запросом: у wiki и у трекера она своя, см. `request_pacing`.
    interval_s: float = 10.0
    version_message: str = "обновлено агентом"
    # Поиск и чтение появились позже публикации и живут своими настройками:
    # полнотекстовый поиск есть только у v1, и путь к нему не выводится из
    # `api_path` версии v2 никаким преобразованием.
    search_path: str = "/rest/api/content/search"
    search_limit: int = 8
    read_max_chars: int = 12000


# --------------------------------------------------------------------------
# Конфигурация снимается единым снимком перед публикацией. Изменения окружения
# процесса видны следующему вызову; отредактированный `.env` требует перезапуска.
# --------------------------------------------------------------------------
def missing_vars() -> list[str]:
    """Каких обязательных переменных не хватает для публикации."""
    return [name for name in cfg.confluence_required_vars() if not cfg.env_str(name)]


def is_enabled() -> bool:
    """CONFLUENCE_PUBLISH=0 — рубильник, чтобы этап не ходил в сеть вообще."""
    return cfg.confluence_publish()


def is_configured() -> bool:
    return is_enabled() and not missing_vars()


def load_settings() -> Settings:
    absent = missing_vars()
    if absent:
        raise ConfluenceError("не заданы переменные окружения: " + ", ".join(absent))
    base_url = cfg.confluence_base_url()
    parsed = urlsplit(base_url)
    local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfluenceError("CONFLUENCE_BASE_URL должен быть абсолютным http(s)-адресом")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfluenceError(
            "CONFLUENCE_BASE_URL не должен содержать учётные данные, query или fragment"
        )
    if parsed.scheme != "https" and not local_http and not cfg.confluence_allow_insecure_http():
        raise ConfluenceError(
            "CONFLUENCE_BASE_URL использует незашифрованный HTTP; нужен HTTPS или "
            "явный CONFLUENCE_ALLOW_INSECURE_HTTP=1"
        )
    return Settings(
        base_url=base_url,
        token=cfg.confluence_token(),
        space_key=cfg.confluence_space_key(),
        space_id=cfg.confluence_space_id(),
        email=cfg.confluence_email(),
        parent_id=cfg.confluence_parent_id(),
        api_path=cfg.confluence_api_path(),
        api_version=cfg.confluence_api_version(),
        timeout_s=cfg.confluence_timeout_s(),
        interval_s=cfg.confluence_request_interval_s(),
        version_message=cfg.confluence_version_message(),
        search_path=cfg.confluence_search_path(),
        search_limit=cfg.confluence_search_limit(),
        read_max_chars=cfg.confluence_read_max_chars(),
    )


# --------------------------------------------------------------------------
# Транспорт
# --------------------------------------------------------------------------
def _auth(s: Settings) -> tuple[dict, tuple[str, str] | None]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if s.email:
        return headers, (s.email, s.token)
    headers["Authorization"] = f"Bearer {s.token}"
    return headers, None


def _reason(response: requests.Response, s: Settings) -> str:
    """
    Внятное сообщение об ошибке вместо простыни HTML из тела ответа.

    Подсказка про учётные данные дописывается только к ответу самого
    Confluence. Ответ шлюза или страницы входа приходит HTML-ом, и разбирать
    его как отказ авторизации нельзя: совет «добавьте CONFLUENCE_EMAIL» на
    блокировку по частоте запросов отправляет чинить исправное.
    """
    block = request_pacing.block_reason(response)
    if block:
        return block
    try:
        payload = response.json()
    except ValueError:
        return " ".join(response.text.split())[:300]
    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("reason") or str(payload)
    else:
        message = str(payload)
    if response.status_code in (401, 403):
        hint = (
            "для Cloud нужен CONFLUENCE_EMAIL рядом с токеном"
            if not s.email
            else "проверьте права токена на пространство"
        )
        message = f"{message} ({hint})"
    return message


def _call(method: str, path: str, s: Settings, **kwargs) -> dict:
    url = s.base_url + path
    headers, auth = _auth(s)
    try:
        response = request_pacing.send(
            method, url, headers=headers, auth=auth, timeout=s.timeout_s,
            interval=s.interval_s, **kwargs
        )
    except requests.RequestException as exc:
        # Разорванное соединение — та же блокировка, только до HTTP-ответа:
        # тип отказа тот же, и повторять её так же осмысленно.
        blocked = request_pacing.transport_block(exc)
        error = ConfluenceBlocked if blocked else ConfluenceError
        advice = "; повторите позже или увеличьте паузу ATLASSIAN_REQUEST_INTERVAL_S" if blocked else ""
        raise error(
            f"{method} {path}: сеть недоступна — {request_pacing.transport_reason(exc)}"
            f" ({request_pacing.transport_detail(exc)}){advice}"
        ) from exc

    if 300 <= response.status_code < 400:
        raise ConfluenceError(f"{method} {path}: HTTP redirect запрещён")
    if response.status_code >= 400:
        # Отказ шлюза отделён типом, а не текстом: по нему решают, повторять ли.
        error = ConfluenceBlocked if request_pacing.block_reason(response) else ConfluenceError
        raise error(f"{method} {path}: HTTP {response.status_code} — {_reason(response, s)}")
    try:
        return response.json()
    except ValueError as exc:
        raise ConfluenceError(f"{method} {path}: ответ не является JSON") from exc


# --------------------------------------------------------------------------
# Диалекты REST
#
# У v1 и v2 отличается не только путь: другой формат тела, другой поиск
# страницы по заголовку, другое место идентификатора пространства. Поэтому
# каждая версия — отдельная реализация трёх операций за общим интерфейсом,
# а не набор `if version == ...` внутри publish_page.
# --------------------------------------------------------------------------
class _V1:
    """`/rest/api/content` — единственная версия, доступная и в Cloud, и в DC."""

    @staticmethod
    def find_page(title: str, s: Settings) -> dict | None:
        data = _call(
            "GET",
            s.api_path,
            s,
            params={
                "type": "page",
                "spaceKey": s.space_key,
                "title": title,
                "expand": "version",
                "limit": 1,
            },
        )
        results = data.get("results") or []
        return results[0] if results else None

    @staticmethod
    def read_page(page_id: str, s: Settings) -> dict:
        return _call(
            "GET",
            f"{s.api_path}/{page_id}",
            s,
            params={"expand": "body.storage,version"},
        )

    @staticmethod
    def create(title: str, storage_html: str, s: Settings, *, status: str = "current") -> dict:
        payload: dict = {
            "type": "page",
            "status": status,
            "title": title,
            "space": {"key": s.space_key},
            "body": {"storage": {"value": storage_html, "representation": "storage"}},
        }
        if s.parent_id:
            payload["ancestors"] = [{"id": s.parent_id}]
        return _call("POST", s.api_path, s, json=payload)

    @staticmethod
    def update(existing: dict, title: str, storage_html: str, s: Settings) -> dict:
        page_id = existing["id"]
        return _call(
            "PUT",
            f"{s.api_path}/{page_id}",
            s,
            json={
                "id": page_id,
                "type": "page",
                "title": title,
                "version": {"number": next_version(existing), "message": s.version_message},
                "body": {"storage": {"value": storage_html, "representation": "storage"}},
            },
        )


class _V2:
    """`/api/v2/pages` — только Confluence Cloud, страница создаётся по spaceId."""

    @staticmethod
    def find_page(title: str, s: Settings) -> dict | None:
        data = _call(
            "GET",
            f"{s.api_path}/pages",
            s,
            params={"space-id": s.space_id, "title": title, "limit": 1},
        )
        results = data.get("results") or []
        return results[0] if results else None

    @staticmethod
    def read_page(page_id: str, s: Settings) -> dict:
        return _call(
            "GET",
            f"{s.api_path}/pages/{page_id}",
            s,
            params={"body-format": "storage"},
        )

    @staticmethod
    def create(title: str, storage_html: str, s: Settings, *, status: str = "current") -> dict:
        payload: dict = {
            "spaceId": s.space_id,
            "status": status,
            "title": title,
            "body": {"representation": "storage", "value": storage_html},
        }
        if s.parent_id:
            payload["parentId"] = s.parent_id
        return _call("POST", f"{s.api_path}/pages", s, json=payload)

    @staticmethod
    def update(existing: dict, title: str, storage_html: str, s: Settings) -> dict:
        page_id = existing["id"]
        return _call(
            "PUT",
            f"{s.api_path}/pages/{page_id}",
            s,
            json={
                "id": page_id,
                "status": "current",
                "title": title,
                "version": {"number": next_version(existing), "message": s.version_message},
                "body": {"representation": "storage", "value": storage_html},
            },
        )


_BACKENDS = {"v1": _V1, "v2": _V2}


def backend(s: Settings):
    """Реализация REST под версию из настроек."""
    try:
        return _BACKENDS[s.api_version]
    except KeyError as exc:
        raise ConfluenceError(
            f"CONFLUENCE_API_VERSION={s.api_version!r}: поддерживаются " + ", ".join(_BACKENDS)
        ) from exc


def next_version(existing: dict) -> int:
    """Номер следующей версии страницы. Формат поля у v1 и v2 совпадает."""
    return (existing.get("version") or {}).get("number", 1) + 1


# --------------------------------------------------------------------------
# Операции над страницей
# --------------------------------------------------------------------------
def find_page(title: str, s: Settings) -> dict | None:
    """Найти страницу по точному заголовку в пространстве. None — если нет."""
    return backend(s).find_page(title, s)


def page_url(data: dict, s: Settings) -> str:
    links = data.get("_links") or {}
    webui = links.get("webui")
    if webui:
        return (links.get("base") or s.base_url).rstrip("/") + webui
    return f"{s.base_url}/pages/viewpage.action?pageId={data.get('id')}"


# Адреса инстансов, у которых свойства share-id не оказалось. Спрашивать его
# заново на каждой странице — лишний запрос к серверу, который и так считает
# конвейер слишком частым гостем.
_NO_SHARE_ID: set[str] = set()


def _write(find, save):
    """
    Запись с пережиданием отказов защитного шлюза перед Confluence.

    Повторять POST вслепую нельзя: блокировка приходит и до того, как сервер
    выполнил запрос, и после — на второй случай в пространстве осталась бы
    вторая страница. Поэтому попытка начинается не с записи, а с поиска:
    `find()` отдаёт уже существующую страницу или None, `save(existing)`
    решает, что с этим делать. Повтор через поиск дороже на один GET и
    единственный, который нельзя превратить в дубль.
    """
    attempt = 0
    while True:
        existing = find()
        try:
            return save(existing)
        except ConfluenceBlocked:
            delay = request_pacing.block_delay(attempt)
            if delay is None:
                raise
            request_pacing.pause(delay)
            attempt += 1


def _draft_page(draft_title: str, storage_html: str, s: Settings, path: str,
                params: dict) -> dict:
    """Прежний черновик по заголовку или новый; правки оператора не трогаются."""

    def find() -> dict | None:
        found = _call("GET", path, s, params=params).get("results") or []
        return found[0] if found else None

    def save(existing: dict | None) -> dict:
        if existing:
            return existing
        return backend(s).create(draft_title, storage_html, s, status="draft")

    return _write(find, save)


def create_draft(title: str, storage_html: str, settings: Settings | None = None,
                 *, draft_key: str = "") -> dict:
    """Create a separate unpublished page; never overwrite an operator's edits.

    A stable title marker lets a retried graph node find its draft again, including
    after a lost POST response. Existing published pages are never updated here.
    """
    s = settings or load_settings()
    marker = hashlib.sha256((title + "\n" + (draft_key or storage_html)).encode()).hexdigest()[:12]
    suffix = f" [Orbita {marker}]"
    draft_title = title[:255 - len(suffix)] + suffix
    path = f"{s.api_path}/pages" if s.api_version == "v2" else s.api_path
    params = {"title": draft_title, "status": "draft", "limit": 1}
    params["space-id" if s.api_version == "v2" else "spaceKey"] = (
        s.space_id if s.api_version == "v2" else s.space_key
    )
    if s.api_version == "v1":
        params["type"] = "page"
    data = _draft_page(draft_title, storage_html, s, path, params)
    page_id = str(data.get("id") or "")
    if not page_id or data.get("status") != "draft":
        raise ConfluenceError("Confluence не подтвердил создание неопубликованного черновика")
    query = {"draftId": page_id}
    note = "Откройте черновик, внесите правки и опубликуйте его в Confluence."
    own_account = " Открывайте под учётной записью, чей токен настроен в Orbita."
    if s.base_url in _NO_SHARE_ID:
        # Инстанс уже отвечал, что такого свойства у него нет. Спрашивать
        # заново на каждой странице — лишняя треть запросов, а именно их
        # количество и набирает блокировку защитного шлюза.
        note += own_account
    else:
        try:
            if s.api_version == "v2":
                properties = _call("GET", f"{path}/{page_id}/properties", s,
                                   params={"key": "share-id"}).get("results") or []
                share_id = next(
                    (p.get("value") for p in properties if p.get("key") == "share-id"), None
                )
            else:
                prop = _call("GET", f"{path}/{page_id}/property/share-id", s,
                             params={"status": "draft"})
                share_id = prop.get("value")
            if isinstance(share_id, str) and share_id:
                query["draftShareId"] = share_id
            else:
                note += own_account
        except ConfluenceBlocked:
            # The draft already exists; unavailable sharing metadata must not hide its link.
            note += " Ссылка для совместного доступа недоступна; откройте под владельцем токена Orbita."
        except ConfluenceError:
            # Отказ самого Confluence: свойства share-id на этом сервере нет
            # (Server и Data Center делятся черновиком иначе), и на следующих
            # страницах спрашивать уже незачем.
            _NO_SHARE_ID.add(s.base_url)
            note += own_account
    return {
        "status": "draft",
        "title": str(data.get("title") or draft_title),
        "page_id": page_id,
        "url": f"{s.base_url}/pages/resumedraft.action?{urlencode(query)}",
        "reason": note,
    }


# --------------------------------------------------------------------------
# Чтение и поиск
#
# Публикация была односторонней: агент писал на wiki и никогда её не читал.
# Графу подготовки задачи нужно обратное направление — сначала посмотреть, что
# по теме уже написано, и только потом писать своё. Иначе он честно, дорого и
# заново сочиняет то, что лежит в пространстве третий год.
#
# Поиск один на обе версии REST, и это не упрощение: у v2 полнотекстового
# поиска нет вообще. Cloud продолжает отдавать v1-эндпоинт CQL, поэтому
# страницы можно писать через v2, а искать через v1 — путь настраивается
# отдельной CONFLUENCE_SEARCH_PATH.
# --------------------------------------------------------------------------
def _cql_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def search(query: str, settings: Settings | None = None) -> list[dict]:
    """
    Страницы пространства по тексту запроса.

    CQL собирается здесь, а не приходит от модели: свободный CQL — чужой язык
    запросов под нашим токеном, и `space = OTHER` в нём выносит поиск за
    пределы пространства, которое разрешил оператор. Ограничение по
    CONFLUENCE_SPACE_KEY ставится тут, и снять его изнутри запроса нельзя.
    """
    s = settings or load_settings()
    text = (query or "").strip()
    if not text:
        return []

    parts = ['type = "page"', f'text ~ "{_cql_escape(text)}"']
    if s.space_key:
        parts.insert(1, f'space = "{_cql_escape(s.space_key)}"')
    data = _call(
        "GET",
        s.search_path,
        s,
        params={"cql": " AND ".join(parts), "limit": s.search_limit},
    )

    found = []
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        # `/rest/api/content/search` отдаёт страницы напрямую, `/rest/api/search`
        # — обёрнутыми в `content` рядом с фрагментом совпадения. Путь берётся
        # из настроек, поэтому поддержаны обе формы.
        content = item.get("content") if isinstance(item.get("content"), dict) else item
        page_id = str(content.get("id") or "")
        if not page_id:
            continue
        excerpt = " ".join(_TAG.sub(" ", str(item.get("excerpt") or "")).split())
        found.append(
            {
                "id": page_id,
                "title": str(content.get("title") or item.get("title") or ""),
                "url": page_url(content, s),
                "excerpt": html.unescape(excerpt),
            }
        )
    return found


def fetch_page(page_id: str, settings: Settings | None = None) -> dict:
    """Заголовок, адрес и текст страницы по её идентификатору."""
    s = settings or load_settings()
    page_id = str(page_id or "").strip()
    if not page_id.isdigit():
        raise ConfluenceError(
            f"{page_id!r} не похоже на идентификатор страницы Confluence: нужно число из поиска"
        )
    data = backend(s).read_page(page_id, s)
    storage = ((data.get("body") or {}).get("storage") or {}).get("value") or ""
    text = storage_to_text(storage)
    return {
        "id": str(data.get("id") or page_id),
        "title": str(data.get("title") or ""),
        "url": page_url(data, s),
        "text": text[: s.read_max_chars],
        "truncated": len(text) > s.read_max_chars,
    }


# --------------------------------------------------------------------------
# Ссылка на страницу
#
# Обычный вход конвейера декомпозиции — не идентификатор страницы, а ссылка из
# адресной строки: «разложи вот это <адрес>». Разбирает её регулярка, а не
# модель, по тому же доводу, что и ключ задачи в `jira.find_keys`: аргумент у
# чтения ровно один, он уже написан в запросе, и просить за него отдельный
# вызов значит платить за работу, в которой модель ничего не выбирает.
# --------------------------------------------------------------------------
_PAGE_ID = re.compile(r"(?:pageId=|/pages/(?:viewpage\.action\?pageId=)?)(\d{4,})")
_LINK = re.compile(r"https?://([^/\s]+)")


def find_page_ids(text: str) -> list[str]:
    """
    Идентификаторы страниц, упомянутые в тексте, в порядке появления.

    Поддерживаются обе формы адреса: старая (`viewpage.action?pageId=123456`)
    и нынешняя (`/wiki/spaces/DOCS/pages/123456/Заголовок`). Четыре цифры
    минимум — чтобы номер версии или год в ссылке не стал идентификатором.
    """
    seen: dict[str, None] = {}
    for match in _PAGE_ID.finditer(text or ""):
        seen.setdefault(match.group(1), None)
    return list(seen)


def foreign_hosts(text: str) -> list[str]:
    """
    Хосты ссылок из текста, не совпадающие с настроенным CONFLUENCE_BASE_URL.

    Тот же случай, что и с чужим трекером в `jira.foreign_hosts`: страница
    12345 есть в любой вики, и ссылка на чужую заставит прочитать из нашей
    совсем другой документ. Отказывать нельзя — у компании бывает и зеркало,
    и второй домен, — поэтому расхождение показывается, а решает человек.
    """
    ours = urlsplit(cfg.confluence_base_url()).hostname or ""
    seen: dict[str, None] = {}
    for match in _LINK.finditer(text or ""):
        host = match.group(1).split("@")[-1].split(":")[0].lower()
        if ours and host != ours:
            seen.setdefault(host, None)
    return list(seen)


# Storage format — это XHTML с макросами Confluence. Полный разбор не нужен:
# модели нужно содержание страницы, а не её вёрстка. Поэтому блочные теги
# становятся переводами строк, всё остальное вырезается, а макросы (`ac:`)
# теряют обёртку, но сохраняют текст внутри — в `code`-макросе лежит ровно тот
# фрагмент конфига, ради которого страницу и открыли.
_BLOCK_END = re.compile(
    r"</(p|div|h[1-6]|li|tr|td|th|blockquote|pre)\s*>|<br\s*/?>", re.IGNORECASE
)
_LIST_ITEM = re.compile(r"<li\b[^>]*>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_CDATA = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
_BLANK_LINES = re.compile(r"\n{3,}")


def storage_to_text(storage_html: str) -> str:
    """Обратное преобразование к `text_to_storage`: страница обычным текстом."""
    if not storage_html:
        return ""
    out = _CDATA.sub(r"\1", storage_html)
    out = _LIST_ITEM.sub("- ", out)
    out = _BLOCK_END.sub("\n", out)
    out = _TAG.sub("", out)
    out = html.unescape(out)
    out = "\n".join(line.strip() for line in out.splitlines())
    return _BLANK_LINES.sub("\n\n", out).strip()


def format_results(results: list[dict]) -> str:
    """Найденные страницы для модели: идентификатор нужен ей, чтобы читать дальше."""
    if not results:
        return ""
    lines = []
    for item in results:
        lines.append(f"- [{item['id']}] {item['title']} — {item['url']}")
        if item.get("excerpt"):
            lines.append(f"  {item['excerpt']}")
    return "\n".join(lines)


def format_page(page: dict) -> str:
    """Прочитанная страница плоским текстом."""
    tail = "\n\n(текст обрезан по лимиту CONFLUENCE_READ_MAX_CHARS)" if page["truncated"] else ""
    return f"{page['title']}\nСсылка: {page['url']}\n\n{page['text']}{tail}"


def publish_page(title: str, storage_html: str, settings: Settings | None = None) -> dict:
    """
    Upsert по заголовку: страница с таким заголовком есть — обновляем с
    инкрементом версии; нет — создаём (под CONFLUENCE_PARENT_PAGE_ID, если задан).

    Заголовок обязан быть стабильным между ходами одного треда, иначе вместо
    обновления одной страницы получится россыпь новых.
    """
    s = settings or load_settings()
    api = backend(s)
    status = ""

    def save(existing: dict | None) -> dict:
        # Заново найденная версия важна и при повторе после блокировки:
        # обновление уходит с тем номером, который сейчас на сервере.
        nonlocal status
        if existing:
            status = "updated"
            return api.update(existing, title, storage_html, s)
        status = "created"
        return api.create(title, storage_html, s)

    data = _write(lambda: api.find_page(title, s), save)

    return {
        "status": status,
        "page_id": data.get("id"),
        "version": (data.get("version") or {}).get("number"),
        "title": title,
        "url": page_url(data, s),
    }


# --------------------------------------------------------------------------
# Текст ответа модели -> storage format
# --------------------------------------------------------------------------
_ORDERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*•]\s+(.*)$")
_HEADING = re.compile(r"^\s*(#{1,6})\s+(.*)$")
_FENCE = re.compile(r"^\s*```")
_BUILTIN_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b"),
    re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?"
        r"-----END [A-Z0-9 ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
)


def _inline(text: str) -> str:
    """Экранирование плюс минимум разметки: **жирный** и `код`."""
    out = html.escape(text, quote=False)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    return out


def mask_text(text: str) -> str:
    """
    Замаскировать в тексте всё, что попадает под CONFLUENCE_MASK_PATTERNS.

    Применяется к тексту до конвертации в storage format — то есть маска видит
    исходные символы, а не экранированные сущности.

    Нерабочее регулярное выражение — это ошибка конфигурации, а не повод
    пропустить шаблон: пропущенная маска означает, что данные уедут на wiki
    открытым текстом. Поэтому падаем сразу и на первом же ходе.
    """
    patterns = cfg.confluence_mask_patterns()
    if not text:
        return text or ""

    replacement = cfg.confluence_mask_replacement()
    out = text
    for pattern in _BUILTIN_SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    for pattern in patterns:
        try:
            out = re.sub(pattern, replacement, out)
        except re.error as exc:
            raise cfg.ConfigError(
                f"CONFLUENCE_MASK_PATTERNS: не компилируется {pattern!r} ({exc})"
            ) from exc
    return out


def expand_macro(title: str, body_html: str) -> str:
    """Свёрнутый блок: заголовок виден всегда, содержимое — по клику."""
    label = html.escape(title, quote=True)
    return (
        '<ac:structured-macro ac:name="expand">'
        f'<ac:parameter ac:name="title">{label}</ac:parameter>'
        f"<ac:rich-text-body>{body_html}</ac:rich-text-body>"
        "</ac:structured-macro>"
    )


def _code_block(lines: list[str]) -> str:
    text = "\n".join(lines).replace("]]>", "]]]]><![CDATA[>")
    return (
        '<ac:structured-macro ac:name="code"><ac:plain-text-body>'
        f"<![CDATA[{text}]]></ac:plain-text-body></ac:structured-macro>"
    )


def _table_cells(line: str) -> list[str]:
    """Markdown row; escaped pipes belong to the cell, not the table grid."""
    cells = re.split(r"(?<!\\)\|", line.strip())
    if not cells[0].strip():
        cells.pop(0)
    if cells and not cells[-1].strip():
        cells.pop()
    return [cell.strip().replace(r"\|", "|") for cell in cells]


def text_to_storage(text: str) -> str:
    """Абзацы, заголовки, списки, Markdown-таблицы и блоки кода в XHTML."""
    out: list[str] = []
    list_tag: str | None = None
    fence: list[str] | None = None

    def close_list() -> None:
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None

    def open_list(tag: str) -> None:
        nonlocal list_tag
        if list_tag != tag:
            close_list()
            out.append(f"<{tag}>")
            list_tag = tag

    lines = (text or "").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if _FENCE.match(line):
            if fence is None:
                close_list()
                fence = []
            else:
                out.append(_code_block(fence))
                fence = None
            continue
        if fence is not None:
            fence.append(line)
            continue

        if not line.strip():
            close_list()
            continue

        if "|" in line and index < len(lines):
            headers = _table_cells(line)
            separator = _table_cells(lines[index])
            if (headers and len(headers) == len(separator)
                    and all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator)):
                close_list()
                index += 1
                out.append("<table><tbody><tr>" + "".join(
                    f"<th>{_inline(cell)}</th>" for cell in headers
                ) + "</tr>")
                while index < len(lines) and "|" in lines[index] and not _FENCE.match(lines[index]):
                    cells = _table_cells(lines[index])
                    if len(cells) > len(headers):
                        break  # preserve malformed rows as text instead of dropping cells
                    cells += [""] * (len(headers) - len(cells))
                    out.append("<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in cells) + "</tr>")
                    index += 1
                out.append("</tbody></table>")
                continue

        heading = _HEADING.match(line)
        if heading:
            close_list()
            level = min(len(heading.group(1)) + 1, 6)
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            continue

        ordered = _ORDERED.match(line)
        if ordered:
            open_list("ol")
            out.append(f"<li>{_inline(ordered.group(1))}</li>")
            continue

        bullet = _BULLET.match(line)
        if bullet:
            open_list("ul")
            out.append(f"<li>{_inline(bullet.group(1))}</li>")
            continue

        close_list()
        out.append(f"<p>{_inline(line.strip())}</p>")

    if fence is not None:  # незакрытый ``` в ответе модели
        out.append(_code_block(fence))
    close_list()
    return "\n".join(out)
