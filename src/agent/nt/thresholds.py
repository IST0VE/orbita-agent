"""SLA verdict and routing are decisions made exclusively by code."""

from __future__ import annotations

import math

SLA_FIELDS = {"sla_p95_ms": "p95", "sla_p99_ms": "p99", "sla_error_rate": "error_rate",
              "sla_max_cpu": "cpu", "sla_max_memory": "memory"}


def limits_from(state: dict) -> dict:
    limits = {}
    for field, metric in SLA_FIELDS.items():
        value = state.get(field)
        if value is None:
            continue
        if isinstance(value, bool):
            raise ValueError(f"invalid {field}")
        value = float(value)
        if not math.isfinite(value) or value < 0 or (metric in {"error_rate", "cpu", "memory"}
                                                    and value > 1):
            raise ValueError(f"invalid {field}")
        limits[metric] = value
    return limits


def breaches(value: float, limit: float, comparator: str = "<=") -> bool:
    return value >= limit if comparator == "<" else value > limit


def violations(series: list[dict], thresholds: dict[str, dict], *, comparators: dict | None = None) -> list[dict]:
    found = []
    for item in series:
        limit = thresholds.get(item["service"], {}).get(item["metric"])
        if limit is None:
            continue
        comparator = (comparators or {}).get(item["metric"], "<=")
        points = [(t, v) for t, v in zip(item["timestamps"], item["values"], strict=True)
                  if breaches(v, limit, comparator)]
        if points:
            found.append({"service": item["service"], "metric": item["metric"], "limit": limit,
                          "comparator": comparator, "peak": max(v for _, v in points), "first_at": points[0][0],
                          "last_at": points[-1][0], "count": len(points),
                          "unit": item["unit"], "source": item["source"]})
    return found


def verdict(violations: list, missing: list, *, completed: bool, has_sla: bool) -> str:
    if violations:
        return "FAILED"
    if missing or not completed or not has_sla:
        return "INCONCLUSIVE"
    return "PASSED"


def evaluate_test(state: dict, *, critical_limits: dict | None = None) -> str:
    """Reusable monitoring policy; The historical graph uses completed test input."""
    status = state.get("test_status")
    if status == "failed":
        return "failed"
    if status in {"completed", "stopped"}:
        return "finished"
    if state.get("iteration", 0) >= state.get("max_iterations", 100):
        return "stop"
    if state.get("elapsed_seconds", 0) >= state.get("global_timeout_seconds", 3600):
        return "stop"
    if violations(state.get("metric_snapshots", []), critical_limits or {}):
        return "stop"
    if state.get("critical_failure"):
        return "stop"
    return "investigate" if state.get("anomalies") else "continue"
