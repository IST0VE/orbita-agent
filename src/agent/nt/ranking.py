"""Bounded deterministic service score, counting each signal kind once per metric."""

import math

DEFAULT_WEIGHTS = {"threshold": .6, "baseline": .25, "spike": .2, "trend": .2,
                   "counter_increase": .3, "saturation": .4}


def rank_services(services: list[str], anomalies: list[dict], violations: list[dict],
                  *, weights: dict | None = None) -> list[dict]:
    weights = DEFAULT_WEIGHTS if weights is None else weights
    if any(not math.isfinite(v) or not 0 <= v <= 1 for v in weights.values()):
        raise ValueError("score weights must be finite in [0, 1]")
    signals = {name: set() for name in services}
    for item in anomalies + [{**v, "kind": "threshold"} for v in violations]:
        signals.setdefault(item["service"], set()).add((item["metric"], item["kind"]))
    ranked = []
    for service, items in signals.items():
        complement = math.prod(1 - weights.get(kind, 0) for _, kind in sorted(items))
        score = round(1 - complement, 6)
        ranked.append({"service": service, "score": score,
                       "severity": "critical" if score >= .85 else "warning" if score else "normal",
                       "metrics": sorted({metric for metric, _ in items})})
    return sorted(ranked, key=lambda row: (-row["score"], row["service"]))
