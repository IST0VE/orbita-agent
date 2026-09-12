"""
Задача 3.4: бюджет на тред.

Учёт, который только отчитывается постфактум, — половина ценности. Вторая
половина: при превышении граф уходит в конец с честным сообщением вместо
следующего вызова модели.

Ворота стоят на входе в тред, в цикле с инструментами и перед каждым этапом
конвейера: у пяти ролей пять поводов потратить деньги, и остановиться нужно
уметь на любом из них.
"""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent.graph import budget_gate, build_graph

CONFIG = {"configurable": {"thread_id": "t-1"}}

# Ход стоит заметных денег: 1M пересчитанных токенов по тарифу из таблицы.
EXPENSIVE = 1_000_000


def answer(text="Ответ оператору", miss=EXPENSIVE, hit=0, output=0):
    return AIMessage(
        content=text,
        response_metadata={
            "token_usage": {
                "prompt_cache_hit_tokens": hit,
                "prompt_cache_miss_tokens": miss,
                "completion_tokens": output,
            }
        },
    )


def asks_for_tool(call_id="call-1", text=""):
    # `text` — то, что роль успела написать рядом с вопросом к инструменту.
    # На последнем ходе вызов снимается, и написанное становится документом
    # этапа: именно так конвейер выпускает бумаги по неполному материалу.
    return AIMessage(
        content=text,
        tool_calls=[{"name": "read_task_file", "args": {"name": "встреча.md"}, "id": call_id}],
        response_metadata={
            "token_usage": {
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": EXPENSIVE,
                "completion_tokens": 0,
            }
        },
    )


@pytest.fixture(autouse=True)
def no_publishing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")
    # Цель задана явно: с фазы 4 `auto` без реквизитов Confluence уводит
    # документ на диск, а вместе с ним меняется и разметка (Markdown).
    monkeypatch.setenv("PUBLISH_TARGET", "confluence")


def thread(*messages):
    """Компилируем с чекпоинтером: бюджет живёт на треде, а не на вызове."""
    model = GenericFakeChatModel(messages=iter(messages))
    return build_graph(llm=model).compile(checkpointer=InMemorySaver())


# --------------------------------------------------------------------------
# Ворота
# --------------------------------------------------------------------------
def test_no_limit_by_default():
    assert budget_gate({"usage": {"cache_miss": EXPENSIVE * 100}}) == "agent"


def test_gate_lets_through_while_there_is_money(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BUDGET_USD_PER_THREAD", "1")
    assert budget_gate({"usage": {"cache_miss": 10}}) == "agent"


def test_gate_stops_when_the_limit_is_reached(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BUDGET_USD_PER_THREAD", "0.01")
    # 1M пересчитанных токенов по пиковым $0.44 за 1M — это $0.44, лимит пройден.
    assert budget_gate({"usage": {"cache_miss": EXPENSIVE}}) == "over_budget"


def test_empty_usage_is_within_any_limit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BUDGET_USD_PER_THREAD", "0.000001")
    assert budget_gate({}) == "agent"


# --------------------------------------------------------------------------
# Полный тред
# --------------------------------------------------------------------------
def test_pipeline_stops_between_stages_and_spends_nothing_more(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    Критерий приёмки: копеечный лимит останавливает конвейер после первого же
    этапа. Второй элемент последовательности фейковой модели остаётся
    неизрасходованным — значит, вызова не было.

    Раньше этот тест ловил границу между ходами. Теперь ход — это пять вызовов
    подряд, и ловить надо границу между этапами: именно там дорогой конвейер
    сжигает бюджет, не приходя в сознание.
    """
    monkeypatch.setenv("BUDGET_USD_PER_THREAD", "0.01")
    unused = answer("Этот ответ не должен прозвучать")
    app = thread(answer("Требования"), unused)

    result = app.invoke({"messages": [HumanMessage("задача")]}, config=CONFIG)

    assert result["artifacts"] == {"requirements": "Требования"}
    assert "Бюджет треда исчерпан" in result["messages"][-1].content
    assert result["usage"]["calls"] == 1


def test_next_turn_of_a_spent_thread_does_not_call_the_model(
    monkeypatch: pytest.MonkeyPatch,
):
    """Ворота на входе в тред: потраченный бюджет переживает конец хода."""
    monkeypatch.setenv("BUDGET_USD_PER_THREAD", "0.01")
    app = thread(answer("Требования"), answer("Не должно прозвучать"))

    first = app.invoke({"messages": [HumanMessage("задача")]}, config=CONFIG)
    second = app.invoke({"messages": [HumanMessage("ещё задача")]}, config=CONFIG)

    assert "Бюджет треда исчерпан" in second["messages"][-1].content
    assert second["usage"]["calls"] == first["usage"]["calls"] == 1
    assert second["usage"]["cache_miss"] == first["usage"]["cache_miss"]


def test_stop_message_names_the_variable_and_the_numbers(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("BUDGET_USD_PER_THREAD", "0.01")
    app = thread(answer("Первый ответ"), answer("Второй"))
    app.invoke({"messages": [HumanMessage("первый")]}, config=CONFIG)

    text = app.invoke({"messages": [HumanMessage("второй")]}, config=CONFIG)["messages"][-1].content

    assert "BUDGET_USD_PER_THREAD" in text
    assert "$0.010000" in text
    assert "$0.440000" in text


def test_thread_still_reaches_the_end(monkeypatch: pytest.MonkeyPatch):
    """Граф уходит в конец, а не падает: документ по треду всё равно собран."""
    monkeypatch.setenv("BUDGET_USD_PER_THREAD", "0.01")
    app = thread(answer("Первый ответ"), answer("Второй"))
    app.invoke({"messages": [HumanMessage("первый")]}, config=CONFIG)

    result = app.invoke({"messages": [HumanMessage("второй")]}, config=CONFIG)

    assert result["publication"]["status"] == "disabled"
    assert "<h2>Расход токенов по треду</h2>" in result["document"]


def test_tool_loop_is_gated_too(monkeypatch: pytest.MonkeyPatch):
    """
    Внутри одного хода модель может вызываться несколько раз. Если бы ворота
    стояли только на входе в тред, зациклившийся инструмент тратил бы деньги
    сколько угодно.
    """
    monkeypatch.setenv("BUDGET_USD_PER_THREAD", "0.01")
    unused = answer("Ответ после инструмента")
    app = thread(asks_for_tool(), unused)

    result = app.invoke({"messages": [HumanMessage("вопрос")]}, config=CONFIG)

    # Первый вызов состоялся и попросил инструмент, второго уже не было.
    assert result["usage"]["calls"] == 1
    assert "Бюджет треда исчерпан" in result["messages"][-1].content


def test_without_a_limit_the_pipeline_runs_all_the_way_through():
    """Без лимита ворота не вмешиваются: пять ролей за ход, десять за два."""
    app = thread(*[answer(f"Этап {n}") for n in range(1, 11)])

    app.invoke({"messages": [HumanMessage("первая задача")]}, config=CONFIG)
    result = app.invoke({"messages": [HumanMessage("вторая задача")]}, config=CONFIG)

    assert result["messages"][-1].content == "Этап 10"
    assert result["usage"]["calls"] == 10


# --------------------------------------------------------------------------
# Потолок ходов в инструменты
#
# Ворота бюджета выключены по умолчанию, и тогда у петли «роль → инструменты
# → роль» не остаётся тормоза вообще: она упирается в recursion_limit
# LangGraph — тысячи оплаченных вызовов — и обрывается ошибкой мимо ветки,
# которая публикует сделанное. Поэтому у петли есть собственный потолок,
# не зависящий от денег и включённый по умолчанию.
# --------------------------------------------------------------------------
def test_tool_loop_ends_on_its_own_without_any_budget(monkeypatch: pytest.MonkeyPatch):
    """
    Модель зовёт инструмент на каждом ходе и сама не остановится никогда.

    Конвейер обязан дойти до конца и выпустить документы: последний ход роли
    делается без инструментов, вызов с него снимается, и роутер уводит на
    следующий этап. Без потолка этот тест не кончился бы.
    """
    monkeypatch.delenv("BUDGET_USD_PER_THREAD", raising=False)
    monkeypatch.setenv("TOOL_TURNS_PER_RUN", "3")
    # Поддельная модель про отвязку схем не знает и просит инструмент всегда:
    # проверяется ограничитель, а не сговорчивость модели.
    app = thread(*[asks_for_tool(f"call-{n}", "документ этапа") for n in range(40)])

    result = app.invoke({"messages": [HumanMessage("вопрос")]}, config=CONFIG)

    assert result["tool_turns"] == 3
    # Три хода в инструменты плюс четвёртый, последний, и по одному на
    # остальные четыре роли.
    assert result["usage"]["calls"] == 8
    assert sorted(result["artifacts"]) == ["api", "architecture", "data", "requirements", "review"]


def test_the_tool_ceiling_is_per_run_not_per_thread(monkeypatch: pytest.MonkeyPatch):
    """
    Второй запрос в том же треде получает полный потолок заново.

    Счётчик, который жил бы на треде, оставил бы вторую задачу вовсе без
    инструментов — и роль писала бы документ, не открыв ни одного файла.
    """
    monkeypatch.delenv("BUDGET_USD_PER_THREAD", raising=False)
    monkeypatch.setenv("TOOL_TURNS_PER_RUN", "2")
    app = thread(*[asks_for_tool(f"call-{n}", "документ этапа") for n in range(80)])

    first = app.invoke({"messages": [HumanMessage("первая задача")]}, config=CONFIG)
    assert first["tool_turns"] == 2
    second = app.invoke({"messages": [HumanMessage("вторая задача")]}, config=CONFIG)

    assert second["tool_turns"] == 2
    assert second["usage"]["calls"] == first["usage"]["calls"] * 2


def test_a_zero_ceiling_means_no_ceiling(monkeypatch: pytest.MonkeyPatch):
    """Ноль снимает потолок — как и у бюджета; тормозом остаются ворота."""
    monkeypatch.setenv("TOOL_TURNS_PER_RUN", "0")
    monkeypatch.setenv("BUDGET_USD_PER_THREAD", "0.01")
    app = thread(asks_for_tool(), answer("Ответ после инструмента"))

    result = app.invoke({"messages": [HumanMessage("вопрос")]}, config=CONFIG)

    assert result["usage"]["calls"] == 1
    assert "Бюджет треда исчерпан" in result["messages"][-1].content
