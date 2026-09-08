"""Server-side state/event redaction with wildcard path support."""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from typing import Any

MASK = "********"
REMOVE = object()

DEFAULT_RULES = (
    {"path": "authorization", "mode": "remove"},
    {"path": "headers.authorization", "mode": "remove"},
    {"path": "headers.cookie", "mode": "remove"},
    {"path": "cookies", "mode": "remove"},
    {"path": "api_key", "mode": "mask"},
    {"path": "*.api_key", "mode": "mask"},
    {"path": "messages.*.response_metadata.raw_prompt", "mode": "remove"},
    {"path": "raw_provider_request", "mode": "remove"},
    {"path": "raw_provider_response", "mode": "remove"},
)


def _metadata(value: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"type": type(value).__name__}
    if isinstance(value, (str, bytes, list, tuple, dict)):
        result["size"] = len(value)
    return result


def _replacement(value: Any, rule: dict[str, Any], roles: set[str]) -> Any:
    mode = rule.get("mode")
    if mode == "remove":
        return REMOVE
    if mode == "mask":
        return MASK
    if mode == "truncate":
        limit = max(0, min(int(rule.get("max_length", 256)), 100_000))
        text = str(value)
        return text if len(text) <= limit else text[:limit] + "…"
    if mode == "metadata_only":
        return _metadata(value)
    if mode == "role":
        allowed = {str(role) for role in rule.get("roles", [])}
        return value if roles & allowed else REMOVE
    return value


def _apply(container: Any, tokens: list[str], rule: dict[str, Any], roles: set[str]) -> None:
    if not tokens:
        return
    token, rest = tokens[0], tokens[1:]
    if isinstance(container, dict):
        keys = list(container) if token == "*" else ([token] if token in container else [])
        for key in keys:
            if rest:
                _apply(container[key], rest, rule, roles)
            else:
                replacement = _replacement(container[key], rule, roles)
                if replacement is REMOVE:
                    container.pop(key, None)
                else:
                    container[key] = replacement
    elif isinstance(container, list):
        indexes = range(len(container)) if token == "*" else []
        for index in indexes:
            if rest:
                _apply(container[index], rest, rule, roles)
            else:
                replacement = _replacement(container[index], rule, roles)
                container[index] = None if replacement is REMOVE else replacement


def redact(
    value: Any,
    rules: Iterable[dict[str, Any]] = (),
    *,
    roles: Iterable[str] = (),
    include_defaults: bool = True,
) -> Any:
    """Return a deep redacted copy; the caller's state is never mutated."""
    result = deepcopy(value)
    active = [*DEFAULT_RULES, *rules] if include_defaults else list(rules)
    role_set = set(roles)
    for rule in active:
        path = rule.get("path")
        if isinstance(path, str) and path:
            _apply(result, [part for part in path.split(".") if part], rule, role_set)
    return result
