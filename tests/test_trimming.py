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
from agent.nodes import role_input


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


def test_single_question_history_keeps_the_task_instead_of_emptying(monkeypatch: pytest.MonkeyPatch):
    """
    В расследовании НТ человеческое сообщение одно и стоит первым: окно, которое
    обязано начинаться с него, либо вмещает всю переписку, либо не вмещает ничего.
    Пустая история — вызов модели без задачи и без прочитанного, оплаченный как
    обычный. Режется середина, задача и последние ответы инструментов остаются.
    """
    monkeypatch.setenv("LLM_MAX_HISTORY_TOKENS", "4000")

    messages = [HumanMessage("Бриф расследования. " + "факт " * 1600)]
    for i in range(4):
        messages += [
            AIMessage(content="", tool_calls=[{"name": "prometheus_range_query",
                                               "args": {"metric": "cpu"}, "id": f"c{i}"}]),
            ToolMessage(content=f"ряд {i} " + "точка " * 400, tool_call_id=f"c{i}", name="t"),
        ]

    trimmed = trim_history(messages)

    assert count_tokens_approximately(trimmed) <= 4000
    assert trimmed[0] is messages[0]
    assert "ряд 3" in trimmed[-1].content
    ids = {m.tool_call_id for m in trimmed if isinstance(m, ToolMessage)}
    called = {call["id"] for m in trimmed if isinstance(m, AIMessage) for call in (m.tool_calls or [])}
    assert ids <= called


def research_turn(*pages: int) -> list:
    """
    Ход роли поиска в конвейере подготовки, как 27 сентября 2026: запрос
    оператора, заметка о прочитанной задаче, разбор, список запросов, а за ними
    её собственная переписка с Confluence. `pages` — размер каждой прочитанной
    страницы в словах; слово здесь — около полутора токенов.
    """
    messages = [
        HumanMessage("помоги мне с задачей https://jira.example.com/browse/ORB-1"),
        AIMessage("Прочитана задача ORB-1 «Управление пользователями»."),
        AIMessage("# Разбор задачи\n\n" + "требование " * 300),
        AIMessage("# Что нужно выяснить\n\nЗапрос: пользователи nginx. " + "пробел " * 800),
    ]
    for i, words in enumerate(pages):
        messages += [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "confluence_page", "args": {"page_id": str(i)}, "id": f"p{i}"}
                ],
            ),
            ToolMessage(
                content=f"страница {i} " + "абзац " * words,
                tool_call_id=f"p{i}",
                name="confluence_page",
            ),
        ]
    return messages


def test_the_answer_the_model_asked_for_is_never_cut_away(monkeypatch: pytest.MonkeyPatch):
    """
    Ответ инструмента больше остатка лимита. Окно, отсчитанное от конца, не
    вмещало его и оставалось пустым: роль видела один запрос оператора, звала
    тот же инструмент снова и в конце писала, что ничего не прочитано.
    Лимит здесь уступает: вызов без ответа, ради которого модель звали, — это
    оплаченный вызов, который ничего не даёт.
    """
    monkeypatch.setenv("LLM_MAX_HISTORY_TOKENS", "3000")
    turn = research_turn(2600)

    trimmed = trim_history(turn)

    assert trimmed[0] is turn[0]
    assert trimmed[-2:] == turn[-2:]


def test_the_role_input_is_not_trimmed_away(monkeypatch: pytest.MonkeyPatch):
    """
    Разбор и список запросов для роли поиска — само её задание. Под лимит
    уходит середина её переписки, а не то, ради чего она ищет.
    """
    monkeypatch.setenv("LLM_MAX_HISTORY_TOKENS", "3000")
    turn = research_turn(1000, 1000, 1000)

    trimmed = trim_history(turn, keep=role_input(turn))

    assert trimmed[:4] == turn[:4]
    assert trimmed[-2:] == turn[-2:]
    assert turn[5] not in trimmed, "старая страница уходит первой"
    ids = {m.tool_call_id for m in trimmed if isinstance(m, ToolMessage)}
    called = {
        call["id"] for m in trimmed if isinstance(m, AIMessage) for call in (m.tool_calls or [])
    }
    assert ids <= called


def test_role_input_is_everything_before_its_own_tool_loop():
    turn = research_turn(10, 10)

    assert role_input(turn) == 4
    assert role_input(turn[:4]) == 4
    assert role_input(turn[:1]) == 1
    # Расследование НТ: бриф и сразу ходы модели.
    assert role_input([turn[0], *turn[4:]]) == 1


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
