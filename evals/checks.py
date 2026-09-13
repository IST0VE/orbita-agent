"""
Измерения качества: что именно считается и почему именно это.

Показателей пять, и каждый отвечает на вопрос, который обычные unit-тесты не
задают. Тесты проверяют, что код делает то, что написано; здесь проверяется,
что получившийся документ пригоден для работы.

    source_accuracy       доля существенных выводов со ссылкой на источник.
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

import re

#: Ссылка на источник: имя файла материалов, ключ Jira или адрес страницы.
SOURCE = re.compile(
    r"(?:\b[\w-]+\.(?:md|txt|json|csv|yaml|yml)\b)|(?:\b[A-Z][A-Z0-9]+-\d+\b)|(?:https?://\S+)"
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


def measure(documents: dict[str, str]) -> dict:
    """Показатели по всем документам прогона."""
    text = "\n".join(documents.values())
    claims = [line for document in documents.values() for line in statements(document)]
    sourced = [line for line in claims if SOURCE.search(line)]
    assumed = [line for line in claims if ASSUMPTION.search(line) or GAP.search(line)]
    unsupported = [line for line in claims if line not in sourced and line not in assumed]
    return {
        "claims": len(claims),
        "sourced_claims": len(sourced),
        "source_accuracy": round(len(sourced) / len(claims), 3) if claims else 0.0,
        "unsupported_claims": len(unsupported),
        "contradictions_mentioned": len(CONTRADICTION.findall(text)),
        "gaps_marked": len(GAP.findall(text)),
        "chars": len(text),
    }
