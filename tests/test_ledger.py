"""
Реестр источников: собирается кодом по следам инструментов, а не пересказом.

27 сентября 2026 документ подготовки задачи называл «12 непрочитанных страниц»
при списке из шестнадцати, а у двух страниц пропали ссылки. Реестр считает то,
что инструменты действительно отдали, и называет каждую строку тем же тегом,
что и документы ролей.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from agent import ledger


def traced(name: str, call_id: str, artifact: dict | None) -> ToolMessage:
    return ToolMessage(content="ответ", tool_call_id=call_id, name=name, artifact=artifact)


SEARCH = {
    "kind": "search",
    "system": "confluence",
    "query": "пользователи nginx",
    "scope": "пространство SUP",
    "found": [
        {"id": "1", "title": "Кластеризация", "url": "https://wiki.example.com/1"},
        {"id": "2", "title": "Демо", "url": "https://wiki.example.com/2"},
    ],
}
JIRA_SEARCH = {
    "kind": "search",
    "system": "jira",
    "query": "смена пароля",
    "scope": "ORB",
    "found": [{"key": "ORB-5", "summary": "Не работает смена пароля", "url": "https://jira.example.com/browse/ORB-5"}],
}


def conversation() -> list:
    return [
        AIMessage(content="", tool_calls=[{"name": "confluence_search", "args": {}, "id": "a"}]),
        traced("confluence_search", "a", SEARCH),
        traced("jira_search", "b", JIRA_SEARCH),
        traced(
            "confluence_page",
            "c",
            {"kind": "read", "system": "confluence", "id": "1", "title": "Кластеризация",
             "url": "https://wiki.example.com/1", "truncated": True},
        ),
        traced("confluence_page", "d", {"kind": "read", "system": "confluence", "id": "9",
                                        "error": "HTTP 404"}),
        traced("read_task_file", "e", {"kind": "read", "system": "file", "name": "встреча.md"}),
        # Ответ из старого чекпоинта, без следа: в реестр не попадает, а не выдумывается.
        traced("confluence_search", "f", None),
    ]


def test_everything_read_is_counted_once():
    collected = ledger.collect(conversation())

    assert set(collected["read"]) == {("confluence", "1"), ("file", "встреча.md")}
    assert set(collected["failed"]) == {("confluence", "9")}
    assert len(collected["searches"]) == 2


def test_found_but_not_opened_is_listed_with_its_query():
    text = ledger.render(ledger.collect(conversation()))

    assert "### Найдено, но не открыто: 2" in text
    assert "| [WIKI 2] | Демо — https://wiki.example.com/2 | пользователи nginx |" in text
    assert "[JIRA ORB-5]" in text


def test_a_failed_read_is_not_mistaken_for_an_unopened_page():
    text = ledger.render(ledger.collect(conversation()))

    assert "### Не открылось: 1" in text
    assert "[WIKI 9] — не прочитано: HTTP 404" in text


def test_truncation_is_said_next_to_the_page():
    text = ledger.render(ledger.collect(conversation()))

    assert "текст обрезан по CONFLUENCE_READ_MAX_CHARS" in text


def test_what_the_code_read_comes_first():
    prefetched = [
        {"system": "jira", "key": "ORB-1", "title": "Задача", "url": "https://jira.example.com/browse/ORB-1",
         "how": "задача прогона, прочитана кодом"},
        {"system": "jira", "key": "ORB-7", "how": "связанная задача", "error": "HTTP 403"},
    ]

    text = ledger.render(ledger.collect(conversation()), prefetched)

    assert "### Прочитано: 3" in text
    assert text.index("[JIRA ORB-1]") < text.index("[WIKI 1]")
    assert "[JIRA ORB-7] — связанная задача: HTTP 403" in text


def test_queries_are_listed_with_their_scope_and_count():
    text = ledger.render(ledger.collect(conversation()))

    assert "| Confluence | пользователи nginx | пространство SUP | 2 |" in text
    assert "| Jira | смена пароля | ORB | 1 |" in text


def test_a_pipe_in_a_title_does_not_break_the_table():
    collected = ledger.collect([
        traced("confluence_page", "x", {"kind": "read", "system": "confluence", "id": "3",
                                        "title": "A | B", "url": "https://wiki.example.com/3"}),
    ])

    assert "A \\| B" in ledger.render(collected)


@pytest.mark.parametrize("error", ["HTTP 403", "потолок PREP_LINKED_PAGES"])
def test_a_later_success_supersedes_a_prefetch_error(error):
    collected = ledger.collect([
        traced("confluence_page", "retry", {
            "kind": "read", "system": "confluence", "id": "7", "title": "Страница",
        }),
    ])
    prefetched = [{"system": "confluence", "id": "7", "error": error}]

    text = ledger.render(collected, prefetched)

    assert "### Прочитано: 1" in text
    assert "Не открылось" not in text
    assert text.count("[WIKI 7]") == 1


def test_repeated_failures_are_counted_once():
    collected = ledger.collect([
        traced("jira_issue", "retry", {
            "kind": "read", "system": "jira", "key": "ORB-7", "error": "HTTP 403",
        }),
    ])

    text = ledger.render(collected, [{"system": "jira", "key": "ORB-7", "error": "HTTP 403"}])

    assert "### Не открылось: 1" in text
    assert text.count("[JIRA ORB-7]") == 1
