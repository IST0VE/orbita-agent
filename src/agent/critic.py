"""
Первый критик: ссылки и цитаты документа проверяет код, без модели.

Роли конвейера подготовки ссылаются на прочитанное по id (`[EV-3f9a2c]`,
`evidence.py`). Ссылка — это утверждение «это написано вот там», и его можно
проверить, не спрашивая модель: есть ли такой источник среди прочитанного и
есть ли в нём слова, взятые в кавычки. Судья-модель стоил бы денег на каждом
этапе и ошибался бы вместе с автором; сравнение строк не ошибается никогда,
а чего не умеет — не делает.

Правила выросли из прогонов 27 сентября 2026, где каждое из них было нарушено:

  unknown_ref       ссылка на id, которого нет среди прочитанного: модель
                    переписала его с ошибкой или выдумала;
  unread_ref        старый тег `[WIKI 12345]` на источник, который в прогоне не
                    открывали. Строки «найдено, но не открыто» — не нарушение;
  quote_missing     слов в кавычках нет ни в одном прочитанном источнике;
  quote_elsewhere   слова есть, но в другом источнике, а не в том, на который
                    сослались. Так цитаты о SYNCGW уезжали в факты о AUTHGW;
  own_source        ссылка на страницу, собранную самой Orbita: черновик
                    прошлого прогона выдавался за первоисточник, и из него
                    выводились «расхождения» статуса и дат;
  phantom_comment   утверждение о комментариях задачи, у которой их нет или
                    их не запрашивали: конвейер гонялся за «непрочитанными
                    комментариями», которых не существовало;
  foreign_subject   утверждение называет систему из заголовка одного
                    источника, а ссылается на другой, где её имени нет вовсе.

Что критик не делает. Не судит, верен ли пересказ без кавычек: это работа
для модели, и она будет позже. Не правит документ: замечания уходят
приложением на страницу и следующим ролям в бриф, а решает человек.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field

from agent.evidence import LOOSE_ID, EvidenceItem, by_source, locate

KINDS = {
    "unknown_ref": "ссылка на источник, которого нет среди прочитанного",
    "unread_ref": "источник назван, но в этом прогоне не прочитан",
    "quote_missing": "слов в кавычках нет ни в одном прочитанном источнике",
    "quote_elsewhere": "слова в кавычках есть, но в другом источнике",
    "own_source": "ссылка на страницу, собранную самой Orbita",
    "phantom_comment": "утверждение о комментариях, которых у задачи нет",
    "foreign_subject": "источник не упоминает то, о чём утверждение",
}

_EXACT = re.compile(r"EV-[0-9a-f]{6}")
_LEGACY = re.compile(r"\[(JIRA|WIKI|ФАЙЛ)\s+([^\]\n]+?)\]", re.IGNORECASE)
_LEGACY_SYSTEMS = {"jira": "jira", "wiki": "confluence", "файл": "file"}
_FENCE = re.compile(r"^\s*```")

# Цитаты: «ёлочки», „лапки“, “английские” и прямые двойные кавычки.
_QUOTES = (
    re.compile(r"«([^«»\n]+)»"),
    re.compile(r"„([^„“”\n]+)[“”]"),
    re.compile(r"“([^“”\n]+)”"),
    re.compile(r'"([^"\n]+)"'),
)
_CODE = re.compile(r"`([^`\n]+)`")
#: Короче этого слова в кавычках не проверяются: «куб» и «OSP» — это имена
#: на слух из стенограммы, а не цитаты, и искать их дословно бессмысленно.
_MIN_QUOTE = 8
#: Локальные ключи черновика задач и требований: их нет ни в одном источнике.
_LOCAL = re.compile(r"(?:TASK|EPIC|R)-(?:\d+|\?)|EV-\w+", re.IGNORECASE)
_ELLIPSIS = re.compile(r"\s*(?:…|\.\.\.)\s*")

#: Слова, которыми строка сама говорит, что источник не прочитан: это не
#: ссылка на прочитанное, а честный список того, до чего не дошли.
_UNREAD_SAID = re.compile(
    r"(?i)не\s*(?:успел|открыт|открыва|прочит|читал|удалось)|непрочит|не\s+открывали"
)
#: Строка прямо говорит, что комментариев нет: это верное утверждение.
_NO_COMMENTS = re.compile(
    r"(?i)(?:нет|без|ни\s+одного|0|ноль)\s+коммент"
    r"|коммент\w*\s*[:—-]?\s*(?:нет|отсутств|не\s+запрашива|пуст)"
    r"|коммент\w*\s+(?:у\s+задачи\s+)?(?:нет|отсутствуют)"
)
_COMMENT = re.compile(r"(?i)коммент")
#: Строка сама оговаривает применимость или то, что это вывод.
_HEDGE = re.compile(r"(?i)\[ВЫВОД\]|применимост|не\s+подтвержд|про\s+другую\s+систем|страница\s+про")
#: Строка сама говорит, что ссылается на черновик Orbita.
_OWN_SAID = re.compile(r"(?i)orbita|черновик|собран\w*\s+(?:автоматически|конвейером)")

# Имя системы или компонента: латиница с двумя заглавными (AUTHGW, HAProxy) или
# идентификатор через дефис и подчёркивание (syncgw-proxy, users_config).
_NAME = re.compile(r"(?<![\w-])[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*(?![\w-])")
_ISSUE_KEY = re.compile(r"[A-Z][A-Z0-9_]+-\d+")
_NOT_NAMES = {"ev", "wiki", "jira", "tbd", "url"}


@dataclass(frozen=True)
class Finding:
    """Одно замечание к строке документа."""

    stage: str
    line: int
    kind: str
    ref: str
    detail: str
    text: str


@dataclass
class Report:
    """Проверка одного документа: сколько проверено и что не сошлось."""

    stage: str
    refs: int = 0
    sources: int = 0
    quotes: int = 0
    verified: int = 0
    findings: list[Finding] = field(default_factory=list)
    places: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# Сравнение строк
# --------------------------------------------------------------------------
_DASHES = str.maketrans({"—": "-", "–": "-", "‑": "-", "−": "-", "«": '"', "»": '"',
                         "“": '"', "”": '"', "„": '"', "ё": "е", "\u00a0": " "})


def normalize(text: str) -> str:
    """Для сравнения: регистр, ё, тире, кавычки, разметка и пробелы не различаются."""
    text = (text or "").casefold().translate(_DASHES).replace("*", "").replace("`", "")
    return " ".join(text.split())


class _Corpus:
    """Нормализованный текст источника и номера его строк."""

    def __init__(self, text: str) -> None:
        self.starts: list[int] = []
        parts: list[str] = []
        position = 0
        for line in (text or "").split("\n"):
            self.starts.append(position)
            norm = normalize(line)
            parts.append(norm)
            position += len(norm) + 1
        self.text = " ".join(parts)

    def find(self, quote: str) -> int | None:
        """Номер строки источника, где стоит цитата; None — её там нет."""
        fragments = [part for part in _ELLIPSIS.split(quote) if len(part) >= 4] or [quote]
        first: int | None = None
        for fragment in fragments:
            index = self.text.find(normalize(fragment))
            if index < 0:
                return None
            if first is None:
                first = index
        return _line_of(self.starts, first or 0)


def _line_of(starts: list[int], index: int) -> int:
    return max(0, bisect_right(starts, index) - 1)


def _quotes(line: str) -> list[str]:
    found: list[str] = []
    for pattern in _QUOTES:
        for match in pattern.finditer(line):
            text = match.group(1).strip().strip(".,;:!?…").strip()
            if len(text) >= _MIN_QUOTE and re.search(r"\w", text) and not _LOCAL.fullmatch(text):
                found.append(text)
    for match in _CODE.finditer(line):
        text = match.group(1).strip()
        # Код в строке — имя из источника: путь, файл, модуль, команда. Слово
        # без знаков (`backend`) — термин, а не имя, и дословно не ищется.
        if (
            len(text) >= 4
            and re.search(r"[A-Za-z0-9]", text)
            and re.search(r"[/._\- ]", text)
            and not _LOCAL.fullmatch(text)
            and not text.startswith("[")
        ):
            found.append(text)
    return list(dict.fromkeys(found))


def _names(text: str) -> set[str]:
    """Имена систем и компонентов в тексте, в нижнем регистре."""
    found: set[str] = set()
    for match in _NAME.finditer(text or ""):
        word = match.group(0)
        if len(word) < 3 or _ISSUE_KEY.fullmatch(word) or word.casefold() in _NOT_NAMES:
            continue
        capitals = sum(ch.isupper() for ch in word)
        if capitals >= 2 or re.search(r"[A-Za-z][-_][A-Za-z0-9]", word):
            found.add(word.casefold())
    return found


# --------------------------------------------------------------------------
# Проверка
# --------------------------------------------------------------------------
class Critic:
    """
    Проверка документов по одному набору прочитанного.

    Набор собирается один раз на этап: нормализованный текст каждого
    источника и словарь имён из их заголовков не пересчитываются на каждую
    строку.
    """

    def __init__(
        self,
        items: Mapping[str, EvidenceItem],
        *,
        request: str = "",
        primary: str = "",
        ignore: Iterable[str] = (),
    ) -> None:
        self.items = dict(items)
        self.sources = by_source(self.items)
        self.corpus = {key: _Corpus(item.text) for key, item in self.items.items()}
        self.request = _Corpus(request) if request else None
        self.primary = primary
        self.ignore = {normalize(text) for text in ignore if text}
        self.vocabulary: set[str] = set()
        for item in self.items.values():
            self.vocabulary |= _names(item.title)

    # -- ссылки ------------------------------------------------------------
    def _refs(self, line: str) -> tuple[list[EvidenceItem], list[tuple[str, str]]]:
        """Источники, на которые ссылается строка, и ссылки, которые не разрешились."""
        cited: dict[str, EvidenceItem] = {}
        broken: list[tuple[str, str]] = []
        for match in LOOSE_ID.finditer(line):
            token = match.group(0)
            ident = "EV-" + token[3:].lower()
            if _EXACT.fullmatch(ident) and ident in self.items:
                cited[ident] = self.items[ident]
            else:
                broken.append(("unknown_ref", token))
        for match in _LEGACY.finditer(line):
            system = _LEGACY_SYSTEMS[match.group(1).casefold()]
            ident = match.group(2).strip()
            key = (system, ident if system == "file" else ident.upper())
            item = self.sources.get(key)
            if item is not None:
                cited[item.id] = item
            elif not _UNREAD_SAID.search(line):
                broken.append(("unread_ref", match.group(0)))
        return list(cited.values()), broken

    def check(self, document: str, *, stage: str) -> Report:
        report = Report(stage=stage)
        seen: set[str] = set()
        fence = False
        for number, raw in enumerate((document or "").split("\n"), start=1):
            if _FENCE.match(raw):
                fence = not fence
                continue
            line = raw.strip()
            if fence or not line or line.startswith("#"):
                continue
            cited, broken = self._refs(line)
            short = line if len(line) <= 160 else line[:157] + "…"

            def note(kind: str, ref: str, detail: str, *, _n: int = number, _s: str = short) -> None:
                report.findings.append(Finding(stage, _n, kind, ref, detail, _s))

            for kind, token in broken:
                note(kind, token, f"{token}: {KINDS[kind]}")
            self._comments(line, cited, note)
            if not cited:
                continue
            report.refs += len(cited)
            seen |= {item.id for item in cited}

            for item in cited:
                if item.own and not _OWN_SAID.search(line):
                    note(
                        "own_source",
                        item.id,
                        f"{item.id} — страница {item.source_id} «{item.title}» собрана самой "
                        "Orbita: это черновик прошлого прогона, а не первоисточник",
                    )
            for quote in _quotes(line):
                if normalize(quote) in self.ignore:
                    continue
                report.quotes += 1
                self._quote(quote, cited, number, report, note)
            if not _HEDGE.search(line):
                self._subject(line, cited, note)
        report.sources = len(seen)
        return report

    def _quote(self, quote, cited, number, report, note) -> None:
        for item in cited:
            where = self.corpus[item.id].find(quote)
            if where is not None:
                report.verified += 1
                report.places.append(
                    {"line": number, "ref": item.id, "where": locate(item, where), "quote": quote}
                )
                return
        refs = ", ".join(item.id for item in cited)
        for key, corpus in self.corpus.items():
            if key not in {item.id for item in cited} and corpus.find(quote) is not None:
                other = self.items[key]
                note(
                    "quote_elsewhere",
                    key,
                    f"«{quote}» есть в {key} ({_label(other)}), а ссылка — на {refs}",
                )
                return
        if self.request is not None and self.request.find(quote) is not None:
            note("quote_elsewhere", "запрос", f"«{quote}» — слова из запроса оператора, а ссылка — на {refs}")
            return
        cut = [item.id for item in cited if item.meta.get("truncated")]
        tail = f"; {', '.join(cut)} прочитан не целиком" if cut else ""
        note("quote_missing", refs, f"«{quote}» нет ни в {refs}, ни в другом прочитанном{tail}")

    def _subject(self, line: str, cited: list[EvidenceItem], note) -> None:
        texts = [self.corpus[item.id].text for item in cited]
        for name in sorted(_names(line) & self.vocabulary):
            if not any(name in text for text in texts):
                refs = ", ".join(item.id for item in cited)
                owners = [
                    item.id for item in self.items.values() if name in _names(item.title)
                ]
                note(
                    "foreign_subject",
                    refs,
                    f"{refs} не упоминает {name}; это имя из заголовка {', '.join(owners)}",
                )

    def _comments(self, line: str, cited: list[EvidenceItem], note) -> None:
        if not _COMMENT.search(line) or _NO_COMMENTS.search(line):
            return
        issues = [item for item in cited if item.system == "jira"]
        if not issues:
            keys = {match.group(0) for match in _ISSUE_KEY.finditer(line)}
            issues = [
                item for (system, key), item in self.sources.items()
                if system == "jira" and key in keys
            ]
        if not issues and not cited and self.primary in self.items:
            issues = [self.items[self.primary]]
        if not issues or any(item.meta.get("comments", 1) for item in issues):
            return
        for item in issues:
            if item.meta.get("comments_read", True):
                note("phantom_comment", item.id, f"у {item.source_id} нет ни одного комментария")
            else:
                note(
                    "phantom_comment",
                    item.id,
                    f"комментарии {item.source_id} не запрашивались (JIRA_COMMENTS_LIMIT=0)",
                )


def _label(item: EvidenceItem) -> str:
    return f"{item.source_id} «{item.title}»" if item.title and item.title != item.source_id else item.source_id


# --------------------------------------------------------------------------
# Как это видят человек и следующие роли
# --------------------------------------------------------------------------
def findings(reports: Mapping[str, Mapping]) -> list[dict]:
    return [finding for report in reports.values() for finding in report.get("findings") or []]


def render(reports: Mapping[str, Mapping], titles: Mapping[str, str]) -> str:
    """
    Итог проверки Markdown-таблицами: счёт по этапам и замечания по строкам.

    reports — `Report.to_dict()` по ключу этапа, titles — «01. Разбор задачи».
    """
    parts = [
        "Проверено кодом, без модели: каждая ссылка `[EV-…]` ведёт на прочитанный "
        "источник, слова в кавычках стоят в нём дословно, а страницы, собранные самой "
        "Orbita, не выдаются за первоисточник. Пересказ без кавычек код не судит."
    ]
    rows = ["| Этап | Ссылок | Источников | Цитат | Подтверждено | Замечаний |",
            "| --- | --- | --- | --- | --- | --- |"]
    for stage, report in reports.items():
        rows.append(
            f"| {titles.get(stage, stage)} | {report.get('refs', 0)} | {report.get('sources', 0)} | "
            f"{report.get('quotes', 0)} | {report.get('verified', 0)} | "
            f"{len(report.get('findings') or [])} |"
        )
    parts.append("\n".join(rows))
    found = findings(reports)
    parts.append(f"### Замечания: {len(found)}")
    if not found:
        parts.append("Нет: все ссылки ведут на прочитанное, все цитаты найдены.")
        return "\n\n".join(parts)
    table = ["| Этап | Строка | Что не так | Утверждение |", "| --- | --- | --- | --- |"]
    for item in found:
        table.append(
            f"| {_cell(titles.get(item['stage'], item['stage']))} | {item['line']} | "
            f"{_cell(item['detail'])} | {_cell(item['text'])} |"
        )
    parts.append("\n".join(table))
    return "\n\n".join(parts)


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def summary(reports: Mapping[str, Mapping]) -> dict:
    """Числа проверки: для строки под задачей, итога прогона и оценок."""
    found = findings(reports)
    kinds: dict[str, int] = {}
    for item in found:
        kinds[item["kind"]] = kinds.get(item["kind"], 0) + 1
    return {
        "refs": sum(int(report.get("refs") or 0) for report in reports.values()),
        "quotes": sum(int(report.get("quotes") or 0) for report in reports.values()),
        "verified": sum(int(report.get("verified") or 0) for report in reports.values()),
        "findings": len(found),
        "kinds": kinds,
    }
