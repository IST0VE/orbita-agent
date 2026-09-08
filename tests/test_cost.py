"""
Задача 1.3: деньги — `estimate_cost` и `format_usage`.

Цены читаются из окружения на каждом вызове, а не при импорте: тарифы меняются
чаще, чем перезапускается сервер. Тест это и фиксирует — подставляет свои цены
через monkeypatch и проверяет арифметику на круглых числах.
"""

from __future__ import annotations

import pytest

from agent import config as cfg
from agent.graph import cost_summary, estimate_cost, format_usage, naive_cost

MILLION = 1_000_000


@pytest.fixture
def prices(monkeypatch: pytest.MonkeyPatch):
    """Круглые цены: $1 / $10 / $100 за 1M токенов."""

    def apply(hit: float, miss: float, output: float) -> None:
        monkeypatch.setenv("PRICE_CACHE_HIT_PER_MTOK", str(hit))
        monkeypatch.setenv("PRICE_CACHE_MISS_PER_MTOK", str(miss))
        monkeypatch.setenv("PRICE_OUTPUT_PER_MTOK", str(output))

    return apply


def test_cost_is_sum_of_three_lines(prices):
    prices(1, 10, 100)
    usage = {"cache_hit": MILLION, "cache_miss": MILLION, "output": MILLION, "calls": 3}
    assert estimate_cost(usage) == pytest.approx(111.0)


def test_calls_are_not_priced(prices):
    """`calls` лежит в том же словаре, но денег не стоит."""
    prices(1, 10, 100)
    assert estimate_cost({"calls": 42}) == pytest.approx(0.0)


def test_missing_counters_are_zero(prices):
    prices(1, 10, 100)
    assert estimate_cost({}) == pytest.approx(0.0)
    assert estimate_cost({"output": MILLION}) == pytest.approx(100.0)


def test_price_change_applies_within_one_process(prices):
    """
    Критерий приёмки задачи: смена PRICE_* между двумя вызовами меняет
    результат в том же процессе. Если цены уедут в константы модуля — упадёт.
    """
    usage = {"cache_hit": MILLION}

    prices(1, 10, 100)
    before = estimate_cost(usage)

    prices(2, 10, 100)
    after = estimate_cost(usage)

    assert before == pytest.approx(1.0)
    assert after == pytest.approx(2.0)


def test_cache_hit_is_cheaper_than_miss(prices):
    """Смысл всего проекта одной строкой: то же число токенов из кеша дешевле."""
    prices(1, 10, 100)
    assert estimate_cost({"cache_hit": MILLION}) < estimate_cost({"cache_miss": MILLION})


def test_format_usage_on_empty_input_does_not_divide_by_zero(prices):
    """Нулевой вход: hit rate не должен ронять форматирование."""
    prices(1, 10, 100)
    out = format_usage({"cache_hit": 0, "cache_miss": 0, "output": 0, "calls": 0})
    assert "cache hit rate" in out
    assert "0.0%" in out
    assert "$0.000000" in out


def test_format_usage_shows_saving_against_naive_price(prices):
    """
    Наивная цена считает весь вход по тарифу miss. При ненулевом кеше она
    обязана быть строго больше фактической — иначе витрина в README врёт.
    """
    prices(1, 10, 100)
    usage = {"cache_hit": MILLION, "cache_miss": MILLION, "output": 0, "calls": 1}

    out = format_usage(usage)

    # фактическая 1 + 10 = $11, наивная 2M * $10 = $20
    assert "$11.000000" in out
    assert "$20.000000" in out
    assert "50.0%" in out


def test_format_usage_counts_input_as_hit_plus_miss(prices):
    prices(1, 10, 100)
    out = format_usage({"cache_hit": 300, "cache_miss": 100, "output": 7, "calls": 2})
    assert "вход 400 ток." in out
    assert "выход 7 ток." in out
    assert "вызовов LLM: 2" in out
    assert "75.0%" in out


# --------------------------------------------------------------------------
# Задача 2.3: запись в кеш как четвёртая статья
# --------------------------------------------------------------------------
@pytest.fixture
def anthropic_prices(monkeypatch: pytest.MonkeyPatch):
    """Форма тарифа Anthropic: запись в кеш дороже обычного входа."""
    monkeypatch.setenv("PRICE_CACHE_HIT_PER_MTOK", "1")
    monkeypatch.setenv("PRICE_CACHE_MISS_PER_MTOK", "10")
    monkeypatch.setenv("PRICE_CACHE_WRITE_PER_MTOK", "12.5")
    monkeypatch.setenv("PRICE_OUTPUT_PER_MTOK", "100")


def test_cache_write_is_charged_at_its_own_price(anthropic_prices):
    assert estimate_cost({"cache_write": MILLION}) == pytest.approx(12.5)


def test_report_no_longer_understates_the_bill(anthropic_prices):
    """
    Раньше записанные в кеш токены либо терялись, либо шли по цене miss.
    Теперь сумма по статьям сходится с тарифом провайдера.
    """
    usage = {"cache_hit": MILLION, "cache_miss": MILLION, "cache_write": MILLION}

    assert estimate_cost(usage) == pytest.approx(1 + 10 + 12.5)


def test_cache_write_counts_as_input(anthropic_prices):
    out = format_usage(
        {"cache_hit": 100, "cache_miss": 50, "cache_write": 850, "output": 10, "calls": 1}
    )
    assert "вход 1000 ток." in out
    assert "записано в кеш 850" in out
    assert "10.0%" in out  # 100 из 1000


def test_cache_write_line_is_hidden_when_zero(prices):
    """На DeepSeek такой статьи нет — вывод не должен обрастать нулями."""
    prices(1, 10, 100)
    out = format_usage({"cache_hit": 100, "cache_miss": 50, "output": 10, "calls": 1})

    assert "записано в кеш" not in out
    assert "вход 150 ток." in out


def test_switching_model_changes_the_price_without_touching_env(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    Задача 3.2 со стороны агента: смена LLM_MODEL даёт другой тариф без правки
    .env. Модель из таблицы — свои числа, модель не из таблицы — предупреждение
    и нули, а не молча унаследованный тариф предыдущей.
    """
    assert cfg.price_per_mtok()["cache_miss"] == pytest.approx(0.44)

    monkeypatch.setenv("LLM_MODEL", "модель-которой-нет")
    with pytest.warns(UserWarning, match="не найден"):
        assert cfg.price_per_mtok()["cache_miss"] == pytest.approx(0.0)


def test_usage_table_shows_cache_write_only_when_present(anthropic_prices):
    from agent.graph import _usage_table

    with_write = _usage_table({"cache_hit": 10, "cache_write": 5, "calls": 1})
    without_write = _usage_table({"cache_hit": 10, "calls": 1})

    assert "Записано в кеш, токенов" in with_write
    assert "Записано в кеш, токенов" not in without_write


# --------------------------------------------------------------------------
# Сводка для интерфейсов
#
# Тариф остаётся на сервере, а браузер получает уже посчитанные деньги. Значит,
# сводка обязана сходиться с тем, что печатает консоль: иначе за один и тот же
# тред на экране и в логе будут разные суммы.
# --------------------------------------------------------------------------
def test_cost_summary_agrees_with_the_console(prices):
    prices(1, 10, 100)
    usage = {"cache_hit": MILLION, "cache_miss": MILLION, "output": MILLION, "calls": 3}

    summary = cost_summary(usage)

    assert summary["usd"] == pytest.approx(estimate_cost(usage))
    assert summary["naive_usd"] == pytest.approx(naive_cost(usage))
    assert summary["hit_rate"] == pytest.approx(50.0)
    assert summary["input"] == 2 * MILLION
    assert summary["calls"] == 3


def test_cost_summary_carries_the_thread_limit(prices, monkeypatch: pytest.MonkeyPatch):
    """По лимиту интерфейс рисует, сколько бюджета уже съедено."""
    prices(1, 10, 100)
    monkeypatch.setenv("BUDGET_USD_PER_THREAD", "0.5")

    assert cost_summary({})["limit_usd"] == pytest.approx(0.5)


def test_cost_summary_reports_no_limit_as_zero(prices, monkeypatch: pytest.MonkeyPatch):
    """0 в BUDGET_USD_PER_THREAD означает «без лимита»; так и отдаём наружу."""
    prices(1, 10, 100)
    monkeypatch.setenv("BUDGET_USD_PER_THREAD", "0")

    assert cost_summary({})["limit_usd"] == 0


def test_cost_summary_on_empty_thread_does_not_divide_by_zero(prices):
    prices(1, 10, 100)

    summary = cost_summary({})

    assert summary["usd"] == pytest.approx(0.0)
    assert summary["hit_rate"] == pytest.approx(0.0)
