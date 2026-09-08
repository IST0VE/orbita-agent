"""
Задача 2.5: подрезка истории.

Кеш держит префикс, но сам вход растёт с каждым ходом: на длинном треде
платишь по цене cache hit, зато за всё больший объём. Тримминг ставит
стоимость хода на полку — ценой того, что модель перестаёт видеть начало
разговора. Поэтому по умолчанию выключен.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from agent.graph import trim_history


def history(turns: int) -> list:
    messages = []
    for i in range(1, turns + 1):
        messages.append(HumanMessage(f"Вопрос {i}. " + "довольно длинный текст " * 20))
        messages.append(AIMessage(f"Ответ {i}. " + "тоже длинный текст " * 20))
    return messages


def test_disabled_by_default():
    messages = history(20)
    assert trim_history(messages) is messages


def test_zero_means_no_trimming(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_MAX_HISTORY_TOKENS", "0")
    messages = history(20)
    assert trim_history(messages) is messages


def test_history_is_cut_to_the_limit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_MAX_HISTORY_TOKENS", "400")

    trimmed = trim_history(history(20))

    assert count_tokens_approximately(trimmed) <= 400
    assert len(trimmed) < 40


def test_the_newest_turns_survive(monkeypatch: pytest.MonkeyPatch):
    """Срезается начало разговора, а не конец: последний вопрос нужен всегда."""
    monkeypatch.setenv("LLM_MAX_HISTORY_TOKENS", "400")

    trimmed = trim_history(history(20))

    assert "Вопрос 20" in trimmed[-2].content
    assert "Вопрос 1." not in trimmed[0].content


def test_trimmed_history_starts_with_a_question(monkeypatch: pytest.MonkeyPatch):
    """
    Обрезать посреди пары «вызов инструмента — ответ» нельзя: провайдер
    отклонит запрос, в котором ToolMessage не на что сослаться.
    """
    monkeypatch.setenv("LLM_MAX_HISTORY_TOKENS", "300")

    messages = []
    for i in range(1, 11):
        messages += [
            HumanMessage(f"Вопрос {i} " + "длинный " * 20),
            AIMessage(
                content="",
                tool_calls=[{"name": "get_sync_status", "args": {}, "id": f"c{i}"}],
            ),
            ToolMessage(content="ответ системы", tool_call_id=f"c{i}", name="t"),
            AIMessage(f"Ответ {i} " + "длинный " * 20),
        ]

    trimmed = trim_history(messages)

    assert trimmed[0].type == "human"
    ids = {m.tool_call_id for m in trimmed if isinstance(m, ToolMessage)}
    called = {
        call["id"]
        for m in trimmed
        if isinstance(m, AIMessage)
        for call in (m.tool_calls or [])
    }
    assert ids <= called


def test_input_stops_growing_with_the_thread(monkeypatch: pytest.MonkeyPatch):
    """
    Смысл задачи в одной проверке: без лимита вход растёт вместе с тредом,
    с лимитом выходит на полку.
    """
    monkeypatch.setenv("LLM_MAX_HISTORY_TOKENS", "0")
    grows_10 = count_tokens_approximately(trim_history(history(10)))
    grows_40 = count_tokens_approximately(trim_history(history(40)))
    assert grows_40 > grows_10 * 3

    monkeypatch.setenv("LLM_MAX_HISTORY_TOKENS", "500")
    flat_10 = count_tokens_approximately(trim_history(history(10)))
    flat_40 = count_tokens_approximately(trim_history(history(40)))
    assert flat_10 <= 500
    assert flat_40 <= 500
