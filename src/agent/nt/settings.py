"""Server-owned limits and query allowlist; never supplied by an LLM or thread."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field

from agent.nt.metric_profiles import PROFILES, unit_for

TOOL_APPROVAL_MODES = ("off", "generated", "all")


def _flag(name: str, default: bool) -> bool:
    value = os.getenv(name, "1" if default else "0").strip().lower()
    if value not in {"0", "1", "true", "false", "yes", "no", "on", "off"}:
        raise ValueError(f"{name} must be a boolean flag")
    return value in {"1", "true", "yes", "on"}


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
    settling_seconds: int = 30
    plateau_tolerance: float = .1
    queries: dict = field(default_factory=dict)
    anomaly_weights: dict = field(default_factory=dict)
    # Запросы, которые составляет модель: discovery + выполнение проверенного
    # PromQL. Ряды из них живут только в уликах и не участвуют в вердикте.
    generated_queries: bool = False
    query_range_seconds: int = 900
    discovery_limit: int = 40
    # Что показывать оператору до выполнения: ничего, только составленные
    # моделью запросы или каждый вызов инструмента.
    tool_approval: str = "generated"


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
    if not isinstance(weights, dict) or set(weights) - {"threshold", "baseline", "spike", "trend", "counter_increase", "saturation"}:
        raise ValueError("invalid NT_ANOMALY_WEIGHTS")
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1
           for v in weights.values()):
        raise ValueError("anomaly weights must be finite in [0, 1]")
    approval = os.getenv("NT_TOOL_APPROVAL", "generated").strip().lower()
    if approval not in TOOL_APPROVAL_MODES:
        raise ValueError("NT_TOOL_APPROVAL must be one of " + ", ".join(TOOL_APPROVAL_MODES))
    return Settings(
        step=_integer("NT_STEP_SECONDS", 30, 1, 3600),
        max_window=_integer("NT_MAX_WINDOW_SECONDS", 86400, 60, 604800),
        baseline_seconds=_integer("NT_BASELINE_SECONDS", 600, 60, 86400),
        concurrency=_integer("NT_CONCURRENCY", 4, 1, 16),
        max_series=_integer("NT_MAX_SERIES", 500, 20, 2000),
        max_points=_integer("NT_MAX_POINTS", 200000, 1000, 2000000),
        top_n=_integer("NT_TOP_N", 5, 1, 20),
        max_iterations=_integer("NT_MAX_INVESTIGATION_CALLS", 4, 1, 8),
        timeout_seconds=_integer("NT_TIMEOUT_SECONDS", 300, 30, 3600),
        stable_seconds=_integer("NT_STABLE_SECONDS", 60, 30, 3600),
        settling_seconds=_integer("NT_SETTLING_SECONDS", 30, 0, 3600),
        plateau_tolerance=_integer("NT_PLATEAU_TOLERANCE_PERCENT", 10, 1, 30) / 100,
        queries=queries,
        anomaly_weights=weights,
        generated_queries=_flag("NT_GENERATED_QUERIES", False),
        query_range_seconds=_integer("NT_QUERY_RANGE_SECONDS", 900, 60, 3600),
        discovery_limit=_integer("NT_DISCOVERY_LIMIT", 40, 5, 200),
        tool_approval=approval,
    )
