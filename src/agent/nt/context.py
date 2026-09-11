"""Explicit input extraction. Unmentioned values are never replaced by guessed defaults."""

from __future__ import annotations

import json
import math
import re

from agent.nt.models import timestamp
from agent.nt.thresholds import SLA_FIELDS

TEXT_FIELDS = {"jira_key", "target_service", "environment", "namespace", "target_url", "test_type",
               "test_id", "scenario", "previous_test_id", "test_status"}
NUMBER_FIELDS = {*SLA_FIELDS, "target_rps", "duration_seconds", "ramp_up_seconds", "virtual_users"}
TIME_FIELDS = {"started_at", "finished_at", "baseline_start", "baseline_end"}
LIST_FIELDS = {"services", "dependencies", "databases", "queues", "infrastructure_components"}
INPUT_FIELDS = TEXT_FIELDS | NUMBER_FIELDS | TIME_FIELDS | LIST_FIELDS | {"component_profiles"}
ALIASES = {"service": "target_service", "сервис": "target_service", "окружение": "environment",
           "test_start": "started_at", "test_end": "finished_at", "start": "started_at",
           "end": "finished_at", "target RPS": "target_rps"}


def validate_fields(fields: dict) -> tuple[dict, list[str]]:
    clean, errors = {}, []
    for key, value in fields.items():
        if key not in INPUT_FIELDS or value is None:
            continue
        valid = True
        if key in TEXT_FIELDS:
            valid = isinstance(value, str) and len(value) <= 2000
            if key == "test_status":
                valid = isinstance(value, str) and value in {
                    "not_started", "precheck", "ready", "running", "failed", "stopped", "completed"}
        elif key in NUMBER_FIELDS:
            valid = (isinstance(value, (int, float)) and not isinstance(value, bool)
                     and math.isfinite(value) and value >= 0)
        elif key in TIME_FIELDS:
            try:
                value = timestamp(value)
            except (ValueError, TypeError, OverflowError):
                valid = False
        elif key == "dependencies":
            valid = (isinstance(value, list) and len(value) <= 500 and all(isinstance(e, dict)
                and all(isinstance(e.get(k), str) and len(e[k]) <= 253 for k in ("from", "to")) for e in value))
            if valid:
                value = [{"from": e["from"], "to": e["to"]} for e in value]
        elif key in LIST_FIELDS:
            valid = (isinstance(value, list) and len(value) <= 500
                     and all(isinstance(s, str) and 0 < len(s) <= 253 for s in value))
        elif key == "component_profiles":
            valid = (isinstance(value, dict) and len(value) <= 500
                     and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()))
        if valid:
            clean[key] = value
        else:
            errors.append(f"неверный параметр: {key}")
    return clean, errors


def explicit_fields(text: str) -> dict:
    """Canonical JSON or key=value lines, plus unambiguous SLA units in prose."""
    result = {}
    # Chat messages may omit the closing Markdown fence; the JSON itself must
    # still be complete and valid before any fields can be accepted.
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*(?:```|\Z)", text, re.S)
    if text.strip().startswith("{"):
        blocks.append(text.strip())
    for block in blocks:
        try:
            data = json.loads(block)
            if isinstance(data, dict):
                result.update({k: v for k, v in data.items() if k in INPUT_FIELDS})
        except ValueError:
            pass
    for line in text.splitlines():
        match = re.match(r"^\s*([\w ]+)\s*[:=]\s*(.+?)\s*$", line)
        if not match:
            continue
        key = ALIASES.get(match[1].strip(), match[1].strip())
        value = match[2].strip().strip('`')
        if key in TEXT_FIELDS | TIME_FIELDS:
            result[key] = value
        elif key in NUMBER_FIELDS:
            try:
                result[key] = float(value)
            except ValueError:
                pass
    for quantile in (95, 99):
        match = re.search(rf"\bp{quantile}\s*(?:[<≤:=]|SLA)\s*(\d+(?:\.\d+)?)\s*(ms|мс)\b", text, re.I)
        if match:
            result.setdefault(f"sla_p{quantile}_ms", float(match[1]))
    match = re.search(r"(?:errors?|error rate|ошибки)\s*[<≤:=]\s*(\d+(?:\.\d+)?)\s*%", text, re.I)
    if match:
        result.setdefault("sla_error_rate", float(match[1]) / 100)
    return result


def validated_extraction(output: str, source_text: str) -> dict:
    """LLM can copy identifiers/dates from context, not choose SLA or do arithmetic.

Each field needs a verbatim evidence quote containing the verbatim value.
Numbers/SLA are parsed deterministically from explicit inputs above.
"""
    try:
        data = json.loads(output.strip().removeprefix("```json").removesuffix("```").strip())
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    result = {}
    for key, item in data.items():
        if key not in TEXT_FIELDS | TIME_FIELDS or not isinstance(item, dict):
            continue
        value, quote = item.get("value"), item.get("evidence")
        if not isinstance(value, str) or not isinstance(quote, str) or not value or len(value) > 500:
            continue
        if quote and quote in source_text and value in quote:
            if key in TIME_FIELDS:
                try:
                    timestamp(value)
                except ValueError:
                    continue
            result[key] = value
    return result
