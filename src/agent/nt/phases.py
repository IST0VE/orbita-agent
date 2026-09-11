"""Observed load plateaus, sustained capacity and comparison at matched load.

No interpolation, averaging exported percentiles, or mixing populations. A
plateau must remain inside a relative RPS band for the whole measured interval.
The settling interval is excluded; every subsequent SLA sample must comply.
"""

from statistics import median

from agent.nt import baseline
from agent.nt.thresholds import breaches


def analyze(series: list[dict], service: str, limits: dict, step: int, *,
            stable_seconds: int = 60, settling_seconds: int = 30,
            tolerance: float = .1, warmup_until: float | None = None,
            comparators: dict | None = None) -> dict:
    items = {s["metric"]: s for s in series if s["service"] == service}
    rps = items.get("rps", {})
    data = {m: dict(zip(s["timestamps"], s["values"], strict=True)) for m, s in items.items()}
    if not rps.get("values") or rps.get("partial") or rps.get("invalid_points"):
        return {"status": "INCONCLUSIVE", "maximum_stable_rps": None, "phases": [],
                "reason": "нет полного корректного ряда RPS"}
    segments, run, low, high = [], [], 0., 0.
    for at, value in zip(rps["timestamps"], rps["values"], strict=True):
        if run and (at - run[-1][0] > step * 1.5 or value <= 0
                    or max(high, value) - min(low, value) > tolerance * min(low, value)):
            segments.append(run)
            run = []
        if value <= 0 or (warmup_until is not None and at < warmup_until):
            continue
        low, high = (min(low, value), max(high, value)) if run else (value, value)
        run.append((at, value))
    if run:
        segments.append(run)
    phases, candidates = [], []
    for points in segments:
        start, end = points[0][0], points[-1][0]
        measured = [(t, v) for t, v in points if t >= start + settling_seconds]
        sustained = (len(measured) >= 3
                     and measured[-1][0] - measured[0][0] >= stable_seconds)
        phase = {"start": start, "end": end, "duration_seconds": end - start,
                 "kind": "plateau" if sustained else "transition",
                 "rps_min": min(v for _, v in points), "rps_max": max(v for _, v in points),
                 "rps_median": median(v for _, v in points)}
        if sustained:
            times = [t for t, _ in measured]
            missing = [m for m in limits if not items.get(m) or items[m].get("partial")
                       or items[m].get("invalid_points") or any(t not in data[m] for t in times)]
            breached = [m for m, limit in limits.items()
                        if any(breaches(data.get(m, {}).get(t, float("-inf")), limit,
                               (comparators or {}).get(m, "<=")) for t in times)]
            phase.update(measured_start=times[0], samples=len(times), missing_metrics=missing,
                         violated_metrics=breached,
                         sla_status="FAILED" if breached else "INCONCLUSIVE" if missing or not limits else "PASSED")
            phase["metrics"] = {m: {**baseline.statistics([values[t] for t in times]),
                                   "unit": items[m]["unit"]}
                                for m, values in data.items() if all(t in values for t in times)
                                and not items[m].get("partial") and not items[m].get("invalid_points")}
            if phase["sla_status"] == "PASSED":
                candidates.append(min(v for _, v in measured))
        phases.append(phase)
    truncated = len(phases) > 64
    return {"status": "ESTABLISHED" if candidates and not truncated else "INCONCLUSIVE",
            "maximum_stable_rps": max(candidates) if candidates and not truncated else None,
            "phases": phases[:64], "truncated": truncated,
            "settling_seconds": settling_seconds, "stable_seconds": stable_seconds,
            "rps_tolerance": tolerance,
            "reason": "число фаз превышает лимит 64; сузьте окно анализа" if truncated else
                      "наблюдаемая нижняя оценка на плато; предел мощности не доказан"
                      if candidates else "нет устойчивого плато с полным соблюдением SLA"}


def compare(current: dict, previous: dict, *, service: str, tolerance: float = .1) -> dict:
    """Match complete target-service plateaus by RPS, without reusing a plateau."""
    before = [p for p in previous.get("phases", []) if p["kind"] == "plateau"
              and p.get("sla_status") != "INCONCLUSIVE" and not p.get("missing_metrics")]
    matched, used = [], set()
    for now in current.get("phases", []):
        if now["kind"] != "plateau" or now.get("sla_status") == "INCONCLUSIVE" or now.get("missing_metrics"):
            continue
        duration = now["end"] - now["measured_start"]
        choices = [(abs(now["rps_median"] - p["rps_median"]) / p["rps_median"], i, p)
                   for i, p in enumerate(before) if i not in used and p["rps_median"] > 0
                   and abs(duration - (p["end"] - p["measured_start"])) <= .2 * min(duration, p["end"] - p["measured_start"])]
        if not choices:
            continue
        distance, index, old = min(choices, key=lambda choice: (choice[0], choice[1]))
        if distance > tolerance:
            continue
        used.add(index)
        metrics = {m: baseline.deviation(stats["median"], old["metrics"][m]["median"])
                   for m, stats in now["metrics"].items() if m in old["metrics"]
                   and stats["unit"] == old["metrics"][m]["unit"]}
        matched.append({"service": service, "current_start": now["measured_start"],
                        "previous_start": old["measured_start"], "current_rps": now["rps_median"],
                        "previous_rps": old["rps_median"], "metrics": metrics})
    return {"status": "COMPARABLE" if matched else "NOT_COMPARABLE", "matched_phases": matched,
            "reason": "сравнение медиан рядов на сопоставимой нагрузке" if matched
                      else "нет полных плато с сопоставимой RPS"}
