"""
Измерения качества: что именно считается и почему именно это.

Показателей пять, и каждый отвечает на вопрос, который обычные unit-тесты не
задают. Тесты проверяют, что код делает то, что написано; здесь проверяется,
что получившийся документ пригоден для работы.

    source_accuracy       доля существенных выводов со ссылкой на материал
                          закреплённой версии конкретного случая.
                          Вывод без источника нельзя проверить, а проверять
                          его будет человек, которому его отдали;
    contradictions        найдено ли заложенное противоречие материалов.
                          Конвейер, который его не заметил, выпустил
                          согласованный на вид документ по несогласованному
                          входу;
    unsupported_claims    утверждения, поданные как факт, но не опирающиеся
                          ни на источник, ни на явное предположение;
    gaps_marked           отмечены ли пробелы данных. Пробел, названный
                          пробелом, — это работа; пробел, заполненный
                          догадкой, — это ущерб;
    refusals              отказы, которые обязаны быть: недоступный источник
                          и неудавшаяся публикация не должны выглядеть
                          успехом.

Считается это по тексту документов и по состоянию прогона, без вызова модели:
судья-модель стоила бы денег на каждом прогоне набора и меняла бы оценку
вместе со своей версией.
"""

from __future__ import annotations

import hashlib
import re

#: Ссылка на источник: имя файла материалов, ключ Jira или адрес страницы.
SOURCE = re.compile(
    r"https?://[^\s)\]>]+|\b[\w./-]+\.(?:md|txt|json|csv|yaml|yml)\b"
    r"(?:@sha256:[a-fA-F0-9]+)?|\b[A-Z][A-Z0-9]+-\d+\b"
)

#: Явное предположение: утверждение, поданное как допущение, а не как факт.
ASSUMPTION = re.compile(
    r"(?i)\b(?:предполож|допуст|вероятн|по умолчанию принимаем|assumption|предполагаем)\w*"
)

#: Явно названный пробел данных.
GAP = re.compile(
    r"(?i)\b(?:не указан\w*|нет данных|данных нет|данных недостаточно|не определ\w*"
    r"|не измерял\w*|неизвестн\w*|уточнить|TBD|пробел\w*|не подтвержд\w*"
    r"|недоступ\w*|не прочитан\w*|не решено|не согласован\w*|не устранен\w*)\b"
)

#: Утверждение о поведении системы: то, что читатель примет за факт.
CLAIM = re.compile(
    r"(?i)\b(?:должен|должна|должно|обязан\w*|гарантирует\w*|всегда|никогда|не более|не менее)\b"
)

#: Слова, которыми документ сообщает о найденном противоречии.
CONTRADICTION = re.compile(
    r"(?i)\b(?:противореч\w*|расхожден\w*|конфликт\w*|не согласуется|взаимоисключ\w*)\b"
)


def statements(text: str) -> list[str]:
    """Существенные утверждения документа: строки списков и абзацы с фактами."""
    found = []
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("-*•").strip()
        if len(line) < 20 or line.startswith("#") or line.startswith("|"):
            continue
        if CLAIM.search(line):
            found.append(line)
    return found


def material_versions(materials: dict[str, str]) -> dict[str, str]:
    return {name: hashlib.sha256(body.encode("utf-8")).hexdigest() for name, body in materials.items()}


def _references(line: str) -> list[str]:
    return [match.group(0).rstrip(".,;:") for match in SOURCE.finditer(line)]


def _annotations_match(line: str, annotations: list[dict], materials: dict, valid: set[str]) -> bool:
    for annotation in annotations:
        sources = annotation.get("sources") or {}
        # Annotation is tied to both the source version and a verbatim evidence
        # span. Editing a fixture requires reviewing its facts, not just a hash.
        if (not sources or not set(sources) <= valid
                or any(not quote or quote not in materials[name] for name, quote in sources.items())):
            continue
        refs = {ref.split("@sha256:", 1)[0] for ref in _references(line)}
        if set(sources) <= refs and re.fullmatch(annotation["pattern"], line, re.IGNORECASE):
            return True
    return False


def measure(documents: dict[str, str], *, materials: dict | None = None,
            versions: dict | None = None, facts: list[dict] | None = None,
            contradictions: list[dict] | None = None) -> dict:
    """Показатели по всем документам прогона."""
    text = "\n".join(documents.values())
    claims = [line for document in documents.values() for line in statements(document)]
    materials, versions = materials or {}, versions or {}
    actual = material_versions(materials)
    valid = {name for name, digest in actual.items() if versions.get(name) == digest}

    def valid_ref(ref: str) -> bool:
        name, _, digest = ref.partition("@sha256:")
        return name in valid and (not digest or digest == actual[name])

    cited = [line for line in claims if _references(line)]
    sourced = [line for line in cited if all(valid_ref(ref) for ref in _references(line))]
    grounded = [line for line in sourced if _annotations_match(line, facts or [], materials, valid)]
    assumed = [line for line in claims if ASSUMPTION.search(line) or GAP.search(line)]
    unsupported = [line for line in claims if line not in grounded and line not in assumed]
    return {
        "source_version_errors": sorted(name for name in set(materials) | set(versions)
                                        if name not in valid),
        "claims": len(claims),
        "sourced_claims": len(sourced),
        "citation_presence": round(len(cited) / len(claims), 3) if claims else 0.0,
        "source_accuracy": round(len(sourced) / len(claims), 3) if claims else 0.0,
        "grounded_claims": len(grounded),
        "unsupported_claims": len(unsupported),
        "verified_contradictions": sum(
            any(_annotations_match(line.strip().lstrip("-*•").strip(), [annotation], materials, valid)
                for line in text.splitlines()) for annotation in contradictions or []
        ),
        "contradictions_mentioned": len(CONTRADICTION.findall(text)),
        "gaps_marked": len(GAP.findall(text)),
        "chars": len(text),
    }
