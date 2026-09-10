"""Canonical metric contracts. Exporter-specific expressions belong in server config.

Latency is milliseconds, utilization/rates are fractions (0..1), memory bytes
and CPU cores have distinct names so that units cannot be silently conflated.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricProfile:
    name: str
    metrics: dict[str, str]


PROFILES = {
    "http": MetricProfile("http", {
        "rps": "requests/s", "request_count": "requests", "error_rate": "ratio",
        "http_4xx": "requests/s", "http_5xx": "requests/s", "p50": "ms",
        "p95": "ms", "p99": "ms", "cpu": "ratio", "memory": "ratio",
        "cpu_throttling": "ratio", "pod_restarts": "count", "replicas": "count",
        "network": "bytes/s",
    }),
    "postgresql": MetricProfile("postgresql", {
        "cpu": "ratio", "memory": "ratio", "connections": "count",
        "active_connections": "count", "waiting_connections": "count",
        "connection_utilization": "ratio", "tps": "transactions/s",
        "query_latency": "ms", "locks": "count", "deadlocks": "count",
        "disk_io": "bytes/s",
    }),
    "kafka": MetricProfile("kafka", {
        "produce_rate": "messages/s", "consume_rate": "messages/s",
        "consumer_lag": "count", "errors": "count",
        "under_replicated_partitions": "count", "cpu": "ratio", "memory": "ratio",
    }),
    "redis": MetricProfile("redis", {
        "cpu": "ratio", "memory": "ratio", "connections": "count",
        "ops": "operations/s", "latency": "ms", "evictions": "count",
        "hit_rate": "ratio", "miss_rate": "ratio",
    }),
}


def register_profile(profile: MetricProfile) -> None:
    if profile.name in PROFILES:
        raise ValueError(f"profile already registered: {profile.name}")
    PROFILES[profile.name] = profile


def unit_for(metric: str) -> str:
    units = {p.metrics[metric] for p in PROFILES.values() if metric in p.metrics}
    if len(units) != 1:
        raise ValueError(f"unknown or ambiguous metric: {metric}")
    return units.pop()
