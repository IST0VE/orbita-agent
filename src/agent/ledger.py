"""
Реестр источников прогона: что прочитано, что найдено и не открыто, по каким запросам.

Собирается кодом, а не моделью. Модель пересказывает прочитанное, и пересказ
теряет ровно то, ради чего реестр нужен: идентификаторы, ссылки и счёт.
27 сентября 2026 документ подготовки задачи называл «12 непрочитанных страниц»
при списке из шестнадцати, а у страниц 291910267 и 291909833 пропали ссылки.

Материал реестра — следы инструментов (`ToolMessage.artifact`, см. `tools.py`)
и то, что прочитала нода чтения задачи без модели: сам тикет, связанные задачи
и файлы, выбранные оператором. Следа модель не видит и подделать не может: его
пишет инструмент рядом с ответом.

Модуль ничего не знает о конвейерах. Какой конвейер сколько читал и как это
назвать на странице, решает тот, кто его вызывает (`prep_roles.ledger`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.evidence import by_source

#: Как источник помечается в документах конвейера. Та же форма, что велят
#: промпты (`prep_prompts.COMMON`): реестр и текст ролей ссылаются одинаково.
TAGS = {"jira": "JIRA", "confluence": "WIKI", "file": "ФАЙЛ"}
SYSTEMS = {"jira": "Jira", "confluence": "Confluence", "file": "Файлы"}


def _key(system: str, item: dict) -> tuple[str, str]:
    ident = item.get("key") or item.get("id") or item.get("name") or ""
    return system, str(ident)


def tag(system: str, ident: str) -> str:
    """`[WIKI 12345]`, `[JIRA ORB-1]`, `[ФАЙЛ встреча.md]`."""
    return f"[{TAGS.get(system, system.upper())} {ident}]"


_SYSTEM_OF = {value: key for key, value in TAGS.items()}


def _source_key(label: str) -> tuple[str, str]:
    """Обратно к паре (система, идентификатор) из тега `[WIKI 12345]`."""
    head, _, ident = label.strip("[]").partition(" ")
    system = _SYSTEM_OF.get(head, head.lower())
    return system, ident if system == "file" else ident.upper()


def collect(messages: list) -> dict:
    """
    Следы инструментов из переписки роли.

    Поиск без следа (ответ инструмента из старого чекпоинта) пропускается: в
    реестре лучше недосчитать, чем выдумать. Неудачное чтение остаётся
    отдельно от удачного — «не открылась» и «не открывали» для читателя разные
    вещи.
    """
    searches: list[dict] = []
    read: dict[tuple[str, str], dict] = {}
    failed: dict[tuple[str, str], dict] = {}
    found: dict[tuple[str, str], dict] = {}

    for message in messages:
        if getattr(message, "type", "") != "tool":
            continue
        trace = getattr(message, "artifact", None)
        if not isinstance(trace, dict):
            continue
        system = str(trace.get("system") or "")
        if trace.get("kind") == "search":
            items = [item for item in trace.get("found") or [] if isinstance(item, dict)]
            searches.append(
                {
                    "system": system,
                    "query": str(trace.get("query") or ""),
                    "scope": str(trace.get("scope") or ""),
                    "count": len(items),
                    "error": str(trace.get("error") or ""),
                }
            )
            for item in items:
                key = _key(system, item)
                if key[1]:
                    found.setdefault(key, {**item, "query": trace.get("query") or ""})
        elif trace.get("kind") == "read":
            key = _key(system, trace)
            if not key[1]:
                continue
            if trace.get("error"):
                failed.setdefault(key, dict(trace))
            else:
                read[key] = dict(trace)
                failed.pop(key, None)

    return {"searches": searches, "read": read, "failed": failed, "found": found}


def _title(item: dict) -> str:
    return str(item.get("title") or item.get("summary") or "")


def _source(item: dict) -> str:
    """Заголовок и ссылка одной строкой: так страницу находят глазами и кликом."""
    title, url = _title(item), str(item.get("url") or "")
    if title and url:
        return f"{title} — {url}"
    return title or url or "—"


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _reader(value: str) -> str:
    """Чьим токеном прочитано — по-человечески."""
    if not value:
        return "—"
    return "общий токен .env" if value == "env" else f"личный токен {value}"


def render(
    collected: dict,
    prefetched: list[dict] | None = None,
    *,
    evidence: Mapping[str, Any] | None = None,
) -> str:
    """
    Реестр Markdown-таблицами.

    prefetched — прочитанное кодом до ролей: `[{"system", "key"|"id"|"name",
    "title", "url", "how", "error"}]`. Стоит первым: с него начинается прогон.

    evidence — записи Evidence прогона (`evidence.gather`). С ними у каждого
    прочитанного появляются id, на который ссылаются документы, версия
    источника и чей токен его читал: без этой таблицы `[EV-3f9a2c]` в тексте
    некуда было бы развернуть.
    """
    prefetched = prefetched or []
    sources = by_source(evidence) if evidence else {}
    read_rows: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    errors: dict[tuple[str, str], str] = {}

    for item in prefetched:
        system = str(item.get("system") or "")
        key = _key(system, item)
        if item.get("error"):
            errors[key] = f"- {tag(*key)} — {item.get('how') or 'не прочитано'}: {item['error']}"
            continue
        if key in seen:
            continue
        seen.add(key)
        read_rows.append((tag(*key), _source(item), str(item.get("how") or "прочитано кодом")))

    for key, item in collected.get("read", {}).items():
        if key in seen:
            continue
        seen.add(key)
        how = "прочитано ролью поиска"
        if item.get("truncated"):
            how += "; текст обрезан по CONFLUENCE_READ_MAX_CHARS"
        read_rows.append((tag(*key), _source(item), how))

    for key, item in collected.get("failed", {}).items():
        errors[key] = f"- {tag(*key)} — не прочитано: {item.get('error')}"

    # Пропуск или ошибка предварительного чтения не отменяет успешное
    # чтение инструментом. Один источник получает один итоговый статус.
    errors = {key: text for key, text in errors.items() if key not in seen}

    unread = [(key, item) for key, item in collected.get("found", {}).items() if key not in seen]
    searches = collected.get("searches", [])

    parts = [
        "Реестр собран кодом по следам инструментов и чтению задачи: ссылки и числа "
        "в нём не пересказаны, а посчитаны."
    ]
    parts.append(f"### Прочитано: {len(read_rows)}")
    if read_rows and sources:
        rows = ["| Id | Тег | Источник | Как получен | Версия | Прочитано |",
                "| --- | --- | --- | --- | --- | --- |"]
        for a, b, c in read_rows:
            item = sources.get(_source_key(a))
            if item is not None and item.own:
                c += "; страница собрана самой Orbita — не первоисточник"
            rows.append(
                "| {} | {} | {} | {} | {} | {} |".format(
                    _cell(item.id if item else "—"), _cell(a), _cell(b), _cell(c),
                    _cell(item.version if item else "—"),
                    _cell(_reader(item.reader) if item else "—"),
                )
            )
        parts.append("\n".join(rows))
    elif read_rows:
        parts.append(
            "\n".join(
                ["| Тег | Источник | Как получен |", "| --- | --- | --- |"]
                + [f"| {_cell(a)} | {_cell(b)} | {_cell(c)} |" for a, b, c in read_rows]
            )
        )
    else:
        parts.append("Ничего.")

    if errors:
        parts.append(f"### Не открылось: {len(errors)}")
        parts.append("\n".join(errors.values()))

    parts.append(f"### Найдено, но не открыто: {len(unread)}")
    if unread:
        parts.append(
            "\n".join(
                ["| Тег | Источник | Найдено по запросу |", "| --- | --- | --- |"]
                + [
                    f"| {_cell(tag(*key))} | {_cell(_source(item))} | {_cell(item.get('query'))} |"
                    for key, item in unread
                ]
            )
        )
    else:
        parts.append("Ничего: всё найденное открыто.")

    parts.append(f"### Запросы: {len(searches)}")
    if searches:
        parts.append(
            "\n".join(
                ["| Система | Запрос | Где искали | Результатов |", "| --- | --- | --- | --- |"]
                + [
                    "| {} | {} | {} | {} |".format(
                        _cell(SYSTEMS.get(item["system"], item["system"])),
                        _cell(item["query"]),
                        _cell(item["scope"] or "—"),
                        _cell(f"ошибка: {item['error']}" if item["error"] else item["count"]),
                    )
                    for item in searches
                ]
            )
        )
    else:
        parts.append("Поиск не выполнялся.")
    return "\n\n".join(parts)


def summary(collected: dict, prefetched: list[dict] | None = None) -> dict[str, Any]:
    """Числа реестра: для итога прогона и тестов."""
    prefetched = prefetched or []
    seen = {_key(str(item.get("system") or ""), item) for item in prefetched if not item.get("error")}
    seen |= set(collected.get("read", {}))
    return {
        "read": len(seen),
        "unread": len([key for key in collected.get("found", {}) if key not in seen]),
        "searches": len(collected.get("searches", [])),
    }
