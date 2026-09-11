"""Data completeness, stable observed load, and compact source-backed evidence."""

from __future__ import annotations

import math


def coverage_gaps(series: list[dict], service: str, metrics: list[str], start: float,
                  end: float, step: int) -> list[str]:
    by_metric = {s["metric"]: s for s in series if s["service"] == service}
    gaps = []
    for metric in metrics:
        item = by_metric.get(metric, {})
        times = item.get("timestamps", [])
        expected = math.ceil((end - start) / step)
        if (len(times) < max(3, expected * .8) or not times
                or times[0] > start + step or times[-1] < end - 2 * step
                or any(b - a > 2 * step for a, b in zip(times, times[1:], strict=False))
                or item.get("invalid_points") or item.get("partial")):
            gaps.append(f"неполное покрытие {service}/{metric}")
    return gaps


def baseline_gaps(metrics: dict, service: str, names: list[str], start: float,
                  end: float, step: int) -> list[str]:
    gaps = []
    for metric in names:
        stats = metrics.get(service, {}).get(metric, {})
        if (stats.get("count", 0) < max(3, math.ceil((end - start) / step) * .8)
                or stats.get("start", end) > start + step or stats.get("end", start) < end - 2 * step
                or stats.get("max_gap", 0) > step * 2 or stats.get("invalid_points") or stats.get("partial")):
            gaps.append(f"неполный baseline {service}/{metric}")
    return gaps


def stable_load(series: list[dict], service: str, limits: dict, step: int,
                stable_seconds: int = 60) -> float | None:
    """Highest minimum RPS over a contiguous sustained SLA-compliant window.

This is an observed lower bound, not a capacity claim or a single good sample.
"""
    if not limits:
        return None
    data = {s["metric"]: dict(zip(s["timestamps"], s["values"], strict=True))
            for s in series if s["service"] == service}
    rps = data.get("rps", {})
    run, best = [], None
    for time, value in sorted(rps.items()):
        valid = value > 0 and all(time in data.get(m, {}) and data[m][time] <= limit
                                 for m, limit in limits.items())
        if not valid or (run and time - run[-1][0] > step * 1.5):
            run = []
        if not valid:
            continue
        run.append((time, value))
        while len(run) > 1 and time - run[1][0] >= stable_seconds:
            run.pop(0)
        if time - run[0][0] >= stable_seconds:
            observed = min(v for _, v in run)
            best = observed if best is None else max(best, observed)
    return best


def make_evidence(state: dict, top_n: int) -> tuple[dict, dict]:
    ranked = [r for r in state.get("ranked_services", []) if r["score"] > 0][:top_n]
    selected = {r["service"] for r in ranked}
    if not selected and state.get("target_service"):
        selected.add(state["target_service"])
    evidence = {}
    for service in sorted(selected):
        metrics = state.get("current_metrics", {}).get(service, {})
        for metric, stats in sorted(metrics.items()):
            evidence[f"metric:{service}:{metric}"] = {"service": service, "metric": metric, **stats}
    for index, finding in enumerate(state.get("threshold_violations", []) + state.get("anomalies", [])):
        if finding["service"] in selected:
            evidence[f"finding:{index}"] = finding
    previous = dict(state.get("previous_comparison") or {})
    if previous:
        # Сравнение прошлого прогона по всему контуру весит столько же, сколько
        # все метрики: в бриф идут только отобранные сервисы.
        previous["metrics"] = {s: previous.get("metrics", {}).get(s, {}) for s in sorted(selected)}
    summary = {
        "task": {k: state.get(k) for k in ("jira_key", "test_id", "target_service", "environment",
                    "namespace", "target_rps", "started_at", "finished_at")},
        "result": state.get("analysis_result"), "services_checked": len(state.get("current_metrics", {})),
        "top_services": ranked, "evidence": evidence,
        "dependencies": [e for e in state.get("dependencies", [])
                         if e.get("from") in selected or e.get("to") in selected][:100],
        "baseline_comparison": {s: state.get("baseline_comparison", {}).get(s, {}) for s in selected},
        "previous_comparison": previous,
        "timeline": [e for e in state.get("timeline", []) if not e.get("service")
                     or e["service"] in selected][:100],
        "correlations": [e for e in state.get("correlations", []) if e["service"] in selected][:20],
        "limitations": state.get("missing_parameters", [])[:30],
        "source_errors": state.get("source_errors", [])[:20],
        "maximum_stable_rps": state.get("maximum_stable_rps"),
    }
    return evidence, summary
