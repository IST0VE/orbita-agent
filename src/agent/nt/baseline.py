"""Descriptive statistics and comparisons; no LLM arithmetic."""

from __future__ import annotations

import math
from statistics import median


def percentile(values: list[float], q: float) -> float:
    if not values or not 0 <= q <= 1:
        raise ValueError("percentile requires values and q in [0, 1]")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def statistics(values: list[float]) -> dict:
    values = [float(v) for v in values if math.isfinite(float(v))]
    if not values:
        return {}
    center = median(values)
    return {"count": len(values), "median": center, "p95": percentile(values, .95),
            "p99": percentile(values, .99), "min": min(values), "max": max(values),
            "mad": median([abs(v - center) for v in values])}


def deviation(current: float, baseline: float) -> dict:
    return {"baseline": baseline, "current": current, "absolute": current - baseline,
            "percent": (current - baseline) / abs(baseline) * 100 if baseline else None}


def summarize(series: list[dict]) -> dict:
    result = {}
    for item in series:
        stats = statistics(item["values"])
        if stats:
            result.setdefault(item["service"], {})[item["metric"]] = {
                **stats, "unit": item["unit"], "source": item["source"],
                "start": item["timestamps"][0], "end": item["timestamps"][-1],
                "invalid_points": item.get("invalid_points", 0),
                "partial": bool(item.get("partial")),
                "max_gap": max((b - a for a, b in zip(item["timestamps"], item["timestamps"][1:],
                                                     strict=False)), default=0),
            }
    return result


def compare(current: dict, baseline: dict) -> dict:
    result = {}
    for service, metrics in current.items():
        for metric, stats in metrics.items():
            before = baseline.get(service, {}).get(metric)
            if before and before["unit"] == stats["unit"]:
                result.setdefault(service, {})[metric] = deviation(stats["median"], before["median"])
    return result
