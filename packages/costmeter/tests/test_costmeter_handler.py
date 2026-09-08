"""
Задача 3.1: CostMeter как callback handler.

Главное свойство — независимость от `agent`. Счётчик должен работать на чужом
графе, ничего не зная ни про ноды, ни про состояние. Поэтому здесь граф
собирается прямо в тесте из пяти строк, и `agent` не импортируется вовсе.

Задача 3.5 — вывод наружу: JSONL по вызовам, из которого строится график
без ручной обработки.
"""

from __future__ import annotations

import json
from typing import TypedDict

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from costmeter import CostMeter, Prices, Usage

PRICES = Prices(input=10, cache_read=1, cache_write=12.5, output=100, known=True)


def answer(hit=0, miss=0, output=0, write=0, text="ответ"):
    return AIMessage(
        content=text,
        response_metadata={
            "model_name": "модель-из-ответа",
            "token_usage": {
                "prompt_cache_hit_tokens": hit,
                "prompt_cache_miss_tokens": miss,
                "prompt_tokens": hit + miss + write,
                "completion_tokens": output,
            },
        },
    )


class Chat(TypedDict, total=False):
    messages: list


def foreign_graph(model):
    """Чужой граф: ни одной строки из `agent`."""

    def call(state: Chat) -> dict:
        return {"messages": [model.invoke(state["messages"])]}

    return (
        StateGraph(Chat)
        .add_node("call", call)
        .add_edge(START, "call")
        .add_edge("call", END)
        .compile()
    )


# --------------------------------------------------------------------------
# Работа на чужом графе
# --------------------------------------------------------------------------
def test_meter_counts_calls_of_a_graph_it_knows_nothing_about():
    model = GenericFakeChatModel(
        messages=iter([answer(hit=1000, miss=200, output=50)])
    )
    meter = CostMeter(provider="deepseek", model="deepseek-v4-flash", prices=PRICES)

    foreign_graph(model).invoke(
        {"messages": [HumanMessage("вопрос")]}, config={"callbacks": [meter]}
    )

    assert meter.usage == Usage(cache_hit=1000, cache_miss=200, output=50, calls=1)
    assert meter.cost == pytest.approx(1000 / 1e6 * 1 + 200 / 1e6 * 10 + 50 / 1e6 * 100)


def test_several_calls_are_summed():
    model = GenericFakeChatModel(
        messages=iter([answer(hit=100, miss=10), answer(hit=200, miss=20)])
    )
    meter = CostMeter(prices=PRICES)
    graph = foreign_graph(model)

    graph.invoke({"messages": [HumanMessage("раз")]}, config={"callbacks": [meter]})
    graph.invoke({"messages": [HumanMessage("два")]}, config={"callbacks": [meter]})

    assert meter.usage.calls == 2
    assert meter.usage.cache_hit == 300
    assert len(meter.calls) == 2


def test_naive_cost_and_saving():
    model = GenericFakeChatModel(messages=iter([answer(hit=1000, miss=0)]))
    meter = CostMeter(prices=PRICES)

    foreign_graph(model).invoke(
        {"messages": [HumanMessage("вопрос")]}, config={"callbacks": [meter]}
    )

    assert meter.naive_cost == pytest.approx(1000 / 1e6 * 10)
    assert meter.saved == pytest.approx(meter.naive_cost - meter.cost)


def test_model_name_is_taken_from_the_answer_when_not_given():
    model = GenericFakeChatModel(messages=iter([answer(hit=1)]))
    meter = CostMeter(prices=PRICES)

    foreign_graph(model).invoke(
        {"messages": [HumanMessage("вопрос")]}, config={"callbacks": [meter]}
    )

    assert meter.calls[0].model == "модель-из-ответа"


def test_reset_clears_everything():
    model = GenericFakeChatModel(messages=iter([answer(hit=1)]))
    meter = CostMeter(prices=PRICES)
    foreign_graph(model).invoke(
        {"messages": [HumanMessage("вопрос")]}, config={"callbacks": [meter]}
    )

    meter.reset()

    assert meter.calls == []
    assert meter.usage == Usage()


# --------------------------------------------------------------------------
# Отчёт
# --------------------------------------------------------------------------
def test_report_mentions_hit_rate_cost_and_price_source():
    meter = CostMeter(prices=PRICES)
    meter.calls.append(_call(meter, hit=750, miss=250))

    report = meter.report()

    assert "cache hit rate" in report
    assert "75.0%" in report
    assert "тариф" in report


def test_report_warns_when_the_price_is_unknown():
    with pytest.warns(UserWarning):
        meter = CostMeter(provider="openai", model="модель-которой-нет")
        report = meter.report()

    assert "тариф неизвестен" in report


def test_report_shows_budget_state():
    meter = CostMeter(prices=PRICES, budget_usd=0.000001)
    meter.calls.append(_call(meter, hit=0, miss=1_000_000))

    assert meter.over_budget
    assert "превышен" in meter.report()


def test_as_dict_is_machine_readable():
    meter = CostMeter(prices=PRICES, model="m-1")
    meter.calls.append(_call(meter, hit=900, miss=100, output=10))

    data = meter.as_dict()

    assert data["model"] == "m-1"
    assert data["cache_hit"] == 900
    assert data["hit_rate"] == pytest.approx(90.0)
    assert data["saved_usd"] > 0


# --------------------------------------------------------------------------
# 3.5: JSONL
# --------------------------------------------------------------------------
def test_jsonl_log_has_one_line_per_call(tmp_path):
    log = tmp_path / "calls.jsonl"
    model = GenericFakeChatModel(
        messages=iter([answer(hit=100, miss=10, output=5), answer(hit=200, miss=20)])
    )
    meter = CostMeter(prices=PRICES, log_path=log)
    graph = foreign_graph(model)

    graph.invoke({"messages": [HumanMessage("раз")]}, config={"callbacks": [meter]})
    graph.invoke({"messages": [HumanMessage("два")]}, config={"callbacks": [meter]})

    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["cache_hit"] == 100
    assert first["cache_miss"] == 10
    assert first["output"] == 5
    assert first["cost_usd"] > 0
    assert first["naive_cost_usd"] > first["cost_usd"]
    assert first["at"]


def test_log_is_appended_not_overwritten(tmp_path):
    """Несколько прогонов ложатся в один лог и разбираются по thread_id."""
    log = tmp_path / "calls.jsonl"
    log.write_text('{"старая": "строка"}\n', encoding="utf-8")

    meter = CostMeter(prices=PRICES, log_path=log)
    model = GenericFakeChatModel(messages=iter([answer(hit=1)]))
    foreign_graph(model).invoke(
        {"messages": [HumanMessage("вопрос")]}, config={"callbacks": [meter]}
    )

    assert len(log.read_text(encoding="utf-8").strip().splitlines()) == 2


def test_cost_curve_can_be_built_from_the_log(tmp_path):
    """
    Критерий приёмки 3.5: из лога одного прогона строится график стоимости
    по вызовам без ручной обработки — достаточно прочитать поле cost_usd.
    """
    log = tmp_path / "calls.jsonl"
    meter = CostMeter(prices=PRICES, log_path=log)
    model = GenericFakeChatModel(
        messages=iter([answer(hit=0, miss=1000), answer(hit=900, miss=100)])
    )
    graph = foreign_graph(model)
    for text in ("раз", "два"):
        graph.invoke({"messages": [HumanMessage(text)]}, config={"callbacks": [meter]})

    curve = [
        json.loads(line)["cost_usd"]
        for line in log.read_text(encoding="utf-8").splitlines()
    ]

    assert len(curve) == 2
    assert curve[0] > curve[1]  # кеш прогрелся — ход подешевел


def test_no_log_path_means_no_file(tmp_path):
    meter = CostMeter(prices=PRICES)
    model = GenericFakeChatModel(messages=iter([answer(hit=1)]))
    foreign_graph(model).invoke(
        {"messages": [HumanMessage("вопрос")]}, config={"callbacks": [meter]}
    )

    assert list(tmp_path.iterdir()) == []


def _call(meter: CostMeter, **counters):
    """Собрать запись о вызове напрямую, минуя граф."""
    from costmeter.meter import Call

    usage = Usage(
        cache_hit=counters.get("hit", 0),
        cache_miss=counters.get("miss", 0),
        cache_write=counters.get("write", 0),
        output=counters.get("output", 0),
        calls=1,
    )
    prices = meter.prices_for()
    return Call(
        at="2026-08-28T00:00:00+00:00",
        provider=meter.provider,
        model=meter.model,
        usage=usage,
        cost_usd=prices.cost(usage),
        naive_cost_usd=prices.naive_cost(usage),
    )


# --------------------------------------------------------------------------
# Независимость от agent
# --------------------------------------------------------------------------
def test_costmeter_does_not_import_agent():
    """
    Критерий приёмки 3.1: счётчик работает на чужом графе без импорта `agent`.
    Проверяется в отдельном процессе — в этом `agent` уже импортирован
    соседними тестами.
    """
    import subprocess
    import sys

    code = (
        "import sys, costmeter;"
        "assert not [m for m in sys.modules if m == 'agent' or m.startswith('agent.')],"
        " sorted(m for m in sys.modules if m.startswith('agent'));"
        "print('ok')"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok"
