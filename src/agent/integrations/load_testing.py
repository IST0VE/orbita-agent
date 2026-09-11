"""Backend-neutral contract plus a read-only HTTP gateway for completed test metadata.

The optional gateway exposes GET /tests/{id} and GET /tests/{id}/results.
Control methods intentionally have no HTTP implementation in MVP 1.
"""

import math
import re
from typing import Protocol

from agent.integrations.http import AdapterError, HTTPClient
from agent.nt.context import INPUT_FIELDS, validate_fields
from agent.nt.models import failure

_THRESHOLD_FIELDS = {
    "p95": "sla_p95_ms", "p95_ms": "sla_p95_ms",
    "p99": "sla_p99_ms", "p99_ms": "sla_p99_ms",
    "error_rate": "sla_error_rate", "cpu": "sla_max_cpu", "memory": "sla_max_memory",
}
# k6 называет предел агрегатом внутри выражения, а не именем метрики:
# у http_req_duration и p(95), и p(99) — одна и та же метрика.
_K6_AGGREGATES = {
    "http_req_duration": {"p(95)": "p95", "p(99)": "p99"},
    "http_req_failed": {"rate": "error_rate"},
}


def normalize_k6(metric: str, expression: str) -> tuple[str, str] | None:
    """Свести k6-порог к канонической паре (метрика, сравнение).

    Единицы k6 совпадают с контрактом НТ: http_req_duration в миллисекундах,
    http_req_failed — доля 0..1. Незнакомый агрегат не переводится: пропустить
    предел молча нельзя, он может быть строже заданных оператором.
    """
    aggregates = _K6_AGGREGATES.get(metric)
    if not aggregates:
        return None
    match = re.match(r"\s*([a-z]+(?:\(\s*\d+(?:\.\d+)?\s*\))?)\s*(?=<=|<|≤)", expression, re.I)
    if not match:
        return None
    name = re.sub(r"\s+", "", match[1].lower())
    return (aggregates[name], expression[match.end():]) if name in aggregates else None


def threshold_fields(items: object, *, comparators: dict | None = None) -> tuple[dict, list[str]]:
    """Read limits, never the observed measurements or gateway pass/fail flags."""
    if not isinstance(items, list) or len(items) > 64:
        return {}, ["неверный список thresholds в gateway"]
    fields, errors = {}, []
    for item in items:
        if not isinstance(item, dict):
            errors.append("неверный threshold в gateway")
            continue
        metric, expression = item.get("metric"), item.get("expression")
        if isinstance(metric, str) and isinstance(expression, str):
            metric, expression = normalize_k6(metric, expression) or (metric, expression)
        field = _THRESHOLD_FIELDS.get(metric) if isinstance(metric, str) else None
        if not field or not isinstance(expression, str) or len(expression) > 500:
            errors.append("неподдерживаемый threshold в gateway; задайте SLA в каноническом формате")
            continue
        # Accept a comparison with an optional matching metric name, never an
        # arbitrary expression or a measurement masquerading as a limit.
        match = re.fullmatch(
            rf"\s*(?:{re.escape(metric)}\s*)?(?P<operator><=|<|≤)\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>ms|мс|s|%|ratio)?\s*",
            expression, re.I,
        )
        if not match:
            errors.append(f"неподдерживаемое выражение threshold: {metric}")
            continue
        value, unit = float(match["value"]), (match["unit"] or "").lower()
        if field.endswith("_ms"):
            # p95/p99 use milliseconds in the NT metric contract, as do the
            # explicitly suffixed p95_ms/p99_ms gateway names.
            if unit in {"", "ms", "мс"}:
                pass
            elif unit == "s":
                value *= 1000
            else:
                errors.append(f"не указана единица времени threshold: {metric}")
                continue
        elif unit == "%":
            value /= 100
        elif unit not in {"", "ratio"}:
            errors.append(f"неверная единица threshold: {metric}")
            continue
        if not math.isfinite(value) or (not field.endswith("_ms") and value > 1):
            errors.append(f"неверный лимит threshold: {metric}")
        elif field in fields and fields[field] != value:
            errors.append(f"конфликт thresholds в gateway: {metric}")
        else:
            fields[field] = value
            if comparators is not None:
                name = {"sla_p95_ms": "p95", "sla_p99_ms": "p99", "sla_error_rate": "error_rate",
                        "sla_max_cpu": "cpu", "sla_max_memory": "memory"}[field]
                operator = "<" if match["operator"] == "<" else "<="
                # Equal limits with different comparators retain the stricter one.
                comparators[name] = "<" if "<" in (operator, comparators.get(name)) else "<="
    return fields, errors


class LoadTestingBackend(Protocol):
    def prepare_test(self, plan: dict) -> dict: ...
    def start_test(self, prepared_id: str) -> dict: ...
    def stop_test(self, test_id: str) -> dict: ...
    def get_test_status(self, test_id: str) -> dict: ...
    def get_test_results(self, test_id: str) -> dict: ...


class HTTPLoadTesting:
    def __init__(self, url: str, token: str = ""):
        self.http = HTTPClient(url, token, max_bytes=64000)

    def _get(self, test_id, suffix=""):
        if not isinstance(test_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", test_id):
            return failure("INVALID_TEST_ID", "invalid test identifier")
        try:
            data = self.http.json("GET", f"/tests/{test_id}{suffix}")
            if not isinstance(data, dict):
                raise AdapterError("INVALID_RESPONSE")
            if data.get("test_id", test_id) != test_id:
                return failure("INVALID_TEST_METADATA", "gateway returned a different test_id")
            selected = {k: v for k, v in data.items() if k in INPUT_FIELDS}
            if "status" in data:
                if "test_status" in selected and selected["test_status"] != data["status"]:
                    return failure("INVALID_TEST_METADATA", "conflicting gateway status fields")
                selected["test_status"] = data["status"]
            comparators = {}
            limits, threshold_errors = threshold_fields(data.get("thresholds", []), comparators=comparators)
            for field, value in limits.items():
                if field in selected and selected[field] != value:
                    threshold_errors.append(f"конфликт SLA и thresholds в gateway: {field}")
                else:
                    selected[field] = value
            if comparators:
                existing = selected.get("sla_comparators") or {}
                if not isinstance(existing, dict):
                    return failure("INVALID_TEST_METADATA", "invalid SLA comparators")
                if any(k in existing and existing[k] != v for k, v in comparators.items()):
                    threshold_errors.append("конфликт операторов SLA и thresholds в gateway")
                selected["sla_comparators"] = {**existing, **comparators}
            fields, errors = validate_fields(selected)
            if errors:
                return failure("INVALID_TEST_METADATA", "invalid fields in test metadata")
            return {"success": True, "data": fields, "missing_parameters": threshold_errors}
        except (AdapterError, ValueError) as exc:
            return failure("LOAD_TESTING_UNAVAILABLE", str(exc) if isinstance(exc, AdapterError)
                           else "invalid test metadata response")

    def get_test_status(self, test_id):
        return self._get(test_id)

    def get_test_results(self, test_id):
        results = self._get(test_id, "/results")
        if not results["success"]:
            return results
        metadata = self._get(test_id)
        if not metadata["success"]:
            # Keep compatibility with gateways whose /results already contains
            # the entire contract. An invalid card must never be ignored.
            if metadata["error_type"] == "INVALID_TEST_METADATA":
                return metadata
            return {**results, "errors": [metadata]}
        merged = dict(metadata["data"])
        for field, value in results["data"].items():
            if field in merged and merged[field] != value:
                if field == "test_status" and "running" in (merged[field], value):
                    merged[field] = "running"
                    continue
                return failure("INVALID_TEST_METADATA", f"test card and results disagree: {field}")
            merged[field] = value
        return {"success": True, "data": merged,
                "missing_parameters": [*metadata["missing_parameters"], *results["missing_parameters"]]}

    def prepare_test(self, plan):
        return failure("POLICY_DENIED", "MVP 1 is read-only")

    def start_test(self, prepared_id):
        return failure("POLICY_DENIED", "MVP 1 is read-only")

    def stop_test(self, test_id):
        return failure("POLICY_DENIED", "MVP 1 is read-only")
