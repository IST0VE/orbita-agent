"""
Задачи 2.2 и 2.6: что и в каком объёме уезжает на страницу.

2.2 — `render_body` проходит по всем сообщениям треда, поэтому без потолка
тело PUT растёт линейно вместе с историей.
2.6 — на странице по умолчанию не должно быть сырых ответов инструментов:
сейчас за ними фейковая база, но как только появится настоящая, на wiki
поедут данные клиентов.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.graph import render_body, split_turns

TOOL_CALL = {
    "name": "get_subscription",
    "args": {"account_id": "acc-1024"},
    "id": "call-1",
}


def turn(number: int) -> list:
    """Один ход целиком: вопрос, вызов инструмента, ответ системы, ответ агента."""
    return [
        HumanMessage(f"Вопрос номер {number} про клиента acc-1024"),
        AIMessage(content="", tool_calls=[dict(TOOL_CALL, id=f"call-{number}")]),
        ToolMessage(
            content="тариф Growth, оплачен до 2026-09-01",
            tool_call_id=f"call-{number}",
            name="get_subscription",
        ),
        AIMessage(f"Ответ агента на ход {number}, довольно длинный текст " * 5),
    ]


def thread(turns: int) -> dict:
    messages = [m for i in range(1, turns + 1) for m in turn(i)]
    return {"messages": messages, "usage": {"calls": turns}}


# --------------------------------------------------------------------------
# Разбиение на ходы
# --------------------------------------------------------------------------
def test_thread_splits_on_operator_questions():
    turns = split_turns(thread(3)["messages"])
    assert len(turns) == 3
    assert all(t[0].type == "human" for t in turns)


def test_messages_before_the_first_question_are_not_lost():
    turns = split_turns([AIMessage("системная реплика"), HumanMessage("вопрос")])
    assert len(turns) == 2


# --------------------------------------------------------------------------
# 2.2: потолок ходов
# --------------------------------------------------------------------------
def test_only_last_turns_are_rendered_in_full(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_MAX_TURNS", "2")

    body = render_body(thread(5))

    assert "Ответ агента на ход 5" in body
    assert "Ответ агента на ход 4" in body
    assert "Ответ агента на ход 3" not in body
    assert "Ответ агента на ход 1" not in body


def test_hidden_turns_collapse_into_an_expand_macro(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_MAX_TURNS", "2")

    body = render_body(thread(5))

    assert 'ac:name="expand"' in body
    assert "Ранние ходы: 3" in body
    # В указателе остаются только вопросы оператора, без ответов агента.
    assert "Ход 1: Вопрос номер 1" in body


def test_zero_means_no_limit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_MAX_TURNS", "0")

    body = render_body(thread(5))

    assert "Ответ агента на ход 1" in body
    assert 'ac:name="expand"' not in body


def test_limit_caps_the_bulk_of_the_page(monkeypatch: pytest.MonkeyPatch):
    """
    Главное следствие: тело перестаёт расти пропорционально треду. Полный
    текст занимают ровно CONFLUENCE_MAX_TURNS ходов, а каждый вытесненный
    добавляет одну строку указателя вместо всего хода.
    """
    monkeypatch.setenv("CONFLUENCE_MAX_TURNS", "3")

    short = len(render_body(thread(3)))
    long = len(render_body(thread(30)))
    growth_per_hidden_turn = (long - short) / 27

    assert growth_per_hidden_turn < 100  # строка указателя, а не ход целиком

    monkeypatch.setenv("CONFLUENCE_MAX_TURNS", "0")
    unlimited = len(render_body(thread(30)))
    assert long < unlimited / 3


# --------------------------------------------------------------------------
# 2.6: данные клиентов на корпоративной wiki
# --------------------------------------------------------------------------
def test_tool_output_is_not_published_by_default():
    body = render_body(thread(1))

    assert "Ответ системы" not in body
    assert "тариф Growth" not in body
    # Сам факт обращения к системе остаётся — это часть разбора инцидента.
    assert "Запрос в систему: get_subscription" in body


def test_tool_arguments_are_not_published_by_default():
    """
    Аргументы вызова — тоже данные клиента: идентификатор аккаунта. Вопрос
    оператора здесь его не упоминает, иначе проверялся бы не тот источник.
    """
    messages = [
        HumanMessage("Почему не приходит экспорт?"),
        AIMessage(content="", tool_calls=[TOOL_CALL]),
        ToolMessage(
            content="тариф Growth",
            tool_call_id="call-1",
            name="get_subscription",
        ),
        AIMessage("Ответ агента"),
    ]

    body = render_body({"messages": messages, "usage": {}})

    assert "acc-1024" not in body
    assert "Запрос в систему: get_subscription" in body


def test_tool_output_can_be_switched_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_INCLUDE_TOOL_OUTPUT", "1")

    body = render_body(thread(1))

    assert "Ответ системы (get_subscription)" in body
    assert "тариф Growth" in body
    assert "acc-1024" in body


def test_questions_and_answers_are_always_published():
    body = render_body(thread(1))

    assert "<h2>Вопрос оператора</h2>" in body
    assert "<h2>Ответ агента</h2>" in body
    assert "<h2>Расход токенов по треду</h2>" in body
