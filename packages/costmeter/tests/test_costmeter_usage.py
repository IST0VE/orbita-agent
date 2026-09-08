"""
Задача 3.3: единая нормализация usage.

Логика фолбэков переехала из `extract_usage` в таблицу правил по провайдерам.
Смысл таблицы в том, что добавление провайдера — это строка в ней и фикстура
здесь, а не ещё одна ветка в разборе. Поэтому у каждого провайдера свой набор
полей, а проверка одна.
"""

from __future__ import annotations

import pytest

from costmeter import Usage, normalize

# Сырые ответы провайдеров в том виде, в каком они лежат в response_metadata.
DEEPSEEK_RAW = {
    "prompt_cache_hit_tokens": 1472,
    "prompt_cache_miss_tokens": 128,
    "completion_tokens": 210,
    "prompt_tokens": 1600,
}
OPENAI_RAW = {
    "prompt_tokens": 1600,
    "completion_tokens": 210,
    "prompt_tokens_details": {"cached_tokens": 1472},
}
ANTHROPIC_RAW = {
    "input_tokens": 128,
    "cache_read_input_tokens": 1472,
    "cache_creation_input_tokens": 0,
    "output_tokens": 210,
}

EXPECTED = Usage(cache_hit=1472, cache_miss=128, cache_write=0, output=210, calls=1)


@pytest.mark.parametrize(
    ("provider", "raw"),
    [
        ("deepseek", DEEPSEEK_RAW),
        ("openai", OPENAI_RAW),
        ("anthropic", ANTHROPIC_RAW),
    ],
)
def test_every_provider_gives_the_same_four_counters(provider, raw):
    """Один и тот же расход, записанный тремя разными способами."""
    assert normalize(raw, {}, provider) == EXPECTED


@pytest.mark.parametrize(
    ("provider", "raw"),
    [
        ("deepseek", DEEPSEEK_RAW),
        ("openai", OPENAI_RAW),
        ("anthropic", ANTHROPIC_RAW),
    ],
)
def test_provider_can_be_guessed_from_the_fields(provider, raw):
    """Провайдер не назван — правило подбирается по самим полям."""
    assert normalize(raw, {}, None) == EXPECTED


def test_openai_derives_miss_by_subtraction():
    """У OpenAI prompt_tokens включает закешированное, поэтому miss — разность."""
    usage = normalize({"prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 900}}, {})
    assert usage.cache_hit == 900
    assert usage.cache_miss == 100


def test_anthropic_input_tokens_is_already_the_miss():
    """
    У Anthropic input_tokens — это остаток без чтения и записи кеша.
    Вычитать из него ещё раз означало бы потерять токены.
    """
    usage = normalize(
        {
            "input_tokens": 300,
            "cache_read_input_tokens": 1200,
            "cache_creation_input_tokens": 500,
            "output_tokens": 100,
        },
        {},
        "anthropic",
    )
    assert usage.cache_miss == 300
    assert usage.cache_write == 500
    assert usage.input == 2000


def test_sources_complement_each_other():
    """
    Провайдер отдал общий объём входа, но не разбивку по кешу; разбивка есть
    в нормализованных полях LangChain. Правильный ответ собирается из обоих.
    """
    usage = normalize(
        {"prompt_tokens": 1000},
        {"input_tokens": 1000, "output_tokens": 5, "input_token_details": {"cache_read": 900}},
        "deepseek",
    )
    assert usage == Usage(cache_hit=900, cache_miss=100, output=5, calls=1)


def test_normalized_langchain_fields_alone():
    usage = normalize(
        {},
        {
            "input_tokens": 1600,
            "output_tokens": 210,
            "input_token_details": {"cache_read": 1472, "cache_creation": 0},
        },
    )
    assert usage == EXPECTED


def test_no_statistics_is_not_an_exception():
    """Отсутствие usage не повод ронять ход агента."""
    assert normalize(None, None) == Usage(calls=1)
    assert normalize({}, {}, "deepseek") == Usage(calls=1)


def test_cache_write_is_excluded_from_miss():
    usage = normalize(
        {},
        {
            "input_tokens": 1000,
            "output_tokens": 0,
            "input_token_details": {"cache_read": 0, "cache_creation": 1000},
        },
    )
    assert usage.cache_miss == 0
    assert usage.cache_write == 1000


def test_negative_difference_is_clamped():
    usage = normalize({}, {"input_tokens": 100, "input_token_details": {"cache_read": 900}})
    assert usage.cache_miss == 0


def test_garbage_values_are_ignored():
    """Строка вместо числа не должна ни падать, ни попадать в счётчики."""
    usage = normalize({"prompt_cache_hit_tokens": "много", "prompt_tokens": 50}, {})
    assert usage.cache_hit == 0
    assert usage.cache_miss == 50


def test_negative_and_non_finite_counters_are_clamped():
    usage = normalize(
        {"prompt_cache_hit_tokens": -10, "completion_tokens": float("inf")},
        {},
        "deepseek",
    )
    assert usage.cache_hit == 0
    assert usage.output == 0
    assert Usage.from_dict({"cache_miss": float("nan"), "calls": -1}) == Usage()


# --------------------------------------------------------------------------
# Сам датакласс
# --------------------------------------------------------------------------
def test_usage_adds_up():
    total = Usage(cache_hit=10, calls=1) + Usage(cache_hit=5, output=2, calls=1)
    assert total == Usage(cache_hit=15, output=2, calls=2)


def test_input_and_hit_rate():
    usage = Usage(cache_hit=750, cache_miss=200, cache_write=50)
    assert usage.input == 1000
    assert usage.hit_rate == pytest.approx(75.0)


def test_hit_rate_without_input_is_zero():
    assert Usage().hit_rate == 0.0


def test_from_dict_ignores_foreign_and_non_numeric_keys():
    usage = Usage.from_dict(
        {"cache_hit": 10, "calls": 1, "model": "deepseek-v4-flash", "reasoning": None}
    )
    assert usage == Usage(cache_hit=10, calls=1)


def test_round_trip_through_dict():
    usage = Usage(cache_hit=1, cache_miss=2, cache_write=3, output=4, calls=5)
    assert Usage.from_dict(usage.as_dict()) == usage
