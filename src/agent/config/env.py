"""
Чтение окружения: загрузка `.env` и примитивы разбора значений.

Основание всего пакета настроек. Здесь и только здесь проект трогает
`os.environ`, и здесь же лежит правило «неразобранное значение — это ошибка,
а не повод молча взять умолчание»: `LLM_TEMPERATURE=ноль` обязано падать на
старте, а не тихо работать нулём.
"""

from __future__ import annotations

import math
import os

from dotenv import dotenv_values, find_dotenv, load_dotenv

_DOTENV_PATH = find_dotenv(usecwd=True)
load_dotenv(_DOTENV_PATH)


def _drop_empty_vars() -> None:
    """
    `VAR=` в .env означает «не задана», а не «пустая строка».

    Это важно не только для нашего кода: часть значений по умолчанию живёт
    внутри библиотек. Классы LangChain читают собственные переменные
    (DEEPSEEK_API_BASE, OPENAI_API_KEY и подобные) через default_factory, и
    объявленная, но пустая переменная перебила бы их дефолт пустой строкой.
    Поэтому объявленные в .env, но незаполненные переменные убираются из
    окружения целиком.

    Трогаем только имена, перечисленные в самом .env, и только если снаружи
    (в реальном окружении процесса, CI, docker) значения тоже нет: переменная
    из окружения всегда важнее файла.
    """
    if not _DOTENV_PATH:
        return
    for name, value in dotenv_values(_DOTENV_PATH).items():
        if (value or "").strip():
            continue
        if not (os.environ.get(name) or "").strip():
            os.environ.pop(name, None)


_drop_empty_vars()

_TRUTHY = {"1", "true", "yes", "on", "y"}
_FALSY = {"0", "false", "no", "off", "n"}


class ConfigError(RuntimeError):
    """Переменная окружения задана, но её значение нельзя разобрать."""


# --------------------------------------------------------------------------
# Примитивы чтения
# --------------------------------------------------------------------------
def env_str(name: str, default: str = "") -> str:
    """Строка без окружающих пробелов. Пустая переменная считается незаданной."""
    return (os.getenv(name) or "").strip() or default


def env_opt(name: str) -> str | None:
    """То же самое, но незаданное значение — None: для необязательных параметров."""
    return env_str(name) or None


def env_int(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = env_str(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}={raw!r}: ожидается целое число") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name}={raw!r}: значение должно быть не меньше {minimum}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{name}={raw!r}: значение должно быть не больше {maximum}")
    return value


def env_float(
    name: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    minimum_exclusive: bool = False,
) -> float:
    raw = env_str(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}={raw!r}: ожидается число") from exc
    if not math.isfinite(value):
        raise ConfigError(f"{name}={raw!r}: ожидается конечное число")
    if minimum is not None:
        below = value <= minimum if minimum_exclusive else value < minimum
        if below:
            relation = "больше" if minimum_exclusive else "не меньше"
            raise ConfigError(f"{name}={raw!r}: значение должно быть {relation} {minimum}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{name}={raw!r}: значение должно быть не больше {maximum}")
    return value


def env_bool(name: str, default: bool) -> bool:
    raw = env_str(name).lower()
    if not raw:
        return default
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    raise ConfigError(f"{name}={raw!r}: ожидается 1/0, true/false, yes/no, on/off")
