"""
Задача 2.8: редьюсер `usage` не должен падать на чужом поле.

Ключи объединяются, а не берутся из фиксированного списка — провайдер или
новая версия langchain может принести свой счётчик. Обратная сторона: в usage
может приехать строка или словарь, на которых сложение упало бы, а вместе
с ним — весь ход агента.
"""

from __future__ import annotations

from agent.graph import _merge_usage


def test_counters_are_summed():
    merged = _merge_usage(
        {"cache_hit": 100, "cache_miss": 20, "calls": 1},
        {"cache_hit": 50, "cache_miss": 5, "calls": 1},
    )
    assert merged["cache_hit"] == 150
    assert merged["cache_miss"] == 25
    assert merged["calls"] == 2


def test_new_key_from_the_provider_is_kept():
    merged = _merge_usage({"calls": 1}, {"calls": 1, "reasoning_tokens": 42})
    assert merged["reasoning_tokens"] == 42


def test_non_numeric_field_does_not_break_the_reducer():
    merged = _merge_usage(
        {"calls": 1, "model": "deepseek-v4-flash"},
        {"calls": 1, "model": "deepseek-v4-pro"},
    )
    assert merged["calls"] == 2
    assert merged["model"] == "deepseek-v4-pro"


def test_non_numeric_arriving_later_wins():
    merged = _merge_usage({"calls": 1}, {"details": {"cache": "warm"}})
    assert merged["details"] == {"cache": "warm"}


def test_non_numeric_only_in_old_is_preserved():
    merged = _merge_usage({"calls": 1, "model": "m-1"}, {"calls": 1})
    assert merged["model"] == "m-1"


def test_booleans_are_not_added_up():
    """bool — подкласс int, но True + True = 2 здесь ничего не значит."""
    merged = _merge_usage({"cached": True}, {"cached": False})
    assert merged["cached"] is False


def test_empty_update_returns_the_accumulator():
    accumulated = {"calls": 3}
    assert _merge_usage(accumulated, None) is accumulated
    assert _merge_usage(accumulated, {}) is accumulated


def test_first_update_starts_from_zeros():
    merged = _merge_usage(None, {"cache_hit": 10, "calls": 1})
    assert merged["cache_hit"] == 10
    assert merged["cache_write"] == 0
    assert merged["output"] == 0
