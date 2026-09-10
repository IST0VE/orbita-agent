"""Robust spikes, upward trends, baseline changes and aligned correlations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations
from statistics import median

from agent.nt.baseline import deviation


@dataclass(frozen=True)
class DetectorConfig:
    robust_z: float = 3.5
    baseline_relative: float = 1.0
    trend_relative: float = .25
    window: int = 5
    correlation: float = .8

    def __post_init__(self):
        if self.window < 3 or min(self.robust_z, self.baseline_relative, self.trend_relative) <= 0:
            raise ValueError("invalid detector configuration")
        if not 0 <= self.correlation <= 1:
            raise ValueError("invalid correlation threshold")


def detect(series: list[dict], baseline: dict, config: DetectorConfig | None = None) -> list[dict]:
    config = config or DetectorConfig()
    found = []
    for item in series:
        values, times = item["values"], item["timestamps"]
        if not values:
            continue
        common = {"service": item["service"], "metric": item["metric"],
                  "unit": item["unit"], "source": item["source"]}
        before = baseline.get(item["service"], {}).get(item["metric"], {})
        if before and before.get("unit") == item["unit"]:
            delta = deviation(median(values), before["median"])
            # Zero baselines have no meaningful percentage; spikes still work.
            if delta["percent"] is not None and delta["percent"] >= config.baseline_relative * 100:
                found.append({**common, "kind": "baseline", "first_at": times[0], **delta})
        spikes, trends = [], []
        for index in range(config.window, len(values)):
            previous = values[index - config.window:index]
            center = median(previous)
            mad = median([abs(v - center) for v in previous])
            # MAD=0 is common for constant baselines: explicit absolute change,
            # without infinite scores in checkpoints or LLM JSON.
            distance = abs(values[index] - center)
            z = .67448975 * distance / mad if mad else None
            if (z is not None and z > config.robust_z) or (
                mad == 0 and distance > max(abs(center) * config.trend_relative, 1e-9)
            ):
                spikes.append({"at": times[index], "value": values[index], "robust_z": z})
            tail = values[index - config.window:index + 1]
            if all(b > a for a, b in zip(tail, tail[1:], strict=False)) and (
                tail[-1] - tail[0] > max(abs(tail[0]) * config.trend_relative, 1e-9)
            ):
                trends.append(times[index - config.window])
        if spikes:
            found.append({**common, "kind": "spike", "first_at": spikes[0]["at"],
                          "count": len(spikes), "examples": spikes[:3]})
        if trends:
            found.append({**common, "kind": "trend", "first_at": trends[0], "count": len(trends)})
    return found


def correlations(series: list[dict], minimum: float = .8) -> list[dict]:
    grouped = {}
    for item in series:
        grouped.setdefault(item["service"], []).append(item)
    result = []
    for service, items in grouped.items():
        for left, right in combinations(items, 2):
            a = dict(zip(left["timestamps"], left["values"], strict=True))
            b = dict(zip(right["timestamps"], right["values"], strict=True))
            times = sorted(a.keys() & b.keys())
            if len(times) < 5:
                continue
            x, y = [a[t] for t in times], [b[t] for t in times]
            mx, my = sum(x) / len(x), sum(y) / len(y)
            dx, dy = [v - mx for v in x], [v - my for v in y]
            denominator = math.sqrt(sum(v * v for v in dx) * sum(v * v for v in dy))
            if not denominator:
                continue
            coefficient = sum(a * b for a, b in zip(dx, dy, strict=True)) / denominator
            if abs(coefficient) >= minimum:
                result.append({"service": service, "metrics": [left["metric"], right["metric"]],
                               "coefficient": max(-1., min(1., coefficient)), "samples": len(times)})
    return sorted(result, key=lambda row: (-abs(row["coefficient"]), row["service"]))
