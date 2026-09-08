"""
Чтение задач из Jira: то, чего в проекте до сих пор не было.

Тут только чтение. Ни одна функция этого модуля не создаёт задачу, не меняет
статус и не пишет комментарий. Обратное направление появилось — оно живёт
отдельным модулем `jira_writer.py` и со своим подтверждением, как и обещано
здесь: между черновиком и заведёнными задачами стоит человек, а не ещё один
вызов модели. Общий у них только транспорт (`call`): авторизация, таймаут и
превращение отказа во внятное сообщение обязаны быть одни и те же.

Граф подготовки задачи (`prep_graph.py`) остаётся читающим целиком: его
ПРИМЕРНЫЕ задачи — черновик в документе, который человек читает и правит.

Диалектов два, и различает их `JIRA_API_PATH`:

    /rest/api/3   Cloud: описание и комментарии приезжают ADF-документом
    /rest/api/2   Server / Data Center: то же самое — wiki-разметкой строкой

Разбирают оба `field_text`: агенту нужен текст, а не разметка, и решать эту
разницу в промпте было бы и дороже, и ненадёжнее.

Формат ответа для модели — плоский текст с подписанными полями, а не JSON.
JSON тут не даёт ничего: результат инструмента читает модель, а не парсер.
Зато плоский текст заметно короче на тех же данных, и это прямая экономия на
каждом следующем запросе роли: ответ инструмента остаётся в истории этапа и
уезжает провайдеру ещё раз при каждом следующем вызове.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests

from agent import config as cfg
from agent import request_pacing

REQUIRED_VARS = cfg.JIRA_REQUIRED_VARS

# Ключ задачи: буквенно-цифровой префикс проекта, дефис, номер. Проверять
# существование проекта регулярка не может и не должна — это работа сервера,
# который ответит 404. Её работа — не спутать `ORB-42` с `UTF-8` и `COVID-19`,
# поэтому префикс обязан начинаться с буквы, а номер — стоять на конце слова.
ISSUE_KEY = re.compile(r"\b[A-Z][A-Z0-9]*-\d+\b")


class JiraError(RuntimeError):
    """Задача не прочитана: сеть, авторизация или отказ API."""


class JiraBlocked(JiraError):
    """Запрос отбил шлюз перед Jira: сам трекер его не видел.

    Отдельный тип — чтобы отличить чужую блокировку от отказа Jira: первую
    имеет смысл переждать, вторую разбирают по сообщению трекера.
    """


@dataclass(frozen=True)
class Settings:
    """Снимок настроек на один поход в Jira: дальше по коду окружение не читается."""

    base_url: str
    token: str
    email: str | None = None
    api_path: str = "/rest/api/3"
    # Cloud перевёл поиск на `/search/jql`, Data Center остался на `/search`.
    # Чтение задачи по ключу у обоих одинаковое, поэтому один путь на весь
    # клиент не годится.
    search_path: str = "/search/jql"
    timeout_s: float = 30.0
    # Пауза перед запросом: у трекера и у wiki она своя, см. `request_pacing`.
    interval_s: float = 10.0
    search_limit: int = 10
    comments_limit: int = 20
    max_chars: int = 12000


def missing_vars() -> list[str]:
    """Каких обязательных переменных не хватает для чтения Jira."""
    return [name for name in cfg.jira_required_vars() if not cfg.env_str(name)]


def is_configured() -> bool:
    return not missing_vars()


def load_settings() -> Settings:
    """
    Настройки или внятный отказ. Проверки адреса те же, что у Confluence:
    токен уезжает в заголовке каждого запроса, и отправить его открытым HTTP
    на чужой хост нельзя молча.
    """
    absent = missing_vars()
    if absent:
        raise JiraError("не заданы переменные окружения: " + ", ".join(absent))
    base_url = cfg.jira_base_url()
    parsed = urlsplit(base_url)
    local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise JiraError("JIRA_BASE_URL должен быть абсолютным http(s)-адресом")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise JiraError("JIRA_BASE_URL не должен содержать учётные данные, query или fragment")
    if parsed.scheme != "https" and not local_http and not cfg.jira_allow_insecure_http():
        raise JiraError(
            "JIRA_BASE_URL использует незашифрованный HTTP; нужен HTTPS или "
            "явный JIRA_ALLOW_INSECURE_HTTP=1"
        )
    return Settings(
        base_url=base_url,
        token=cfg.jira_token(),
        email=cfg.jira_email(),
        api_path=cfg.jira_api_path(),
        search_path=cfg.jira_search_path(),
        timeout_s=cfg.jira_timeout_s(),
        interval_s=cfg.jira_request_interval_s(),
        search_limit=cfg.jira_search_limit(),
        comments_limit=cfg.jira_comments_limit(),
        max_chars=cfg.jira_read_max_chars(),
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
    Внятное сообщение вместо простыни HTML из тела ответа.

    Ответ шлюза приходит HTML-ом и разбирается отдельно: подсказка про
    JIRA_EMAIL относится к отказу самой Jira, а на чужую блокировку по
    частоте запросов она отправляет чинить исправное.
    """
    block = request_pacing.block_reason(response)
    if block:
        return block
    try:
        payload = response.json()
    except ValueError:
        return " ".join(response.text.split())[:300]

    if isinstance(payload, dict):
        errors = list(payload.get("errorMessages") or [])
        detail = payload.get("errors") or {}
        if isinstance(detail, dict):
            errors += [f"{name}: {value}" for name, value in detail.items()]
        message = "; ".join(str(item) for item in errors) or str(payload.get("message") or payload)
    else:
        message = str(payload)

    if response.status_code in (401, 403):
        hint = (
            "заполните JIRA_EMAIL рядом с токеном — Bearer принимают не все инстансы"
            if not s.email
            else "проверьте права токена на проект"
        )
        message = f"{message} ({hint})"
    return message[:300]


def call(method: str, path: str, s: Settings, **kwargs) -> dict:
    """
    Один запрос к Jira с общим разбором отказов.

    Имя публичное, потому что этим же транспортом ходит `jira_writer.py`:
    запись живёт отдельным модулем, но авторизация, таймаут и превращение
    ответа в понятное сообщение у чтения и записи обязаны быть одни и те же.
    """
    url = s.base_url + path
    headers, auth = _auth(s)
    try:
        response = request_pacing.send(
            method, url, headers=headers, auth=auth, timeout=s.timeout_s,
            interval=s.interval_s, **kwargs
        )
    except requests.RequestException as exc:
        # Разорванное соединение — та же блокировка, только до HTTP-ответа.
        blocked = request_pacing.transport_block(exc)
        error = JiraBlocked if blocked else JiraError
        advice = "; повторите позже или увеличьте паузу ATLASSIAN_REQUEST_INTERVAL_S" if blocked else ""
        raise error(
            f"{method} {path}: сеть недоступна — {request_pacing.transport_reason(exc)}"
            f" ({request_pacing.transport_detail(exc)}){advice}"
        ) from exc

    if 300 <= response.status_code < 400:
        raise JiraError(f"{method} {path}: HTTP redirect запрещён")
    if response.status_code == 404:
        raise JiraError(f"{method} {path}: объект не найден (HTTP 404)")
    if response.status_code >= 400:
        # Отказ шлюза отделён типом, а не текстом: по нему решают, повторять ли.
        error = JiraBlocked if request_pacing.block_reason(response) else JiraError
        raise error(f"{method} {path}: HTTP {response.status_code} — {_reason(response, s)}")
    try:
        return response.json()
    except ValueError as exc:
        raise JiraError(f"{method} {path}: ответ не является JSON") from exc


# --------------------------------------------------------------------------
# Разбор полей
#
# ADF — дерево узлов с типами: `paragraph`, `text`, `bulletList`, `codeBlock`,
# `mention`, `inlineCard` и ещё десяток. Полный конвертер тут не нужен и вреден:
# агенту нужно содержание, а не вёрстка. Поэтому обход достаёт текст и разделяет
# блоки переводами строк, а разметку теряет намеренно.
# --------------------------------------------------------------------------
_BLOCK_NODES = {
    "paragraph",
    "heading",
    "listItem",
    "blockquote",
    "codeBlock",
    "tableRow",
    "panel",
    "rule",
}


def adf_to_text(node: object) -> str:
    """Плоский текст из ADF-документа Jira Cloud."""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(item) for item in node)
    if not isinstance(node, dict):
        return ""

    kind = node.get("type")
    if kind == "text":
        return str(node.get("text") or "")
    if kind == "hardBreak":
        return "\n"
    # У упоминаний и карточек текста в `content` нет: он лежит в атрибутах.
    # Без этой ветки исполнитель, названный в описании через `@`, из текста
    # исчезает — а это ровно та деталь, ради которой задачу и читают.
    if kind in {"mention", "inlineCard", "emoji"}:
        attrs = node.get("attrs") or {}
        return str(attrs.get("text") or attrs.get("shortName") or attrs.get("url") or "")

    inner = adf_to_text(node.get("content") or [])
    return f"{inner}\n" if kind in _BLOCK_NODES else inner


def field_text(value: object) -> str:
    """
    Текст поля независимо от диалекта: ADF-документ из v3, строка из v2.

    Сюда же приходит `None` — незаполненное описание для Jira дело обычное,
    и падать на нём нельзя: пустое описание это факт о задаче, а не сбой.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict) and value.get("type") == "doc":
        return adf_to_text(value).strip()
    return str(value).strip()


def _name(value: object, *keys: str) -> str:
    """Имя вложенного объекта Jira: статус, тип, пользователь, приоритет."""
    if not isinstance(value, dict):
        return ""
    for key in keys or ("name",):
        found = value.get(key)
        if found:
            return str(found)
    return ""


def _names(values: object, *keys: str) -> list[str]:
    if not isinstance(values, list):
        return []
    return [name for item in values if (name := _name(item, *keys))]


def issue_url(key: str, s: Settings) -> str:
    return f"{s.base_url}/browse/{key}"


def find_keys(text: str) -> list[str]:
    """
    Ключи задач, упомянутые в тексте, в порядке появления и без повторов.

    Ссылка обрабатывается тем же выражением и отдельной ветки не требует:
    в `https://jira.example.com/browse/ORB-123` ключ стоит отдельным словом
    ровно так же, как в «доделать ORB-123». Это важно, потому что обычный вход
    конвейера — не ключ, а ссылка из адресной строки браузера.
    """
    seen: dict[str, None] = {}
    for match in ISSUE_KEY.finditer(text or ""):
        seen.setdefault(match.group(0), None)
    return list(seen)


_LINK = re.compile(r"https?://([^/\s]+)")


def foreign_hosts(text: str) -> list[str]:
    """
    Хосты ссылок из текста, не совпадающие с настроенным JIRA_BASE_URL.

    Ключ задачи ничего не говорит о том, из какого он трекера. Ссылка на чужой
    инстанс с ключом `ORB-123` заставит клиента прочитать `ORB-123` из НАШЕГО:
    в лучшем случае это 404, в худшем — существующая, но совсем другая задача,
    и весь прогон уедет не туда молча. Поэтому расхождение показывается модели,
    а решает человек: отказывать тут нельзя, у компании бывает и прокси, и
    зеркало, и второй домен на тот же трекер.
    """
    ours = urlsplit(cfg.jira_base_url()).hostname or ""
    seen: dict[str, None] = {}
    for match in _LINK.finditer(text or ""):
        host = match.group(1).split("@")[-1].split(":")[0].lower()
        if ours and host != ours:
            seen.setdefault(host, None)
    return list(seen)


# --------------------------------------------------------------------------
# Операции
#
# Набор полей перечислен явно, а не запрошен целиком. Задача с историей на год
# отдаёт по `*all` сотни килобайт кастомных полей, из которых агенту нужны
# полтора десятка, — и каждый лишний килобайт оплачивается ещё раз на каждом
# следующем запросе этапа.
# --------------------------------------------------------------------------
_FIELDS = (
    "summary,description,status,issuetype,priority,labels,components,"
    "assignee,reporter,parent,subtasks,issuelinks,fixVersions,duedate,"
    "created,updated,resolution"
)


def fetch_issue(key: str, settings: Settings | None = None) -> dict:
    """
    Одна задача со связями и комментариями.

    Комментарии просятся отдельным запросом, а не через `expand`: у них свой
    порядок сортировки и свой потолок, и совмещать это с выборкой полей значит
    получить либо все комментарии за три года, либо первые пять из тридцати.
    """
    s = settings or load_settings()
    key = (key or "").strip().upper()
    if not ISSUE_KEY.fullmatch(key):
        raise JiraError(f"{key!r} не похоже на ключ задачи Jira (пример: ORB-123)")

    data = call("GET", f"{s.api_path}/issue/{key}", s, params={"fields": _FIELDS})
    fields = data.get("fields") or {}

    links = []
    for link in fields.get("issuelinks") or []:
        if not isinstance(link, dict):
            continue
        kind = link.get("type") or {}
        for side, label in (("outwardIssue", "outward"), ("inwardIssue", "inward")):
            other = link.get(side)
            if isinstance(other, dict):
                links.append(
                    {
                        "relation": str(kind.get(label) or "связана с"),
                        "key": str(other.get("key") or ""),
                        "summary": str((other.get("fields") or {}).get("summary") or ""),
                    }
                )

    return {
        "key": str(data.get("key") or key),
        "url": issue_url(key, s),
        "summary": str(fields.get("summary") or ""),
        "description": field_text(fields.get("description"))[: s.max_chars],
        "status": _name(fields.get("status")),
        "type": _name(fields.get("issuetype")),
        "priority": _name(fields.get("priority")),
        "resolution": _name(fields.get("resolution")),
        "labels": [str(item) for item in fields.get("labels") or []],
        "components": _names(fields.get("components")),
        "versions": _names(fields.get("fixVersions")),
        "assignee": _name(fields.get("assignee"), "displayName", "name"),
        "reporter": _name(fields.get("reporter"), "displayName", "name"),
        "parent": str((fields.get("parent") or {}).get("key") or ""),
        "subtasks": [
            {
                "key": str(item.get("key") or ""),
                "summary": str((item.get("fields") or {}).get("summary") or ""),
            }
            for item in fields.get("subtasks") or []
            if isinstance(item, dict)
        ],
        "links": links,
        "due": str(fields.get("duedate") or ""),
        "updated": str(fields.get("updated") or ""),
        "comments": comments(key, s),
    }


def comments(key: str, settings: Settings | None = None) -> list[dict]:
    """
    Последние комментарии задачи, от старых к новым.

    В комментариях живёт половина требований: договорённости из обсуждения
    в описание переносят редко. Поэтому они не «дополнительно, если хватит
    места», а часть прочитанной задачи.
    """
    s = settings or load_settings()
    if s.comments_limit <= 0:
        return []
    data = call(
        "GET",
        f"{s.api_path}/issue/{key}/comment",
        s,
        params={"maxResults": s.comments_limit, "orderBy": "-created"},
    )
    found = [
        {
            "author": _name(item.get("author"), "displayName", "name"),
            "created": str(item.get("created") or "")[:10],
            "text": field_text(item.get("body")),
        }
        for item in data.get("comments") or []
        if isinstance(item, dict)
    ]
    # `-created` отдаёт новые первыми, а читать удобнее в порядке разговора.
    found.reverse()
    return [item for item in found if item["text"]]


def _escape(value: str) -> str:
    """Экранирование строки для JQL: обратный слеш и кавычка."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def search(query: str, settings: Settings | None = None) -> list[dict]:
    """
    Поиск задач по тексту.

    JQL собирается здесь, а не приходит от модели. Свободный JQL — это чужой
    язык запросов, выполняемый под нашим токеном: `project = X ORDER BY ...`
    безобиден, а выгрузить одним `created >= -100d` всё, до чего дотягивается
    токен, — уже нет. Поиск по тексту закрывает задачу агента целиком и не
    отдаёт наружу того, чего не просили.
    """
    s = settings or load_settings()
    text = (query or "").strip()
    if not text:
        return []
    jql = f'text ~ "{_escape(text)}" ORDER BY updated DESC'
    data = call(
        "GET",
        f"{s.api_path}{s.search_path}",
        s,
        params={"jql": jql, "maxResults": s.search_limit, "fields": "summary,status,issuetype"},
    )
    found = []
    for item in data.get("issues") or []:
        if not isinstance(item, dict):
            continue
        fields = item.get("fields") or {}
        key = str(item.get("key") or "")
        found.append(
            {
                "key": key,
                "url": issue_url(key, s),
                "summary": str(fields.get("summary") or ""),
                "status": _name(fields.get("status")),
                "type": _name(fields.get("issuetype")),
            }
        )
    return found


# --------------------------------------------------------------------------
# Формат для модели
# --------------------------------------------------------------------------
def format_issue(issue: dict) -> str:
    """Задача плоским текстом: подписанные поля, пустые опущены."""
    lines = [f"{issue['key']}: {issue['summary']}", f"Ссылка: {issue['url']}"]

    fields = (
        ("Тип", issue.get("type")),
        ("Статус", issue.get("status")),
        ("Приоритет", issue.get("priority")),
        ("Резолюция", issue.get("resolution")),
        ("Исполнитель", issue.get("assignee")),
        ("Автор", issue.get("reporter")),
        ("Эпик / родитель", issue.get("parent")),
        ("Компоненты", ", ".join(issue.get("components") or [])),
        ("Метки", ", ".join(issue.get("labels") or [])),
        ("Версии", ", ".join(issue.get("versions") or [])),
        ("Срок", issue.get("due")),
        ("Обновлена", (issue.get("updated") or "")[:10]),
    )
    lines += [f"{name}: {value}" for name, value in fields if value]

    description = (issue.get("description") or "").strip()
    lines.append("\nОписание:\n" + (description or "(пустое — требований в описании нет)"))

    if issue.get("subtasks"):
        lines.append("\nПодзадачи:")
        lines += [f"- {item['key']}: {item['summary']}" for item in issue["subtasks"]]

    if issue.get("links"):
        lines.append("\nСвязи:")
        lines += [
            f"- {item['relation']} {item['key']}: {item['summary']}" for item in issue["links"]
        ]

    if issue.get("comments"):
        lines.append("\nКомментарии (от старых к новым):")
        lines += [
            f"- {item['created']} {item['author']}: {item['text']}" for item in issue["comments"]
        ]

    return "\n".join(lines)


def format_results(results: list[dict]) -> str:
    """Найденные задачи — по строке на каждую."""
    if not results:
        return ""
    return "\n".join(
        f"- {item['key']} [{item['type']}, {item['status']}]: {item['summary']} — {item['url']}"
        for item in results
    )
