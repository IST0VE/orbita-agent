"""
Счётчики токенов и их нормализация.

Провайдеры считают одно и то же по-разному и кладут в разные поля. Здесь —
таблица правил: где у кого лежат счётчики, — и один датакласс `Usage` с
четырьмя статьями на выходе. Добавление провайдера должно быть строкой
в таблице, а не ещё одной веткой в разборе.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

# Четыре статьи расхода. Имена совпадают с ключами, которые копит состояние
# графа Orbita: оба пути учёта обязаны считать одно и то же.
COUNTERS = ("cache_hit", "cache_miss", "cache_write", "output")


def _count(value: Any) -> int:
    """Счётчик провайдера как неотрицательное целое; NaN/inf/bool — не счётчики."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    try:
        return max(int(value), 0)
    except (OverflowError, ValueError):
        return 0


@dataclass(frozen=True)
class Usage:
    """
    Расход по одному вызову модели или по целому треду.

    cache_hit   — вход, отданный из кеша провайдера;
    cache_miss  — вход, посчитанный заново;
    cache_write — вход, записанный в кеш (у Anthropic дороже обычного);
    output      — генерация;
    calls       — сколько вызовов модели сложено в эти числа.
    """

    cache_hit: int = 0
    cache_miss: int = 0
    cache_write: int = 0
    output: int = 0
    calls: int = 0

    @property
    def input(self) -> int:
        """Весь вход: из кеша, пересчитанный и записанный."""
        return self.cache_hit + self.cache_miss + self.cache_write

    @property
    def hit_rate(self) -> float:
        """Доля входа, приехавшая из кеша, в процентах."""
        return self.cache_hit / self.input * 100 if self.input else 0.0

    def __add__(self, other: Usage) -> Usage:
        if not isinstance(other, Usage):
            return NotImplemented
        return Usage(
            cache_hit=self.cache_hit + other.cache_hit,
            cache_miss=self.cache_miss + other.cache_miss,
            cache_write=self.cache_write + other.cache_write,
            output=self.output + other.output,
            calls=self.calls + other.calls,
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "cache_hit": self.cache_hit,
            "cache_miss": self.cache_miss,
            "cache_write": self.cache_write,
            "output": self.output,
            "calls": self.calls,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> Usage:
        """Собрать из словаря, игнорируя чужие и нечисловые ключи."""
        data = data or {}
        values = {}
        for name in (*COUNTERS, "calls"):
            value = data.get(name, 0)
            values[name] = _count(value)
        return cls(**values)

    def with_calls(self, calls: int) -> Usage:
        return replace(self, calls=calls)


# --------------------------------------------------------------------------
# Правила разбора
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Rule:
    """
    Где у провайдера лежат счётчики внутри сырого `usage`.

    Каждое поле — путь: последовательность ключей от корня словаря. Пустой
    путь означает «такого поля у провайдера нет», и значение выводится
    вычитанием из `total_input`.
    """

    hit: tuple[str, ...] = ()
    miss: tuple[str, ...] = ()
    write: tuple[str, ...] = ()
    output: tuple[str, ...] = ()
    total_input: tuple[str, ...] = ()


# DeepSeek отдаёт hit и miss явно, и prompt_tokens = hit + miss.
DEEPSEEK = Rule(
    hit=("prompt_cache_hit_tokens",),
    miss=("prompt_cache_miss_tokens",),
    output=("completion_tokens",),
    total_input=("prompt_tokens",),
)

# OpenAI даёт только закешированную часть; miss выводится вычитанием, потому
# что prompt_tokens включает в себя cached_tokens.
OPENAI = Rule(
    hit=("prompt_tokens_details", "cached_tokens"),
    output=("completion_tokens",),
    total_input=("prompt_tokens",),
)

# У Anthropic input_tokens — это уже «всё остальное», без чтения и записи
# кеша. Поэтому вычитать ничего не нужно: это готовый miss.
ANTHROPIC = Rule(
    hit=("cache_read_input_tokens",),
    miss=("input_tokens",),
    write=("cache_creation_input_tokens",),
    output=("output_tokens",),
)

# Фолбэк на нормализованные поля LangChain: они одинаковы у всех провайдеров,
# но появляются не всегда и теряют вендорные детали.
NORMALIZED = Rule(
    hit=("input_token_details", "cache_read"),
    write=("input_token_details", "cache_creation"),
    output=("output_tokens",),
    total_input=("input_tokens",),
)

RULES: dict[str, Rule] = {
    "deepseek": DEEPSEEK,
    "openai": OPENAI,
    "anthropic": ANTHROPIC,
}


def _dig(data: Any, path: tuple[str, ...]) -> int | None:
    """Достать число по пути ключей. Нет пути или значения — None."""
    if not path:
        return None
    node = data
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    if isinstance(node, bool) or not isinstance(node, (int, float)):
        return None
    return _count(node)


FIELDS = ("hit", "miss", "write", "output", "total")


def _read(rule: Rule, data: dict) -> dict[str, int | None]:
    """Прочитать все поля правила из словаря."""
    return {
        "hit": _dig(data, rule.hit),
        "miss": _dig(data, rule.miss),
        "write": _dig(data, rule.write),
        "output": _dig(data, rule.output),
        "total": _dig(data, rule.total_input),
    }


def normalize(raw: dict | None, normalized: dict | None, provider: str | None = None) -> Usage:
    """
    Свести сырой usage провайдера и нормализованный usage LangChain к `Usage`.

    Источники не заменяют друг друга, а дополняют. Сначала берётся правило
    провайдера — в нём вендорные поля, которых LangChain не знает. Провайдер
    не назван — пробуются все известные. Чего не хватило, добирается из
    нормализованных полей LangChain: провайдер может отдать общий объём входа,
    но не разбивку по кешу, и наоборот.

    Ничего не нашлось — нули и один вызов, но не исключение: отсутствие
    статистики не повод ронять ход агента.
    """
    known = provider and provider.lower() in RULES
    sources = (
        [(RULES[provider.lower()], raw or {})]
        if known
        else [(rule, raw or {}) for rule in RULES.values()]
    )
    sources.append((NORMALIZED, normalized or {}))

    found: dict[str, int | None] = dict.fromkeys(FIELDS)
    for rule, data in sources:
        for name, value in _read(rule, data).items():
            if found[name] is None:
                found[name] = value

    hit = found["hit"] or 0
    write = found["write"] or 0
    output = found["output"] or 0
    miss = found["miss"]
    if miss is None:
        # Записанное в кеш вычитается тоже: иначе те же токены попали бы и
        # в miss по обычной цене, и в cache_write по своей.
        miss = max((found["total"] or 0) - hit - write, 0)

    return Usage(cache_hit=hit, cache_miss=miss, cache_write=write, output=output, calls=1)
