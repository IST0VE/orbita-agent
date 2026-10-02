"""
Evidence: прочитанный источник как запись, на которую ссылаются и которую проверяют.

Реестр прочитанного (`ledger.py`) отвечает на вопрос «что открывали». Этого
мало: 27 сентября 2026 документы подготовки задачи ссылались на страницу,
которую собрала сама Orbita тремя днями раньше, выводили из неё
«расхождения», пересказывали комментарии задачи, у которой комментариев не
было, и выдавали цитаты о SYNCGW за факты о AUTHGW. Каждая такая ссылка выглядела
как ссылка на прочитанное, а проверить её было нечем: тег `[WIKI 700777]`
говорит, куда смотреть, но не что там написано.

`EvidenceItem` — то, что было прочитано, в одном месте и в одной форме,
какой бы системой оно ни пришло:

  id          `EV-` и шесть знаков хеша от системы, идентификатора и версии.
              Тот же источник той же версии получает тот же id при любом
              чтении — кодом до ролей, инструментом, на следующем ходе треда, —
              поэтому id можно писать в документ: он не зависит от порядка
              чтения и не требует общего счётчика у параллельных вызовов;
  version     версия источника: `updated` задачи, номер версии страницы,
              хеш содержимого файла. По ней видно, что читали не ту страницу,
              которая лежит сейчас;
  location    какая часть источника прочитана: целиком или первые N символов;
  hash        SHA-256 прочитанного текста — ровно того, что видела модель;
  reader      чьим токеном прочитано: пользователь прогона или общий токен
              `.env`. При личных токенах общий склад Evidence стал бы каналом
              утечки: один человек видел бы чужое прочитанное;
  own         страница собрана самой Orbita. Это черновик прошлого прогона,
              а не первоисточник: ссылка на него проверяется критиком.

Текст хранится рядом (`text`): по нему код проверяет цитаты (`critic.py`).
Модуль ничего не читает сам — ни Jira, ни Confluence, ни диск: записи
собирают те, кто читает (`sources.py`, `tools.py`).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from typing import Any

#: Идентификатор источника в документах: `EV-` и шесть шестнадцатеричных знаков.
ID = re.compile(r"\bEV-[0-9a-f]{6}\b")
#: Всё, что похоже на идентификатор, включая искажённые: `EV-12`, `EV-3F9A2C`.
LOOSE_ID = re.compile(r"\bEV-[0-9A-Za-z]{1,12}\b")

#: Кто читал, если токен не личный: скрипт, тест или интерфейс без входа.
SHARED = "env"

#: Системы и как их называть человеку.
SYSTEMS = {"jira": "задача Jira", "confluence": "страница Confluence", "file": "файл чата"}
#: Старые теги в документах: `[JIRA ORB-1]`, `[WIKI 12345]`, `[ФАЙЛ имя]`.
TAGS = {"jira": "JIRA", "confluence": "WIKI", "file": "ФАЙЛ"}

# Шапка страницы, которую публикует сама Orbita (`documents.document_header`).
# По ней своя страница узнаётся в любой вики и под любым заголовком: заголовок
# собирается из вопроса оператора, а шапка — всегда одна.
_OWN_MARKS = ("Страница собрана автоматически", "Правки руками затрёт следующий прогон")


@dataclass(frozen=True)
class EvidenceItem:
    """Один прочитанный источник."""

    id: str
    system: str
    source_id: str
    title: str
    url: str
    version: str
    location: str
    hash: str
    reader: str
    fetched_at: str
    own: bool = False
    # Сведения, по которым критик проверяет утверждения: у задачи — сколько
    # комментариев и спрашивали ли их, у страницы — обрезана ли она.
    meta: dict = field(default_factory=dict)
    text: str = ""

    @property
    def tag(self) -> str:
        return f"[{self.id}]"

    @property
    def legacy(self) -> str:
        """Старый тег того же источника: `[WIKI 12345]`."""
        return f"[{TAGS.get(self.system, self.system.upper())} {self.source_id}]"

    def to_dict(self, *, text: bool = True) -> dict:
        data = asdict(self)
        if not text:
            data.pop("text")
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EvidenceItem:
        known = {item.name for item in fields(cls)}
        values = {key: value for key, value in data.items() if key in known}
        values["meta"] = dict(values.get("meta") or {})
        return cls(**values)


def digest(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def make_id(system: str, source_id: str, version: str) -> str:
    """Идентификатор источника этой версии. Один и тот же при любом чтении."""
    raw = f"{system}\x00{source_id}\x00{version}".encode()
    return "EV-" + hashlib.sha256(raw).hexdigest()[:6]


def reader() -> str:
    """
    Чьим токеном читается прямо сейчас: пользователь прогона или общий `.env`.

    Решает то же, что решает, какой токен взять (`credentials.current_subject`):
    прочитанное личным токеном видел только этот человек, и записать это нужно
    в момент чтения — потом узнать будет не у кого.
    """
    from agent import credentials

    subject = credentials.current_subject()
    return subject if credentials.personal(subject) else SHARED


def is_own(text: str) -> bool:
    """Страница, собранная самой Orbita: в её тексте стоит наша шапка."""
    head = (text or "")[:2000]
    return all(mark in head for mark in _OWN_MARKS)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def for_issue(issue: dict, text: str) -> EvidenceItem:
    """
    Задача Jira: текст — ровно то, что увидела модель (`jira.format_issue`),
    или его голова, если материал упёрся в общий потолок (`sources.linked`).
    """
    key = str(issue.get("key") or "")
    version = str(issue.get("updated") or "") or digest(text)[:12]
    comments = len(issue.get("comments") or [])
    asked = bool(issue.get("comments_read", True))
    whole = len(text) >= len(_format_issue(issue))
    if not whole:
        location = f"первые {len(text)} символов задачи: остальное за лимитом AGENT_INPUT_MAX_CHARS"
    elif asked:
        location = f"задача целиком: поля, описание, комментарии ({comments})"
    else:
        location = "задача целиком: поля и описание; комментарии не запрашивались"
    return EvidenceItem(
        id=make_id("jira", key, version),
        system="jira",
        source_id=key,
        title=str(issue.get("summary") or ""),
        url=str(issue.get("url") or ""),
        version=version,
        location=location,
        hash=digest(text),
        reader=reader(),
        fetched_at=_now(),
        meta={"comments": comments, "comments_read": asked, "truncated": not whole},
        text=text,
    )


def _format_issue(issue: dict) -> str:
    from agent import jira

    try:
        return jira.format_issue(issue)
    except (KeyError, TypeError):
        return ""


def for_page(page: dict, text: str) -> EvidenceItem:
    """Страница Confluence: текст — то, что увидела модель (`confluence.format_page`)."""
    page_id = str(page.get("id") or "")
    number = page.get("version")
    version = str(number) if number not in (None, "") else digest(text)[:12]
    truncated = bool(page.get("truncated"))
    size = len(page.get("text") or "")
    return EvidenceItem(
        id=make_id("confluence", page_id, version),
        system="confluence",
        source_id=page_id,
        title=str(page.get("title") or ""),
        url=str(page.get("url") or ""),
        version=version,
        location=(
            f"первые {size} символов: остальное обрезано по CONFLUENCE_READ_MAX_CHARS"
            if truncated
            else "страница целиком"
        ),
        hash=digest(text),
        reader=reader(),
        fetched_at=_now(),
        own=is_own(page.get("text") or text),
        meta={"truncated": truncated},
        text=text,
    )


def for_file(name: str, text: str, *, truncated: bool = False) -> EvidenceItem:
    """Файл чата. Версия — хеш содержимого: у файла нет ни номера, ни даты правки в тексте."""
    version = digest(text)[:12]
    return EvidenceItem(
        id=make_id("file", name, version),
        system="file",
        source_id=name,
        title=name,
        url="",
        version=version,
        location=(
            f"первые {len(text)} символов: остальное за лимитом AGENT_INPUT_MAX_CHARS"
            if truncated
            else "файл целиком"
        ),
        hash=digest(text),
        reader=reader(),
        fetched_at=_now(),
        meta={"truncated": truncated},
        text=text,
    )


# --------------------------------------------------------------------------
# Как источник видит модель
# --------------------------------------------------------------------------
OWN_WARNING = (
    "ВНИМАНИЕ: эту страницу собрала сама Orbita — это черновик прошлого прогона, "
    "а не первоисточник. Не ссылайся на неё как на факт и не выводи из неё "
    "расхождений: всё верное в ней есть в первоисточниках, а ошибки — её собственные."
)


def header(item: EvidenceItem) -> str:
    """
    Строка перед текстом источника: его id и как на него ссылаться.

    Стоит первой, а не в конце: модель пишет ссылку, пока читает, и id,
    найденный после сорока тысяч символов страницы, до документа не доезжает.
    """
    what = SYSTEMS.get(item.system, item.system)
    line = (
        f"Источник {item.tag}: {what} {item.source_id}"
        + (f" «{item.title}»" if item.title and item.title != item.source_id else "")
        + f", версия {item.version}, {item.location}. Ссылайся на него так: {item.tag}."
    )
    return line + ("\n" + OWN_WARNING if item.own else "")


def attach(item: EvidenceItem) -> tuple[str, dict]:
    """
    Ответ инструмента: текст для модели и след для кода.

    Текст источника лежит в ответе один раз — в самом сообщении. След несёт
    запись без текста и длину шапки: `from_message` отрезает шапку и получает
    ровно прочитанное, а хеш подтверждает, что это оно.
    """
    head = header(item) + "\n\n"
    meta = item.to_dict(text=False)
    meta["skip"] = len(head)
    return head + item.text, meta


def from_message(message: Any) -> EvidenceItem | None:
    """Запись из ответа инструмента. None — ответ без записи (старый чекпоинт, отказ)."""
    if getattr(message, "type", "") != "tool":
        return None
    trace = getattr(message, "artifact", None)
    meta = trace.get("evidence") if isinstance(trace, dict) else None
    if not isinstance(meta, dict) or not meta.get("id"):
        return None
    content = message.content if isinstance(message.content, str) else ""
    text = content[int(meta.get("skip") or 0):]
    if digest(text) != meta.get("hash"):
        # Сообщение правили после чтения: текста, который видела модель,
        # больше нет, и проверять по нему цитаты нельзя.
        return None
    return EvidenceItem.from_dict({**meta, "text": text})


def gather(stored: Mapping[str, Any] | None, messages: Iterable[Any] = ()) -> dict[str, EvidenceItem]:
    """
    Всё прочитанное треда: записи из состояния и из ответов инструментов.

    Один и тот же источник той же версии мог быть прочитан дважды — кодом и
    инструментом, целиком и обрезанным. Остаётся запись с более длинным
    текстом: по ней больше цитат находится честно.
    """
    found: dict[str, EvidenceItem] = {}

    def keep(item: EvidenceItem) -> None:
        old = found.get(item.id)
        if old is None or len(item.text) > len(old.text):
            found[item.id] = item

    for data in (stored or {}).values():
        if isinstance(data, dict) and data.get("id"):
            keep(EvidenceItem.from_dict(data))
    for message in messages:
        item = from_message(message)
        if item is not None:
            keep(item)
    return found


def by_source(items: Mapping[str, EvidenceItem]) -> dict[tuple[str, str], EvidenceItem]:
    """Записи по паре (система, идентификатор): для старых тегов `[WIKI 12345]`."""
    found: dict[tuple[str, str], EvidenceItem] = {}
    for item in items.values():
        key = (item.system, item.source_id if item.system == "file" else item.source_id.upper())
        old = found.get(key)
        if old is None or item.fetched_at > old.fetched_at:
            found[key] = item
    return found


def locate(item: EvidenceItem, line: int) -> str:
    """
    Место в источнике по номеру строки его текста: раздел задачи или строка.

    У задачи раздел известен из формы `jira.format_issue`: описание, связи,
    комментарии по одному. У страницы и файла разделов код не знает, и
    место — номер строки прочитанного текста.
    """
    lines = item.text.split("\n")
    if item.system == "jira":
        section = ""
        for text in lines[: line + 1]:
            if text.startswith("Описание:"):
                section = "описание"
            elif text.startswith("Подзадачи:"):
                section = "подзадачи"
            elif text.startswith("Связи:"):
                section = "связи"
            elif text.startswith("Вложения"):
                section = "вложения"
            elif text.startswith("Комментарии"):
                section = "комментарии"
            elif section.startswith("коммент") and text.startswith("- "):
                section = "комментарий " + text[2:].split(":", 1)[0].strip()
        if section:
            return section
        return "поля задачи"
    return f"строка {line + 1}"
