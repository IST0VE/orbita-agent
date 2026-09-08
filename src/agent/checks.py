"""
Проверки пакета документов кодом: то, за что не надо платить модели.

Формальную ошибку дешевле найти регуляркой, чем вызовом. Битый JSON в примере,
ссылка на требование, которого нет ни в одном документе, endpoint, о котором
знает архитектура и не знает контракт API, — всё это находится арифметикой,
находится целиком и находится одинаково на каждом прогоне. Модель на тех же
данных отвечает похоже, но не одинаково, и стоит денег за каждый абзац, по
которому она проходит.

Поэтому граница проведена так: здесь только то, что проверяется точно и
объяснимо построчно. Смысловые расхождения — статус в контракте, которого нет
в state machine; событие без потребителя; нефункциональное требование без
механизма — остаются модели, и она получает результат этой сверки как данные,
а не переоткрывает его сама.

Трассировка держится на идентификаторах требований (`ФТ-12`, `NFR-3`, `REQ-7`).
Их может не быть: конвейер аналитики нумерует требования списком, а не ключами.
Тогда сверка не молчит и не выдумывает — она говорит, что трассировать нечем,
и это находка, а не отказ. Тот же приём записан в `diagram_roles._check`:
документ без ссылок `[id: ...]` не «проверен успешно», а не проверяем вообще.

Ничего отсюда не ходит в сеть и не вызывает модель.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

# Идентификатор требования. Дефис обязателен: без него `US 2` и «раздел 3»
# ловились бы наравне с настоящими ключами, а ложная ссылка в отчёте о ложных
# ссылках — худшее, что может выдать сверка.
_REQUIREMENT = re.compile(r"\b(ФТ|НФТ|FR|NFR|REQ|TREQ)-(\d+(?:\.\d+)*)\b")

# Объявление требования против ссылки на него. Различить их можно только
# местом: пометки «здесь оно объявлено» в документах не бывает.
#
# Объявление — это пункт списка, заголовок или строка таблицы (`_MARKED`), либо
# идентификатор, с которого начинается абзац (`_BARE`). Начало абзаца — пустая
# строка перед ним, и это не придирка. Markdown здесь свёрстан по ширине,
# перенос регулярно ставит идентификатор в начало строки, и без проверки на
# начало блока ссылка вида «по НФТ-2 и <перенос> NFR-9.» засчитывалась
# объявлением. Ошибка мелкая, следствие нет: объявленная ссылка перестаёт быть
# сиротой, и находка уровня blocker исчезает молча.
_MARKED = re.compile(
    r"^[ \t]*(?:[-*+]|\d+[.)]|#{1,6}|\|)[ \t]*(?:\*\*|`)?"
    r"(ФТ|НФТ|FR|NFR|REQ|TREQ)-(\d+(?:\.\d+)*)\b"
)

_BARE = re.compile(r"^(?:\*\*|`)?(ФТ|НФТ|FR|NFR|REQ|TREQ)-(\d+(?:\.\d+)*)\b")


def declarations(text: str) -> set[str]:
    """Идентификаторы, объявленные в этом документе."""
    found: set[str] = set()
    blank_before = True
    for line in text.splitlines():
        match = _MARKED.match(line) or (_BARE.match(line) if blank_before else None)
        if match:
            found.add(f"{match.group(1)}-{match.group(2)}")
        blank_before = not line.strip()
    return found


# Знаки препинания предложения к пути не относятся: «принимает POST /orders.» —
# это тот же маршрут, что `POST /orders` в соседнем документе. Без этой обрезки
# пакет расходился бы сам с собой молча: сверка сравнивала бы разные строки
# и отчитывалась, что всё сошлось.
_ROUTE_TAIL = "/.,;:)»\"'`"

_ENDPOINT = re.compile(
    r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)[ \t]+(/[A-Za-z0-9_\-./{}:]*)"
)

_FENCE = re.compile(r"```[ \t]*(\w+)?[ \t]*\n(.*?)```", re.DOTALL)

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", re.MULTILINE)

# Незакрытые места. `TBD` конвейеры ставят намеренно и это законно — но в
# документе, который считают готовым, каждое такое место обязано быть видно
# оператору, а не найдено читателем на третьей странице.
_PLACEHOLDERS = (
    ("TBD", re.compile(r"\bTBD\b")),
    ("TODO", re.compile(r"\bTODO\b|\bFIXME\b")),
    ("???", re.compile(r"\?{3,}")),
    ("уточнить", re.compile(r"\bуточнит[ьяе]\b", re.IGNORECASE)),
)

BLOCKER = "blocker"
MAJOR = "major"
MINOR = "minor"


@dataclass(frozen=True)
class Finding:
    """Одна находка сверки. `where` — имена документов, а не номера строк."""

    kind: str
    severity: str
    text: str
    where: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "text": self.text,
            "where": list(self.where),
        }


@dataclass
class Requirement:
    """Требование пакета: где объявлено и где на него ссылаются."""

    key: str
    declared_in: list[str] = field(default_factory=list)
    mentioned_in: list[str] = field(default_factory=list)

    @property
    def covered(self) -> bool:
        """Есть ли документ, который на требование ссылается, но не объявляет."""
        return bool(set(self.mentioned_in) - set(self.declared_in))

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "declared_in": self.declared_in,
            "mentioned_in": self.mentioned_in,
            "covered": self.covered,
        }


def _sorted_keys(keys: list[str]) -> list[str]:
    """Ключи по префиксу и номеру, а не лексикографически: ФТ-10 после ФТ-9."""

    def order(key: str) -> tuple:
        prefix, _, number = key.partition("-")
        return (prefix, tuple(int(part) for part in number.split(".")))

    return sorted(keys, key=order)


def requirements(package: dict[str, str]) -> list[Requirement]:
    """Требования пакета с местами объявления и упоминания."""
    found: dict[str, Requirement] = {}
    for name, text in package.items():
        declared = declarations(text)
        mentioned = {f"{p}-{n}" for p, n in _REQUIREMENT.findall(text)}
        for key in mentioned:
            item = found.setdefault(key, Requirement(key=key))
            if key in declared:
                item.declared_in.append(name)
            else:
                item.mentioned_in.append(name)
    return [found[key] for key in _sorted_keys(list(found))]


def endpoints(package: dict[str, str]) -> dict[str, list[str]]:
    """`METHOD /path` -> документы, в которых он встретился."""
    found: dict[str, list[str]] = {}
    for name, text in package.items():
        for method, path in set(_ENDPOINT.findall(text)):
            route = f"{method} {path.rstrip(_ROUTE_TAIL) or '/'}"
            found.setdefault(route, []).append(name)
    return {route: sorted(names) for route, names in sorted(found.items())}


def headings(text: str) -> list[str]:
    """Заголовки документа по порядку — оглавление для роли."""
    return [title for _, title in _HEADING.findall(text)]


def _json_findings(package: dict[str, str]) -> list[Finding]:
    """Каждый блок ```json обязан разбираться. Это проверяется, а не читается."""
    broken: list[tuple[str, int, str]] = []
    total = 0
    for name, text in package.items():
        for number, (language, body) in enumerate(_FENCE.findall(text), start=1):
            if (language or "").lower() != "json":
                continue
            total += 1
            try:
                json.loads(body)
            except ValueError as exc:
                broken.append((name, number, str(exc).split("\n")[0]))
    if not broken:
        return []
    where = tuple(sorted({name for name, _, _ in broken}))
    lines = "; ".join(f"{name}, блок {number}: {reason}" for name, number, reason in broken)
    return [
        Finding(
            kind="broken_json",
            severity=MAJOR,
            where=where,
            text=(
                f"Примеров JSON в пакете: {total}, не разбирается: {len(broken)}. {lines}. "
                "Пример, который не парсится, клиент скопирует и получит ошибку раньше, "
                "чем прочитает текст вокруг него."
            ),
        )
    ]


def _requirement_findings(items: list[Requirement]) -> list[Finding]:
    if not items:
        return [
            Finding(
                kind="no_requirement_ids",
                severity=MAJOR,
                text=(
                    "В пакете нет ни одного идентификатора требования вида ФТ-1, NFR-2 "
                    "или REQ-3. Трассировать нечем: связь «требование — решение» "
                    "проверяется только глазами, и проверить её на следующем изменении "
                    "будет так же нечем. Это дефект пакета, а не результат сверки."
                ),
            )
        ]

    out: list[Finding] = []

    orphans = [item.key for item in items if not item.declared_in]
    if orphans:
        out.append(
            Finding(
                kind="orphan_requirement",
                severity=BLOCKER,
                where=tuple(sorted({n for i in items if not i.declared_in for n in i.mentioned_in})),
                text=(
                    "Ссылки на требования, которых нет ни в одном документе пакета: "
                    + ", ".join(_sorted_keys(orphans))
                    + ". Каждая такая ссылка — решение, обоснованное несуществующим "
                    "требованием."
                ),
            )
        )

    uncovered = [item.key for item in items if item.declared_in and not item.covered]
    if uncovered:
        out.append(
            Finding(
                kind="requirement_without_design",
                severity=MAJOR,
                where=tuple(sorted({n for i in items if i.declared_in and not i.covered
                                    for n in i.declared_in})),
                text=(
                    "Требования, на которые не ссылается ни один другой документ пакета: "
                    + ", ".join(_sorted_keys(uncovered))
                    + ". Либо решение по ним не принято, либо принято и не связано "
                    "с требованием — снаружи эти два случая неразличимы."
                ),
            )
        )

    twice = [item.key for item in items if len(item.declared_in) > 1]
    if twice:
        out.append(
            Finding(
                kind="requirement_declared_twice",
                severity=MINOR,
                where=tuple(sorted({n for i in items if len(i.declared_in) > 1
                                    for n in i.declared_in})),
                text=(
                    "Требования объявлены больше чем в одном документе: "
                    + ", ".join(_sorted_keys(twice))
                    + ". У требования должно быть одно место, иначе следующая правка "
                    "поменяет одну из копий."
                ),
            )
        )
    return out


def _endpoint_findings(routes: dict[str, list[str]], package: dict[str, str]) -> list[Finding]:
    if len(package) < 2 or not routes:
        return []
    lonely = [route for route, names in routes.items() if len(names) == 1]
    if not lonely or len(lonely) == len(routes):
        # Все endpoint'ы в одном документе — это нормальный пакет из одного
        # контракта, а не находка. Находка — когда часть маршрутов связана
        # с остальными документами, а часть нет.
        return []
    return [
        Finding(
            kind="endpoint_in_one_document",
            severity=MINOR,
            where=tuple(sorted({names[0] for route, names in routes.items() if len(names) == 1})),
            text=(
                f"Маршрутов в пакете: {len(routes)}, из них упомянуты ровно в одном "
                f"документе: {len(lonely)} — " + ", ".join(f"`{route}`" for route in lonely[:12])
                + ("…" if len(lonely) > 12 else "")
                + ". Остальные маршруты пакет связывает между документами, эти — нет: "
                "проверь, не забыты ли они в архитектуре или не выдуманы ли в ней."
            ),
        )
    ]


def _placeholder_findings(package: dict[str, str]) -> list[Finding]:
    counts: dict[str, int] = {}
    where: set[str] = set()
    for name, text in package.items():
        for label, pattern in _PLACEHOLDERS:
            hits = len(pattern.findall(text))
            if hits:
                counts[label] = counts.get(label, 0) + hits
                where.add(name)
    if not counts:
        return []
    listed = ", ".join(f"{label}: {number}" for label, number in sorted(counts.items()))
    return [
        Finding(
            kind="placeholder",
            severity=MINOR,
            where=tuple(sorted(where)),
            text=(
                f"Незакрытых мест в пакете: {listed}. Пометка сама по себе законна — "
                "закрытым считается пакет, в котором про каждую из них сказано, кто "
                "и когда её закроет."
            ),
        )
    ]


def run(package: dict[str, str]) -> dict:
    """
    Сверить пакет и вернуть данные для ролей.

    `package` — имя документа и его текст. Пустые документы отбрасываются
    молча: пустой файл в папке задачи это не находка сверки, а свойство папки.

    Возвращается словарь, а не текст: его читает и роль (через `audit_roles`),
    и интерфейс, и тест. Текст из него собирает тот, кому он нужен.
    """
    package = {name: text for name, text in package.items() if (text or "").strip()}
    if not package:
        return {
            "documents": [],
            "requirements": [],
            "endpoints": {},
            "findings": [
                Finding(
                    kind="empty_package",
                    severity=BLOCKER,
                    text="Сверять нечего: в пакете нет ни одного непустого документа.",
                ).as_dict()
            ],
        }

    items = requirements(package)
    routes = endpoints(package)
    findings = (
        _requirement_findings(items)
        + _json_findings(package)
        + _endpoint_findings(routes, package)
        + _placeholder_findings(package)
    )
    order = {BLOCKER: 0, MAJOR: 1, MINOR: 2}
    findings.sort(key=lambda finding: order.get(finding.severity, 9))
    return {
        "documents": [
            {"name": name, "chars": len(text), "headings": headings(text)}
            for name, text in package.items()
        ],
        "requirements": [item.as_dict() for item in items],
        "endpoints": routes,
        "findings": [finding.as_dict() for finding in findings],
    }


def as_block(report: dict) -> str:
    """
    Результат сверки в том виде, в каком его увидят роли.

    Не JSON: роль читает его как текст, а не разбирает. Числа и списки здесь
    уже посчитаны — переспрашивать их у модели значило бы платить за арифметику
    и получать её же с ошибками.
    """
    lines = ["## Документы пакета"]
    for item in report["documents"]:
        lines.append(f"- {item['name']} — {item['chars']} символов, разделов {len(item['headings'])}")

    items = report["requirements"]
    lines.append("\n## Трассировка требований")
    if not items:
        lines.append(
            "Идентификаторов требований в пакете нет — таблица не построена. "
            "Построй её по тексту сам и назови это в отчёте: пакет без ключей "
            "нельзя проверить дважды одинаково."
        )
    else:
        covered = sum(1 for item in items if item["covered"])
        lines.append(f"Требований: {len(items)}, из них связаны с решением: {covered}.\n")
        lines.append("| Требование | Объявлено | Упомянуто в |")
        lines.append("|---|---|---|")
        for item in items:
            lines.append(
                f"| {item['key']} | {', '.join(item['declared_in']) or '—'} "
                f"| {', '.join(item['mentioned_in']) or '—'} |"
            )

    routes = report["endpoints"]
    if routes:
        lines.append("\n## Маршруты")
        lines.append(f"Всего: {len(routes)}.\n")
        for route, names in routes.items():
            lines.append(f"- `{route}` — {', '.join(names)}")

    lines.append("\n## Находки сверки")
    if not report["findings"]:
        lines.append(
            "Формальных дефектов не найдено. Это не значит, что пакет верен: "
            "проверено только то, что проверяется арифметикой."
        )
    else:
        for finding in report["findings"]:
            where = f" [{', '.join(finding['where'])}]" if finding["where"] else ""
            lines.append(f"- **{finding['severity']}** ({finding['kind']}){where}: {finding['text']}")
    return "\n".join(lines)
