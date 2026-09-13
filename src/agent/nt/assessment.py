"""SLA, diagnostic completeness and capacity are independent outcomes.

Три исхода, и смешивать их нельзя: тест мог выполниться и нарушить SLA, мог
не выполниться вовсе, а мог выполниться при неполных данных — и это три разных
разговора с человеком. Поэтому здесь три поля, а не одно слово «результат».

Вердикт сопровождается происхождением: сервис, период, источники, какие именно
метрики проверены и чего не хватило. Голое «PASSED» без этого не проверяемо:
по нему нельзя сказать, чем оно измерено и за какой промежуток.

Симулированная телеметрия положительным вердиктом не становится. На стенде
исторические ряды генерирует симулятор, к трафику нагрузки они отношения не
имеют, и «PASSED» по ним доказывает работу симулятора, а не устойчивость цели.
"""

from datetime import UTC, datetime

from agent.nt import metrics_analyzer, thresholds

#: Как статус прогона читается отдельно от вердикта SLA.
EXECUTION = {
    "completed": "COMPLETED",
    "stopped": "STOPPED",
    "failed": "FAILED",
}


def execution_status(state: dict) -> str:
    """Выполнение теста: отдельно от того, уложился ли он в SLA."""
    return EXECUTION.get(str(state.get("test_status") or ""), "UNKNOWN")


def _stamp(value) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, UTC).isoformat()
    return str(value) if value else ""


def _sources_of(state: dict) -> list[str]:
    """Источники рядов, по которым считался вердикт."""
    found = {item.get("source") for item in state.get("metric_snapshots", []) if item.get("source")}
    found.update(
        stats.get("source")
        for metrics in (state.get("current_metrics") or {}).values()
        for stats in metrics.values()
        if isinstance(stats, dict) and stats.get("source")
    )
    return sorted(name for name in found if name)


def sla_verdict(state: dict, settings, *, result: str, missing: list[str]) -> dict:
    """
    Вердикт вместе с тем, чем он измерен.

    `basis` отвечает на вопрос, который в голом вердикте не виден: проверялись
    максимумы рядов за весь заявленный период, а не показатели устойчивого
    плато. Плато — отдельный расчёт (`capacity_assessment`), и выдавать один
    за другой нельзя: агрегат всего прогона включает разгон и хвост.
    """
    limits = thresholds.limits_from(state)
    sources = _sources_of(state)
    simulated = sorted(set(sources) & set(getattr(settings, "simulated_sources", ()) or ()))
    reasons: list[str] = []

    if result == "FAILED":
        reasons = [
            f"{item['service']}/{item['metric']}: пик {item['peak']} при пороге {item['limit']}"
            for item in state.get("threshold_violations", [])
        ]
    elif result == "INCONCLUSIVE":
        if not limits:
            reasons.append("не задан ни один SLA: проверять нечего")
        if state.get("test_status") != "completed":
            reasons.append(
                f"тест не завершился нормально: {state.get('test_status') or 'статус неизвестен'}"
            )
        reasons.extend(missing)

    if result == "PASSED" and simulated:
        # Отдельная ветка и отдельная причина: это не «данных не хватило»,
        # а «данные не про то». Подробности — в модульной строке документации.
        result = "INCONCLUSIVE"
        reasons.append(
            "телеметрия объявлена симулированной (" + ", ".join(simulated)
            + "): она не измеряет реакцию цели на этот прогон"
        )

    capacity = state.get("capacity_assessment") or {}
    return {
        "result": result,
        "reasons": list(dict.fromkeys(reasons)),
        "service": state.get("target_service") or "",
        "environment": state.get("environment") or "",
        "namespace": state.get("namespace") or "",
        "period": {
            "from": _stamp(state.get("started_at")),
            "to": _stamp(state.get("finished_at")),
            "seconds": _period_seconds(state),
        },
        "sources": sources,
        "simulated_sources": simulated,
        "metrics": sorted(limits),
        # Что именно измерялось: максимумы рядов за период целиком. Устойчивость
        # плато этим не доказывается — для неё есть `capacity`.
        "basis": "whole_run_maxima",
        "capacity": {
            "status": capacity.get("status", "INCONCLUSIVE"),
            "maximum_stable_rps": capacity.get("maximum_stable_rps"),
            "reason": capacity.get("reason", ""),
            "plateaus": sum(
                1 for phase in capacity.get("phases", []) if phase.get("kind") == "plateau"
            ),
        },
    }


def _period_seconds(state: dict):
    start, end = state.get("started_at"), state.get("finished_at")
    if isinstance(start, (int, float)) and isinstance(end, (int, float)):
        return max(0, int(end - start))
    return None


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
        diagnostics.append("есть ошибки источников; подробности в разделе Unverified assumptions")
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

    simulated = sorted(set(_sources_of(state)) & set(getattr(settings, "simulated_sources", ()) or ()))
    if simulated:
        diagnostics.append(
            "источники объявлены симулированными: " + ", ".join(simulated)
            + "; ряды не измеряют реакцию цели на этот прогон"
        )

    missing = list(dict.fromkeys(missing))
    result = thresholds.verdict(state["threshold_violations"], missing,
                completed=state.get("test_status") == "completed", has_sla=bool(limits))
    verdict = sla_verdict(state, settings, result=result, missing=missing)

    capacity = state.get("capacity_assessment", {})
    # Наблюдаемая устойчивая RPS — утверждение о плато. Симулированные ряды
    # его не подтверждают, поэтому число не показывается: пустое значение
    # честнее числа, которое нечем обосновать.
    maximum = None if simulated else capacity.get("maximum_stable_rps")

    return {"analysis_result": verdict["result"], "missing_parameters": missing,
            "execution_status": execution_status(state),
            "sla_verdict": verdict,
            "diagnostic_status": "PARTIAL" if diagnostics or missing else "COMPLETE",
            "diagnostic_gaps": list(dict.fromkeys(diagnostics)),
            "maximum_stable_rps": maximum}
