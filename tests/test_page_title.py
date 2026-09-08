"""
Задача 1.5: `page_title` отвечает за то, что upsert обновляет одну страницу,
а не плодит новые на каждом ходе.

Главный тест здесь — на инвариант: заголовок, посчитанный после первого хода,
обязан совпасть с посчитанным после третьего, хотя state["messages"] за это
время вырос. Он сломается, если кто-то захочет «улучшить» заголовок, подставив
в него дату, номер хода или кусок последнего ответа.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.graph import page_title

CONFIG = {"configurable": {"thread_id": "demo-thread-1"}}

FIRST_QUESTION = "Клиент acc-1024 жалуется, что не приходит экспорт на 250 тысяч строк"


def state(*messages):
    return {"messages": list(messages)}


def test_title_is_stable_across_turns():
    after_first = page_title(state(HumanMessage(FIRST_QUESTION)), CONFIG)
    after_third = page_title(
        state(
            HumanMessage(FIRST_QUESTION),
            AIMessage("Ответ на первый вопрос"),
            HumanMessage("Второй вопрос оператора"),
            AIMessage("Ответ на второй"),
            HumanMessage("Третий вопрос оператора"),
            AIMessage("Ответ на третий"),
        ),
        CONFIG,
    )

    assert after_first == after_third


def test_title_contains_agent_name_first_question_and_thread():
    title = page_title(state(HumanMessage(FIRST_QUESTION)), CONFIG)
    assert title == f"Orbita: {FIRST_QUESTION} [demo-thread-1]"


def test_agent_name_comes_from_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_NAME", "Спутник")
    assert page_title(state(HumanMessage("вопрос")), CONFIG).startswith("Спутник: ")


def test_topic_is_truncated_to_max_len(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_TITLE_MAX_LEN", "10")
    title = page_title(state(HumanMessage(FIRST_QUESTION)), CONFIG)
    assert title == f"Orbita: {FIRST_QUESTION[:10]} [demo-thread-1]"


def test_whitespace_in_question_is_collapsed():
    """Перенос строки в заголовке страницы Confluence не нужен никому."""
    title = page_title(state(HumanMessage("две\n\n  строки")), CONFIG)
    assert title == "Orbita: две строки [demo-thread-1]"


def test_explicit_title_overrides_everything(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_PAGE_TITLE", "Фиксированная страница")
    title = page_title(state(HumanMessage(FIRST_QUESTION)), CONFIG)
    assert title == "Фиксированная страница"


def test_thread_without_human_message_still_has_a_title():
    title = page_title(state(AIMessage("сообщение без вопроса")), CONFIG)
    assert title == "Orbita: задача без описания [demo-thread-1]"


def test_missing_thread_id_falls_back():
    title = page_title(state(HumanMessage("вопрос")), {})
    assert title.endswith("[no-thread]")
