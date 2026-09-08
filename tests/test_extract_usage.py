"""
Задача 1.2: `extract_usage` разбирает три источника данных с фолбэками.

Каждый фолбэк — отдельный случай, и именно здесь учёт токенов ломается тихо
при обновлении langchain: поле переехало, исключения нет, счётчики поехали
в ноль. Тест на каждую ветку — чтобы падало по имени, а не в отчёте по деньгам.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent.graph import extract_usage


def message(response_metadata=None, usage_metadata=None):
    """Утиная подделка ответа модели: extract_usage читает только два атрибута."""
    return SimpleNamespace(
        response_metadata=response_metadata, usage_metadata=usage_metadata
    )


def test_deepseek_raw_fields():
    """Ветка 1: сырые поля DeepSeek на месте — берём их и ничего не считаем."""
    msg = message(
        response_metadata={
            "token_usage": {
                "prompt_cache_hit_tokens": 1472,
                "prompt_cache_miss_tokens": 128,
                "completion_tokens": 210,
            }
        }
    )
    assert extract_usage(msg) == {
        "cache_hit": 1472,
        "cache_miss": 128,
        "cache_write": 0,
        "output": 210,
        "calls": 1,
    }


def test_raw_fields_under_usage_key():
    """Тот же случай, но сырые поля лежат под ключом `usage`, а не `token_usage`."""
    msg = message(
        response_metadata={
            "usage": {
                "prompt_cache_hit_tokens": 64,
                "prompt_cache_miss_tokens": 16,
                "completion_tokens": 8,
            }
        }
    )
    assert extract_usage(msg) == {
        "cache_hit": 64,
        "cache_miss": 16,
        "cache_write": 0,
        "output": 8,
        "calls": 1,
    }


def test_normalized_langchain_fields():
    """
    Ветка 2: своих полей нет, есть нормализованный LangChain.

    Тогда hit приезжает из `input_token_details.cache_read`, а miss считается
    вычитанием из общего входа. Это же путь для любого не-DeepSeek провайдера.
    """
    msg = message(
        response_metadata={},
        usage_metadata={
            "input_tokens": 1600,
            "output_tokens": 210,
            "input_token_details": {"cache_read": 1472},
        },
    )
    assert extract_usage(msg) == {
        "cache_hit": 1472,
        "cache_miss": 128,
        "cache_write": 0,
        "output": 210,
        "calls": 1,
    }


def test_no_metadata_at_all():
    """Ветка 3: метаданных нет вообще — нули, а не исключение."""
    assert extract_usage(message()) == {
        "cache_hit": 0,
        "cache_miss": 0,
        "cache_write": 0,
        "output": 0,
        "calls": 1,
    }


def test_cache_read_larger_than_prompt_tokens():
    """
    Ветка 4: провайдер отдал cache_read больше, чем весь вход.

    Такое встречается, когда поля считаются по-разному. Вычитание ушло бы
    в минус и испортило и hit rate, и стоимость — спасает max(..., 0).
    """
    msg = message(
        response_metadata={"token_usage": {"prompt_tokens": 1000}},
        usage_metadata={
            "input_tokens": 1000,
            "output_tokens": 5,
            "input_token_details": {"cache_read": 1472},
        },
    )
    usage = extract_usage(msg)
    assert usage["cache_miss"] == 0
    assert usage["cache_hit"] == 1472
    assert usage["output"] == 5


def test_calls_is_always_one():
    """`calls` — счётчик вызовов; складывает его редьюсер состояния, не эта функция."""
    assert extract_usage(message())["calls"] == 1
    assert extract_usage(message(response_metadata={"token_usage": {}}))["calls"] == 1


# --------------------------------------------------------------------------
# Запись в кеш: четвёртая статья расхода
# --------------------------------------------------------------------------
def test_cache_creation_becomes_cache_write():
    """
    У Anthropic запись в кеш тарифицируется отдельно и дороже обычного входа.
    LangChain кладёт её в `input_token_details.cache_creation`.
    """
    msg = message(
        response_metadata={},
        usage_metadata={
            "input_tokens": 2000,
            "output_tokens": 100,
            "input_token_details": {"cache_read": 1200, "cache_creation": 500},
        },
    )
    assert extract_usage(msg) == {
        "cache_hit": 1200,
        "cache_miss": 300,
        "cache_write": 500,
        "output": 100,
        "calls": 1,
    }


def test_cache_write_is_not_counted_twice_in_miss():
    """
    Записанные в кеш токены не должны попасть ещё и в miss: иначе один и тот
    же вход оплачивался бы дважды — по обычной цене и по цене записи.
    """
    usage = extract_usage(
        message(
            usage_metadata={
                "input_tokens": 1000,
                "output_tokens": 0,
                "input_token_details": {"cache_read": 0, "cache_creation": 1000},
            }
        )
    )
    assert usage["cache_miss"] == 0
    assert usage["cache_write"] == 1000


def test_deepseek_has_no_cache_write():
    """На DeepSeek такой статьи нет — счётчик обязан остаться нулевым."""
    msg = message(
        response_metadata={
            "token_usage": {
                "prompt_cache_hit_tokens": 100,
                "prompt_cache_miss_tokens": 20,
                "completion_tokens": 5,
            }
        }
    )
    assert extract_usage(msg)["cache_write"] == 0
