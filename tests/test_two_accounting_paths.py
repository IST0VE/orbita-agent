"""
Задача 3.1, вторая половина: два пути учёта обязаны считать одно и то же.

Редьюсер в состоянии графа даёт разбивку по треду, которая переживает
перезапуск. `CostMeter` как callback handler даёт ту же разбивку, но работает
на любом графе. Разойтись они не должны: расхождение означало бы, что один из
путей перестал видеть часть вызовов, — и понять, какой отчёт верный, будет
уже не по чему.
"""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent.graph import build_graph, estimate_cost
from costmeter import CostMeter

CONFIG_BASE = {"configurable": {"thread_id": "t-1"}}


def usage_meta(hit, miss, output, write=0):
    return {
        "token_usage": {
            "prompt_cache_hit_tokens": hit,
            "prompt_cache_miss_tokens": miss,
            "prompt_tokens": hit + miss + write,
            "completion_tokens": output,
        }
    }


ASKS_FOR_TOOL = AIMessage(
    content="",
    tool_calls=[
        {"name": "read_task_file", "args": {"name": "встреча.md"}, "id": "call-1"}
    ],
    response_metadata=usage_meta(1472, 128, 30),
)
STAGE = AIMessage(
    content="Документ этапа.",
    response_metadata=usage_meta(1600, 64, 210),
)

# Ход конвейера — это пять вызовов модели подряд, по одному на роль. Оба пути
# учёта обязаны увидеть все пять, поэтому подделке скармливается ровно столько.
PIPELINE = [STAGE] * 5


@pytest.fixture(autouse=True)
def no_publishing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")


def run(*messages, turns=1):
    """Прогнать тред обоими путями учёта сразу."""
    meter = CostMeter(provider="deepseek", model="deepseek-v4-flash")
    model = GenericFakeChatModel(messages=iter(messages))
    app = build_graph(llm=model).compile(checkpointer=InMemorySaver())
    config = {**CONFIG_BASE, "callbacks": [meter]}

    result = {}
    for i in range(turns):
        result = app.invoke(
            {"messages": [HumanMessage(f"вопрос {i + 1}")]}, config=config
        )
    return result, meter


def test_counters_match_over_the_whole_pipeline():
    result, meter = run(*PIPELINE)

    assert result["usage"]["calls"] == 5
    assert meter.usage.as_dict() == result["usage"]


def test_counters_match_across_a_tool_loop():
    """Лишний вызов внутри этапа виден обоим путям так же, как сами этапы."""
    result, meter = run(ASKS_FOR_TOOL, *PIPELINE)

    assert result["usage"]["calls"] == 6
    assert meter.usage.as_dict() == result["usage"]


def test_counters_match_across_several_turns():
    result, meter = run(*(PIPELINE * 3), turns=3)

    assert result["usage"]["calls"] == 15
    assert meter.usage.as_dict() == result["usage"]


def test_cost_matches_too():
    """
    Мало сложить одинаковые токены — деньги тоже должны сойтись. Оба пути
    берут тариф из одной таблицы, и это ровно то, что здесь проверяется.
    """
    result, meter = run(ASKS_FOR_TOOL, *PIPELINE)

    assert meter.cost == pytest.approx(estimate_cost(result["usage"]))


def test_hit_rate_matches():
    from agent.graph import hit_rate

    result, meter = run(ASKS_FOR_TOOL, *PIPELINE)

    assert meter.usage.hit_rate == pytest.approx(hit_rate(result["usage"]))


def test_env_override_reaches_both_paths(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PRICE_CACHE_MISS_PER_MTOK", "50")

    result, meter = run(*PIPELINE)

    assert meter.cost == pytest.approx(estimate_cost(result["usage"]))
    assert meter.cost > 0
