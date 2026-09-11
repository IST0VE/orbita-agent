"""Data completeness, stable observed load, and compact source-backed evidence."""

from __future__ import annotations

import json
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
    from agent.nt.phases import analyze
    return analyze(series, service, limits, step, stable_seconds=stable_seconds,
                   settling_seconds=0)["maximum_stable_rps"]


def make_evidence(state: dict, top_n: int) -> tuple[dict, dict]:
    ranked = [r for r in state.get("ranked_services", []) if r["score"] > 0][:top_n]
    selected = {r["service"] for r in ranked}
    target = state.get("target_service")
    if target:
        selected.add(target)
        neighbours = {e["to"] if e["from"] == target else e["from"]
                      for e in state.get("dependencies", []) if target in (e["from"], e["to"])}
        selected.update(sorted(neighbours)[:top_n])
    evidence = {}
    for index, finding in enumerate(state.get("threshold_violations", []) + state.get("anomalies", [])):
        if finding["service"] == target:
            evidence[f"finding:{index}"] = finding
    for service in sorted(selected, key=lambda s: (s != target, s)):
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
        previous["descriptive_metrics"] = {s: previous.get("descriptive_metrics", {}).get(s, {})
                                           for s in sorted(selected)}
    summary = {
        "question": (state.get("analysis_question") or state.get("task", ""))[:6000],
        "context": [{"kind": c["kind"], "id": c["id"], "text": c.get("text", "")[:2000]}
                    for c in state.get("context_sources", [])[:6]],
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
        "diagnostic_status": state.get("diagnostic_status"),
        "diagnostic_gaps": state.get("diagnostic_gaps", [])[:30],
        "load_phases": state.get("load_phases", [])[:16],
    }
    return evidence, summary


def compact_summary(summary: dict, limit: int = 48000) -> dict:
    """Bound the model input without deleting the durable evidence ledger."""
    result = {**summary, "evidence": dict(summary.get("evidence", {}))}
    def size():
        return len(json.dumps(result, ensure_ascii=False, allow_nan=False))
    for field in ("timeline", "correlations", "previous_comparison", "baseline_comparison",
                  "load_phases", "context", "dependencies", "top_services", "source_errors",
                  "diagnostic_gaps"):
        if size() <= limit:
            break
        result.pop(field, None)
    target = result.get("task", {}).get("target_service")
    entries = list(result["evidence"])
    positions = {key: index for index, key in enumerate(entries)}
    # Retain target findings and recent tool results before peripheral summaries.
    def priority(key):
        item = result["evidence"][key]
        if key.startswith("finding:") and item.get("service") == target:
            return 0
        if key.startswith("tool:"):
            return 1
        if item.get("service") == target:
            return 2
        return 3
    remove = sorted(entries, key=lambda key: (priority(key), -positions[key]), reverse=True)
    omitted = 0
    serialized_size = size()
    for key in remove:
        if serialized_size <= limit - 300:
            break
        item = result["evidence"].pop(key)
        serialized_size -= (len(json.dumps(key, ensure_ascii=False)) + 2
                            + len(json.dumps(item, ensure_ascii=False, allow_nan=False)) + 2)
        omitted += 1
    if omitted:
        result["omitted_evidence"] = omitted
        result["evidence_note"] = "полные улики сохранены в отчёте; не делайте выводы об отсутствующих данных"
    return result
