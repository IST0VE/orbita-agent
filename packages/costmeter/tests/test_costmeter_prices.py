"""
Задача 3.2: таблица цен вместо трёх переменных.

Три PRICE_* хороши ровно для одной модели. Тариф ищется по паре
(провайдер, модель) в `prices.toml`, а переменные окружения остаются как
переопределение поверх таблицы.

Тесты пакета не знают про агента: связка «сменил LLM_MODEL — получил другой
тариф» проверяется на его стороне, в `tests/test_cost.py`.
"""

from __future__ import annotations

import pytest

from costmeter import PriceError, Prices, Usage, captured_at, for_model
from costmeter.prices import table

MILLION = 1_000_000
KNOWN = ("deepseek", "deepseek-v4-flash")


# --------------------------------------------------------------------------
# Таблица
# --------------------------------------------------------------------------
def test_table_has_a_capture_date():
    """Цены устаревают, и это должно быть видно, а не подразумеваться."""
    assert captured_at() != "неизвестно"
    assert table()["meta"]["captured"]


def test_known_model_comes_from_the_table():
    prices = for_model(*KNOWN)

    assert prices.known
    assert "prices.toml" in prices.source
    assert prices.cache_read == pytest.approx(0.014)
    assert prices.input == pytest.approx(0.44)
    assert prices.output == pytest.approx(1.32)
    assert table()["meta"]["policy"] == "peak"


# --------------------------------------------------------------------------
# Неизвестная модель
# --------------------------------------------------------------------------
def test_unknown_model_warns_and_costs_nothing():
    with pytest.warns(UserWarning, match="prices.toml"):
        prices = for_model("openai", "модель-которой-нет")

    assert not prices.known
    assert prices.per_counter() == {
        "cache_hit": 0.0,
        "cache_miss": 0.0,
        "cache_write": 0.0,
        "output": 0.0,
    }


def test_warning_names_the_way_out():
    """Предупреждение бесполезно, если не говорит, что делать."""
    with pytest.warns(UserWarning) as caught:
        for_model("openai", "модель-которой-нет")

    text = str(caught[0].message)
    assert "prices.toml" in text
    assert "PRICE_CACHE_MISS_PER_MTOK" in text


def test_env_alone_is_enough_and_does_not_warn(monkeypatch: pytest.MonkeyPatch):
    """Тариф задан переменными — таблица не нужна и предупреждать не о чем."""
    monkeypatch.setenv("PRICE_CACHE_MISS_PER_MTOK", "3")
    monkeypatch.setenv("PRICE_OUTPUT_PER_MTOK", "6")

    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        prices = for_model("openai", "модель-которой-нет")

    assert prices.known
    assert prices.source == "окружение"
    assert prices.input == pytest.approx(3.0)


# --------------------------------------------------------------------------
# Переопределение
# --------------------------------------------------------------------------
def test_env_overrides_the_table(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PRICE_CACHE_MISS_PER_MTOK", "99")

    prices = for_model(*KNOWN)

    assert prices.input == pytest.approx(99.0)
    assert prices.cache_read == pytest.approx(0.014)  # не тронуто
    assert "окружение" in prices.source


def test_env_can_be_switched_off():
    """Отчёту иногда нужен именно табличный тариф, без стендовых правок."""
    prices = for_model(*KNOWN, use_env=False)
    assert prices.source == f"prices.toml ({captured_at()})"


def test_broken_env_price_is_an_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PRICE_OUTPUT_PER_MTOK", "дёшево")
    with pytest.raises(PriceError, match="PRICE_OUTPUT_PER_MTOK"):
        for_model(*KNOWN)


@pytest.mark.parametrize("value", ["-1", "nan", "inf"])
def test_invalid_env_price_is_an_error(monkeypatch: pytest.MonkeyPatch, value: str):
    monkeypatch.setenv("PRICE_OUTPUT_PER_MTOK", value)
    with pytest.raises(PriceError, match="PRICE_OUTPUT_PER_MTOK"):
        for_model(*KNOWN)


# --------------------------------------------------------------------------
# Счёт
# --------------------------------------------------------------------------
def test_cost_sums_four_articles():
    prices = Prices(input=10, cache_read=1, cache_write=12.5, output=100)
    usage = Usage(cache_hit=MILLION, cache_miss=MILLION, cache_write=MILLION)

    assert prices.cost(usage) == pytest.approx(1 + 10 + 12.5)


def test_naive_cost_prices_all_input_as_miss():
    prices = Prices(input=10, cache_read=1, output=100)
    usage = Usage(cache_hit=MILLION, cache_miss=MILLION)

    assert prices.naive_cost(usage) == pytest.approx(20.0)
    assert prices.cost(usage) < prices.naive_cost(usage)


def test_cost_accepts_a_plain_dict():
    """Состояние графа хранит счётчики словарём, а не датаклассом."""
    prices = Prices(input=10, output=100)
    assert prices.cost({"cache_miss": MILLION}) == pytest.approx(10.0)
