"""Server-owned limits and query allowlist; never supplied by an LLM or thread."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field

from agent.nt.metric_profiles import PROFILES, unit_for


def _integer(name: str, default: int, low: int, high: int) -> int:
    value = int(os.getenv(name, str(default)))
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


@dataclass(frozen=True)
class Settings:
    step: int = 30
    max_window: int = 86400
    baseline_seconds: int = 600
    concurrency: int = 4
    max_series: int = 500
    max_points: int = 200000
    top_n: int = 5
    max_iterations: int = 4
    timeout_seconds: int = 300
    stable_seconds: int = 60
    queries: dict = field(default_factory=dict)
    anomaly_weights: dict = field(default_factory=dict)


def load_settings() -> Settings:
    queries = json.loads(os.getenv("NT_METRIC_QUERIES", "{}"))
    if not isinstance(queries, dict) or len(queries) > 64:
        raise ValueError("NT_METRIC_QUERIES must be an object with at most 64 entries")
    for metric, spec in queries.items():
        if not isinstance(spec, dict):
            raise ValueError("metric query definition must be an object")
        unit_for(metric)
        profiles = spec.get("profiles", [spec.get("profile", "http")])
        if not isinstance(profiles, list) or not profiles or any(p not in PROFILES for p in profiles):
            raise ValueError("unknown metric profile")
        if any(metric not in PROFILES[p].metrics for p in profiles):
            raise ValueError("metric does not belong to its configured profiles")
        query = spec.get("prometheus")
        if query is not None and (not isinstance(query, str) or len(query) > 8000):
            raise ValueError("invalid Prometheus query template")
        if query and "{{namespace}}" not in query:
            raise ValueError("Prometheus query must explicitly scope {{namespace}}")
        if spec.get("influx") and not isinstance(spec["influx"], dict):
            raise ValueError("influx mapping must be an object")
        if not query and not spec.get("influx"):
            raise ValueError("each metric requires prometheus or influx mapping")
    weights = json.loads(os.getenv("NT_ANOMALY_WEIGHTS", "{}"))
    if not isinstance(weights, dict) or set(weights) - {"threshold", "baseline", "spike", "trend"}:
        raise ValueError("invalid NT_ANOMALY_WEIGHTS")
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1
           for v in weights.values()):
        raise ValueError("anomaly weights must be finite in [0, 1]")
    return Settings(
        step=_integer("NT_STEP_SECONDS", 30, 1, 3600),
        max_window=_integer("NT_MAX_WINDOW_SECONDS", 86400, 60, 604800),
        baseline_seconds=_integer("NT_BASELINE_SECONDS", 600, 60, 86400),
        concurrency=_integer("NT_CONCURRENCY", 4, 1, 16),
        max_series=_integer("NT_MAX_SERIES", 500, 20, 2000),
        max_points=_integer("NT_MAX_POINTS", 200000, 1000, 2000000),
        top_n=_integer("NT_TOP_N", 5, 1, 20),
        max_iterations=_integer("NT_MAX_INVESTIGATION_CALLS", 4, 1, 4),
        timeout_seconds=_integer("NT_TIMEOUT_SECONDS", 300, 30, 3600),
        stable_seconds=_integer("NT_STABLE_SECONDS", 60, 30, 3600),
        queries=queries,
        anomaly_weights=weights,
    )
