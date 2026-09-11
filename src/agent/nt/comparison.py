"""Require comparable workload/configuration and match measured load plateaus."""

from agent.nt import baseline, phases, thresholds
from agent.nt.models import window


def compare_run(state: dict, metadata: dict, series: list[dict], settings) -> dict:
    result = {"test_id": state["previous_test_id"], "status": "NOT_COMPARABLE", "metrics": {},
              "matched_phases": [], "limitations": [],
              "descriptive_metrics": baseline.compare(state["current_metrics"], baseline.summarize(series))}
    for field in ("environment", "namespace", "target_service", "environment_fingerprint"):
        if not state.get(field) or not metadata.get(field) or state[field] != metadata[field]:
            result["limitations"].append(f"не подтверждено совпадение {field}")
    # A workload fingerprint includes operation mix/data set; a stable scenario ID
    # is accepted when fingerprints are unavailable on both sides.
    workload = "workload_fingerprint" if state.get("workload_fingerprint") or metadata.get("workload_fingerprint") else "scenario"
    if not state.get(workload) or state.get(workload) != metadata.get(workload):
        result["limitations"].append(f"не подтверждено совпадение {workload}")
    if metadata.get("scenario") != state.get("scenario"):
        result["scenario_differs"] = {"current": state.get("scenario"), "previous": metadata.get("scenario")}
    if result["limitations"]:
        return result
    start, _ = window(metadata["started_at"], metadata["finished_at"], max_seconds=settings.max_window)
    previous = phases.analyze(series, state["target_service"], thresholds.limits_from(state), settings.step,
        stable_seconds=settings.stable_seconds, settling_seconds=settings.settling_seconds,
        comparators=state.get("sla_comparators"),
        tolerance=settings.plateau_tolerance, warmup_until=start + (metadata.get("ramp_up_seconds") or 0))
    current = state["capacity_assessment"]
    if current.get("truncated") or previous.get("truncated"):
        result["limitations"].append("слишком много фаз для полного сопоставления")
        return result
    matched = phases.compare(current, previous, service=state["target_service"], tolerance=settings.plateau_tolerance)
    result.update(matched)
    if not matched["matched_phases"]:
        result["limitations"].append(matched["reason"])
    now_phases = [p for p in current.get("phases", []) if p["kind"] == "plateau"]
    old_phases = [p for p in previous.get("phases", []) if p["kind"] == "plateau"]
    if (matched["matched_phases"] and len(matched["matched_phases"]) == len(now_phases) == len(old_phases)
            and current["maximum_stable_rps"] is not None and previous["maximum_stable_rps"] is not None):
        result["stable_rps"] = baseline.deviation(current["maximum_stable_rps"], previous["maximum_stable_rps"])
        result["note"] = "устойчивая RPS сравнивается под текущими SLA на сопоставимых ступенях"
    else:
        result["limitations"].append("устойчивая RPS не сравнивается: набор ступеней или покрытие различаются")
    return result
