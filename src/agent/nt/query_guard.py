"""Guard for model-composed PromQL: scope, cost and shape before anything runs."""

from __future__ import annotations

import re

# Запрос от модели — единственное место, где в источник уходит текст, которого
# не писал администратор. Синтаксис разберёт сам Prometheus; здесь проверяется
# то, чего запрос не должен уметь:
#
#   область   каждый селектор несёт namespace прогона — иначе ряд приедет из
#             чужого контура, и разбор будет про чужой сервис;
#   объём     диапазон в селекторе ограничен, подзапросы запрещены: они
#             разворачиваются в тысячи вычислений на боевом Prometheus;
#   форма     агрегация по service обязательна — без неё ряд не сопоставить с
#             сервисом, и адаптер всё равно его отбросит;
#   словарь   функции только из списка, `@` и `offset` запрещены: они уводят
#             окно в сторону от периода теста, а отчёт утверждает обратное.

ALLOWED_FUNCTIONS = frozenset({
    "abs", "avg", "avg_over_time", "bottomk", "ceil", "changes", "clamp_max", "clamp_min",
    "count", "count_over_time", "delta", "deriv", "floor", "histogram_quantile", "idelta",
    "increase", "irate", "label_replace", "last_over_time", "max", "max_over_time", "min",
    "min_over_time", "quantile", "quantile_over_time", "rate", "resets", "round", "stddev",
    "stdvar", "sum", "sum_over_time", "topk",
})

# Операторы агрегации пишутся и как `sum by (service) (...)`, и как
# `sum(...) by (service)`: после имени может стоять модификатор, а не скобка.
AGGREGATIONS = frozenset({
    "avg", "bottomk", "count", "max", "min", "quantile", "stddev", "stdvar", "sum", "topk",
})

# Модификаторы агрегации: за ними идёт список меток, а не метрика.
LABEL_LISTS = frozenset({"by", "without", "on", "ignoring", "group_left", "group_right"})
KEYWORDS = frozenset({"and", "or", "unless", "bool", *LABEL_LISTS})

MAX_LENGTH = 600
MAX_RANGE_SECONDS = 900

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DURATION = re.compile(r"\A(?:\d+(?:ms|s|m|h|d|w))+\Z")
_DURATION_PART = re.compile(r"(\d+)(ms|s|m|h|d|w)")
_SUBQUERY = re.compile(r"\[[^\]]*:[^\]]*\]")
_ALLOWED_CHARS = re.compile(r'\A[A-Za-z0-9_.,:;{}\[\]()"\'=!~<>+\-*/%^$|\s]*\Z')
_SECONDS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def _duration_seconds(text: str) -> float | None:
    """Длительность PromQL в секундах. None — запись не разобрана."""
    if not _DURATION.match(text):
        return None
    return sum(int(number) * _SECONDS[unit] for number, unit in _DURATION_PART.findall(text))


def _skip_spaces(query: str, index: int) -> int:
    while index < len(query) and query[index].isspace():
        index += 1
    return index


def _closing(query: str, index: int, opening: str, closing: str) -> int:
    """Индекс за парной скобкой. -1 — пара не закрыта."""
    depth = 0
    while index < len(query):
        if query[index] in "\"'":
            index = _skip_string(query, index)
            if index < 0:
                return -1
            continue
        if query[index] == opening:
            depth += 1
        elif query[index] == closing:
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return -1


def _skip_string(query: str, index: int) -> int:
    """Индекс за строковым литералом. -1 — кавычка не закрыта."""
    quote = query[index]
    index += 1
    while index < len(query):
        if query[index] == "\\":
            index += 2
            continue
        if query[index] == quote:
            return index + 1
        index += 1
    return -1


def _selector_ok(selector: str, namespace: str) -> str:
    """Причина отказа для одного `{...}`; пусто — селектор ограничен namespace."""
    if "__name__" in selector:
        return "__name__ matcher is not allowed; name the metric explicitly"
    compact = re.sub(r"\s*=\s*", "=", selector)
    if f'namespace="{namespace}"' not in compact:
        return f'every selector must match namespace="{namespace}"'
    return ""


def validate(query: str, namespace: str, *, max_range_seconds: int = MAX_RANGE_SECONDS) -> str:
    """Причина отказа для модели и оператора; пусто — запрос можно показывать."""
    query = (query or "").strip()
    if not query:
        return "query is empty"
    if len(query) > MAX_LENGTH:
        return f"query exceeds {MAX_LENGTH} characters"
    if not _ALLOWED_CHARS.match(query):
        return "query contains unsupported characters"
    if "@" in query:
        return "@ modifier is not allowed; the window is fixed by the test period"
    if _SUBQUERY.search(query):
        return "subqueries are not allowed"
    if not re.search(r"\bby\s*\(\s*service\b", query):
        return "result must be aggregated with by (service)"

    index, saw_metric = 0, False
    while index < len(query):
        char = query[index]
        if char in "\"'":
            index = _skip_string(query, index)
            if index < 0:
                return "unterminated string literal"
            continue
        if char == "{":
            # Селектор без имени метрики: `{namespace="nt01", job="x"}`.
            end = _closing(query, index, "{", "}")
            if end < 0:
                return "unbalanced { }"
            reason = _selector_ok(query[index + 1:end - 1], namespace)
            if reason:
                return reason
            saw_metric, index = True, end
            continue
        if char == "[":
            end = query.find("]", index)
            if end < 0:
                return "unbalanced [ ]"
            seconds = _duration_seconds(query[index + 1:end].strip())
            if seconds is None:
                return "unsupported range duration"
            if seconds > max_range_seconds:
                return f"range must not exceed {max_range_seconds}s"
            index = end + 1
            continue
        match = _IDENTIFIER.match(query, index)
        if not match:
            index += 1
            continue
        name = match[0]
        after = _skip_spaces(query, match.end())
        following = query[after] if after < len(query) else ""
        if name in LABEL_LISTS:
            # Список меток агрегации: имена внутри него — не метрики.
            if following != "(":
                return f"{name} must be followed by a label list"
            end = _closing(query, after, "(", ")")
            if end < 0:
                return "unbalanced ( )"
            index = end
            continue
        if following == "(":
            if name not in ALLOWED_FUNCTIONS:
                return f"function {name} is not allowed"
            index = after + 1
            continue
        if following == "{":
            saw_metric = True
            index = match.end()
            continue
        if name in AGGREGATIONS:
            # Дальше идёт `by (...)`/`without (...)`, их разберёт следующий шаг.
            index = match.end()
            continue
        if name in KEYWORDS or _duration_seconds(name) is not None:
            index = match.end()
            continue
        return f"metric {name} must carry a label selector with namespace"

    if not saw_metric:
        return "query selects no metric"
    return ""
