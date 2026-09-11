"""SLA, diagnostic completeness and capacity are independent outcomes."""

from agent.nt import metrics_analyzer, thresholds


def assess(state: dict, settings) -> dict:
    limits = thresholds.limits_from(state)
    missing = list(state.get("missing_parameters", []))
    missing.extend(metrics_analyzer.coverage_gaps(state["metric_snapshots"], state["target_service"],
                   list(limits), state["started_at"], state["finished_at"], settings.step))
    diagnostics = metrics_analyzer.baseline_gaps(state.get("baseline_metrics", {}),
                   state["target_service"], list(limits), state["baseline_start"],
                   state["baseline_end"], settings.step)
    if not state.get("baseline_metrics"):
        diagnostics.append("baseline не получен")
    unseen = sorted(set(state["services"]) - state["current_metrics"].keys())
    if unseen:
        diagnostics.append("нет исторических метрик компонентов scope: " + ", ".join(unseen[:20]))
    if state.get("source_errors"):
        diagnostics.append("есть ошибки источников; подробности в разделе Sources")
    incomplete = []
    for service, metrics in state["current_metrics"].items():
        for metric, stats in metrics.items():
            if (stats.get("partial") or stats.get("invalid_points") or stats.get("count", 0) < 3
                    or stats.get("max_gap", 0) > settings.step * 2):
                incomplete.append(f"{service}/{metric}")
    if incomplete:
        diagnostics.append("неполные диагностические ряды: " + ", ".join(sorted(incomplete)[:20]))
    if not any(p["kind"] == "plateau" for p in state.get("load_phases", [])):
        diagnostics.append("не обнаружены устойчивые плато; анализ трендов ограничен")
    if state.get("capacity_assessment", {}).get("truncated"):
        diagnostics.append("показаны первые 64 фазы; сравнение прогонов ограничено")
    result = thresholds.verdict(state["threshold_violations"], missing,
                completed=state.get("test_status") == "completed", has_sla=bool(limits))
    return {"analysis_result": result, "missing_parameters": list(dict.fromkeys(missing)),
            "diagnostic_status": "PARTIAL" if diagnostics or missing else "COMPLETE",
            "diagnostic_gaps": list(dict.fromkeys(diagnostics)),
            "maximum_stable_rps": state.get("capacity_assessment", {}).get("maximum_stable_rps")}
