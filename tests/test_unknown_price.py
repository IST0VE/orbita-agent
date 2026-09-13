"""
R7: неизвестная стоимость обязана выглядеть неизвестной.

Тариф, которого нет, превращался в `$0`, а ноль всегда меньше лимита — и
денежные ворота пропускали каждый следующий вызов при формально включённом
`BUDGET_USD_PER_THREAD`. Второе: накопленный расход считался умножением
итоговых счётчиков на текущую цену, поэтому смена модели переоценивала всю
историю треда задним числом.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent import config as cfg
from agent import roles
from agent.cost import charge, cost_summary, price_state, spent_usd, unpriced_calls
from agent.routes import budget_gate, over_budget_node
from agent.state import _merge_spend

KNOWN_MODEL = cfg.DEFAULT_MODEL
CALL = {"cache_hit": 1000, "cache_miss": 1000, "output": 1000, "calls": 1}


@pytest.fixture
def unknown_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_MODEL", "модель-которой-нет")
    return "модель-которой-нет"


@pytest.fixture
def partial_price(monkeypatch: pytest.MonkeyPatch):
    """Задана одна статья из четырёх: половина цены — это не цена."""
    monkeypatch.setenv("LLM_MODEL", "модель-которой-нет")
    monkeypatch.setenv("PRICE_OUTPUT_PER_MTOK", "1.0")


# --------------------------------------------------------------------------
# Признак тарифа доезжает до состояния
# --------------------------------------------------------------------------
def test_a_known_tariff_is_reported_complete_with_its_source():
    price = price_state()

    assert price["known"] and price["complete"]
    assert price["missing"] == []
    assert price["source"].startswith("prices.toml")
    assert price["version"]
    assert price["model"] == KNOWN_MODEL


def test_an_unknown_tariff_is_reported_as_unknown(unknown_model):
    with pytest.warns(UserWarning):
        price = price_state()

    assert not price["known"]
    assert not price["complete"]
    assert sorted(price["missing"]) == ["cache_read", "cache_write", "input", "output"]


def test_a_partial_tariff_is_not_a_tariff(partial_price):
    price = price_state()

    assert price["known"]
    assert not price["complete"]
    assert "output" not in price["missing"]
    assert "input" in price["missing"]


def test_the_summary_carries_the_tariff_to_the_interface():
    summary = cost_summary(CALL, charge(CALL))

    assert summary["price"]["complete"]
    assert summary["unpriced_calls"] == 0


# --------------------------------------------------------------------------
# Денежные ворота
# --------------------------------------------------------------------------
def test_an_unknown_tariff_does_not_slip_through_a_money_limit(unknown_model, monkeypatch):
    monkeypatch.setenv("BUDGET_USD_PER_THREAD", "1.0")

    with pytest.warns(UserWarning):
        assert budget_gate({"usage": {}, "spend": {}}) == "over_budget"


def test_a_partial_tariff_does_not_slip_through_either(partial_price, monkeypatch):
    monkeypatch.setenv("BUDGET_USD_PER_THREAD", "1.0")

    assert budget_gate({"usage": {}, "spend": {}}) == "over_budget"


def test_the_refusal_says_the_tariff_is_missing_not_the_money(unknown_model, monkeypatch):
    monkeypatch.setenv("BUDGET_USD_PER_THREAD", "1.0")

    with pytest.warns(UserWarning):
        text = over_budget_node({"usage": {}, "spend": {}}, roles.PIPELINE)["messages"][0].content

    assert "тариф модели неизвестен" in text
    assert "BUDGET_UNKNOWN_PRICE=allow" in text
    assert "Бюджет треда исчерпан" not in text


def test_an_explicit_mode_without_money_control_is_allowed(unknown_model, monkeypatch):
    monkeypatch.setenv("BUDGET_USD_PER_THREAD", "1.0")
    monkeypatch.setenv("BUDGET_UNKNOWN_PRICE", "allow")

    with pytest.warns(UserWarning):
        assert budget_gate({"usage": {}, "spend": {}}) == "agent"


def test_an_unknown_mode_is_a_configuration_error(monkeypatch):
    monkeypatch.setenv("BUDGET_UNKNOWN_PRICE", "ignore")

    with pytest.raises(cfg.ConfigError, match="BUDGET_UNKNOWN_PRICE"):
        cfg.budget_unknown_price()


def test_a_known_tariff_still_closes_the_gate_on_spend(monkeypatch):
    monkeypatch.setenv("BUDGET_USD_PER_THREAD", "0.000001")

    assert budget_gate({"usage": CALL, "spend": {"usd": 1.0}}) == "over_budget"


def test_a_known_tariff_lets_a_cheap_thread_through(monkeypatch):
    monkeypatch.setenv("BUDGET_USD_PER_THREAD", "10.0")

    assert budget_gate({"usage": CALL, "spend": {"usd": 0.0001}}) == "agent"


def test_step_and_history_limits_do_not_depend_on_the_tariff(unknown_model, monkeypatch):
    """Ограничения, которые не про деньги, работают в обоих режимах."""
    monkeypatch.setenv("LLM_MAX_HISTORY_TOKENS", "500")
    monkeypatch.setenv("TOOL_TURNS_PER_RUN", "2")

    assert cfg.max_history_tokens() == 500
    assert cfg.tool_turns_per_run() == 2


# --------------------------------------------------------------------------
# Накопление
# --------------------------------------------------------------------------
def test_money_is_accumulated_per_call():
    first = charge(CALL)
    total = _merge_spend(None, first)
    total = _merge_spend(total, charge(CALL))

    assert total["usd"] == pytest.approx(first["usd"] * 2)
    assert total["priced_calls"] == 2


def test_changing_the_model_does_not_reprice_the_past(monkeypatch: pytest.MonkeyPatch):
    spend = _merge_spend(None, charge(CALL))
    before = spend["usd"]
    assert before > 0

    monkeypatch.setenv("PRICE_CACHE_HIT_PER_MTOK", "1000.0")
    monkeypatch.setenv("PRICE_CACHE_MISS_PER_MTOK", "1000.0")
    monkeypatch.setenv("PRICE_OUTPUT_PER_MTOK", "1000.0")
    monkeypatch.setenv("PRICE_CACHE_WRITE_PER_MTOK", "1000.0")

    assert spent_usd({"usage": CALL, "spend": spend}) == before
    # Новый вызов оценивается новым тарифом и добавляется к прежней сумме.
    after = _merge_spend(spend, charge(CALL))
    assert after["usd"] > before


def test_an_unpriced_call_is_counted_not_valued(unknown_model):
    with pytest.warns(UserWarning):
        money = charge(CALL)
    spend = _merge_spend(None, money)

    assert spend["usd"] == 0.0
    assert unpriced_calls({"spend": spend}) == 1
    assert spend["priced_calls"] == 0


def test_an_old_checkpoint_without_accumulation_still_reports_a_number():
    """Чекпоинты до накопления: счётчики есть, денег нет — оценка по ним."""
    assert spent_usd({"usage": CALL}) > 0


def test_a_pipeline_turn_accumulates_money_in_state(monkeypatch: pytest.MonkeyPatch):
    from agent.graph import build_graph

    monkeypatch.setenv("MEMORY_ENABLED", "0")
    answer = AIMessage(
        content="Ответ",
        response_metadata={"token_usage": {"prompt_cache_hit_tokens": 100,
                                           "prompt_cache_miss_tokens": 10,
                                           "completion_tokens": 5}},
    )

    class Once:
        def invoke(self, messages):
            return answer

    result = build_graph(llm=Once()).compile().invoke(
        {"messages": [HumanMessage("вопрос")]}, config={"configurable": {"thread_id": "t-1"}}
    )

    assert result["spend"]["priced_calls"] == len(roles.KEYS)
    assert result["cost"]["usd"] == pytest.approx(result["spend"]["usd"])
    assert result["cost"]["price"]["complete"]
