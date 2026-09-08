"""
Таблица тарифов и её переопределение из окружения.

Три переменные PRICE_* хороши ровно для одной модели: у каждой свой тариф,
и при смене провайдера все надо переписывать руками. Здесь тариф ищется по
паре (провайдер, модель) в `prices.toml`, а переменные окружения остаются
как переопределение поверх таблицы — для стенда, скидки или свежего прайса,
до которого не дошли руки.
"""

from __future__ import annotations

import math
import os
import tomllib
import warnings
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files

# Имя статьи в таблице -> счётчик, к которому она применяется.
ARTICLE_BY_COUNTER = {
    "cache_hit": "cache_read",
    "cache_miss": "input",
    "cache_write": "cache_write",
    "output": "output",
}

# Переопределения из окружения. Имена сохранены от прежней схемы проекта.
ENV_BY_ARTICLE = {
    "cache_read": "PRICE_CACHE_HIT_PER_MTOK",
    "input": "PRICE_CACHE_MISS_PER_MTOK",
    "cache_write": "PRICE_CACHE_WRITE_PER_MTOK",
    "output": "PRICE_OUTPUT_PER_MTOK",
}


class PriceError(ValueError):
    """Тариф в окружении задан, но его нельзя разобрать."""


@dataclass(frozen=True)
class Prices:
    """Тариф одной модели, $ за 1M токенов."""

    input: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    output: float = 0.0
    #: Откуда приехали числа — видно в отчёте, чтобы не гадать.
    source: str = "неизвестно"
    #: Нашлась ли модель в таблице. False означает, что стоимость может быть
    #: нулевой не потому, что она нулевая, а потому, что тариф неизвестен.
    known: bool = False

    def __post_init__(self) -> None:
        for name in ENV_BY_ARTICLE:
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise PriceError(f"{name}={value!r}: цена должна быть конечной и неотрицательной")

    def per_counter(self) -> dict[str, float]:
        """Цена на каждый счётчик `Usage`."""
        return {counter: getattr(self, article) for counter, article in ARTICLE_BY_COUNTER.items()}

    def cost(self, usage) -> float:
        """Стоимость расхода в долларах."""
        counters = usage.as_dict() if hasattr(usage, "as_dict") else dict(usage)
        return sum(
            counters.get(counter, 0) / 1_000_000 * price
            for counter, price in self.per_counter().items()
        )

    def naive_cost(self, usage) -> float:
        """Сколько стоил бы тот же расход, если бы кеша не было вообще."""
        counters = usage.as_dict() if hasattr(usage, "as_dict") else dict(usage)
        total_input = sum(
            counters.get(name, 0) for name in ("cache_hit", "cache_miss", "cache_write")
        )
        return (
            total_input / 1_000_000 * self.input
            + counters.get("output", 0) / 1_000_000 * self.output
        )


@lru_cache(maxsize=1)
def table() -> dict:
    """Разобранный `prices.toml`. Читается один раз за процесс."""
    with (files("costmeter") / "prices.toml").open("rb") as handle:
        return tomllib.load(handle)


def captured_at() -> str:
    """Дата снятия тарифов из таблицы: цены устаревают, и это должно быть видно."""
    return (table().get("meta") or {}).get("captured", "неизвестно")


def _env_float(name: str) -> float | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise PriceError(f"{name}={raw!r}: ожидается число") from exc
    if not math.isfinite(value) or value < 0:
        raise PriceError(f"{name}={raw!r}: цена должна быть конечной и неотрицательной")
    return value


def _warn_once(provider: str, model: str) -> None:
    warnings.warn(
        f"тариф для {provider}/{model} не найден в prices.toml — стоимость "
        f"будет считаться нулевой. Допишите блок в таблицу или задайте "
        f"переменные {', '.join(sorted(ENV_BY_ARTICLE.values()))}.",
        stacklevel=3,
    )


def for_model(provider: str, model: str, use_env: bool = True) -> Prices:
    """
    Тариф модели: таблица плюс переопределения из окружения.

    Модели нет в таблице — предупреждение и нули. Молчаливый неверный счёт
    хуже отсутствующего: отсутствующий видно сразу.
    """
    entry = ((table().get("prices") or {}).get(provider) or {}).get(model)
    known = entry is not None
    values = {
        article: float(entry.get(article, 0.0)) if known else 0.0 for article in ENV_BY_ARTICLE
    }

    overridden = []
    if use_env:
        for article, variable in ENV_BY_ARTICLE.items():
            value = _env_float(variable)
            if value is not None:
                values[article] = value
                overridden.append(variable)

    if not known and not overridden:
        _warn_once(provider, model)

    if known and overridden:
        source = f"prices.toml ({captured_at()}) + окружение"
    elif known:
        source = f"prices.toml ({captured_at()})"
    elif overridden:
        source = "окружение"
        known = True
    else:
        source = "тариф неизвестен"

    return Prices(**values, source=source, known=known)
