"""Validation for the deliberately small JSON Schema subset used by forms."""

from __future__ import annotations

import re
from typing import Any


class FormValidationError(ValueError):
    pass


def validate_value(value: Any, schema: dict[str, Any], *, path: str = "$", depth: int = 0) -> None:
    if depth > 10:
        raise FormValidationError(f"{path}: nesting exceeds 10 levels")
    if "const" in schema and value != schema["const"]:
        raise FormValidationError(f"{path}: value does not match const")
    if "enum" in schema and value not in schema["enum"]:
        raise FormValidationError(f"{path}: value is outside enum")

    kind = schema.get("type")
    matches = {
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "null": value is None,
    }
    if isinstance(kind, str) and kind in matches and not matches[kind]:
        raise FormValidationError(f"{path}: expected {kind}")

    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            raise FormValidationError(f"{path}: string is too short")
        if len(value) > int(schema.get("maxLength", 1_000_000)):
            raise FormValidationError(f"{path}: string is too long")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            raise FormValidationError(f"{path}: string does not match pattern")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise FormValidationError(f"{path}: value is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise FormValidationError(f"{path}: value is above maximum")

    if isinstance(value, list):
        if len(value) > min(int(schema.get("maxItems", 1000)), 1000):
            raise FormValidationError(f"{path}: array is too large")
        item_schema = schema.get("items", {})
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                validate_value(item, item_schema, path=f"{path}[{index}]", depth=depth + 1)

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise FormValidationError(f"{path}.{key}: required value is missing")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            properties = {}
        if schema.get("additionalProperties") is False:
            unknown = set(value) - set(properties)
            if unknown:
                raise FormValidationError(f"{path}: unknown fields: {', '.join(sorted(unknown))}")
        for key, child in value.items():
            child_schema = properties.get(key)
            if isinstance(child_schema, dict):
                validate_value(child, child_schema, path=f"{path}.{key}", depth=depth + 1)
