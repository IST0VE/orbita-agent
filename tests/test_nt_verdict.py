"""
Q1: вердикт НТ считается по измерениям, и три исхода не смешиваются.

Выполнение теста, соблюдение SLA и полнота диагностики отвечают на разные
вопросы, и одно слово «результат» их путает: прерванный тест — это не
«нарушены пороги», а неполные ряды — не «всё хорошо».

Второе, что здесь закрепляется, — проверяемость вердикта. Голое «PASSED»
не сообщает, за какой промежуток и по каким рядам оно получено. И третье:
свободный текст модели численный результат не переопределяет.
"""

from __future__ import annotations

import pytest

from agent.nt import assessment, report
from agent.nt.settings import Settings

SETTINGS = Settings(step=30)


def series(metric="p95", values=None, service="orders", source="prometheus"):
    """Полный ряд на весь период: иначе вердикт — INCONCLUSIVE по покрытию."""
    values = [100.0 + i for i in range(20)] if values is None else list(values)
    times = [1000.0 + 30 * i for i in range(len(values))]
    return {"metric": metric, "service": service, "timestamps": times, "values": values,
            "unit": "ms" if metric == "p95" else "ratio", "source": source,
            "partial": False, "invalid_points": 0}


def state(**extra) -> dict:
    base = {
        "test_id": "nt-run-1", "test_status": "completed",
        "target_service": "orders", "environment": "nt01", "namespace": "nt01",
        "started_at": 1000.0, "finished_at": 1600.0,
        "baseline_start": 400.0, "baseline_end": 1000.0,
        "services": ["orders"], "sla_p95_ms": 500.0,
        "metric_snapshots": [series()],
        "current_metrics": {"orders": {"p95": {"count": 20, "max_gap": 30, "median": 105.0,
                                               "source": "prometheus"}}},
        "baseline_metrics": {"orders": {"p95": {"count": 20, "median": 100.0}}},
        "threshold_violations": [], "missing_parameters": [],
        "load_phases": [{"kind": "plateau"}],
        "capacity_assessment": {"status": "ESTABLISHED", "maximum_stable_rps": 1800,
                                "phases": [{"kind": "plateau"}], "reason": "наблюдаемая оценка"},
    }
    base.update(extra)
    return base


# --------------------------------------------------------------------------
# Три исхода порознь
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("test_status", "expected"),
    [("completed", "COMPLETED"), ("stopped", "STOPPED"), ("failed", "FAILED"), ("", "UNKNOWN")],
)
def test_execution_status_is_reported_on_its_own(test_status: str, expected: str):
    assert assessment.execution_status({"test_status": test_status}) == expected


def test_a_stopped_test_never_gets_passed():
    """Главный критерий: остановленный тест не получает PASSED ни при каких рядах."""
    result = assessment.assess(state(test_status="stopped"), SETTINGS)

    assert result["execution_status"] == "STOPPED"
    assert result["analysis_result"] == "INCONCLUSIVE"
    assert any("не завершился нормально" in reason for reason in result["sla_verdict"]["reasons"])


def test_a_completed_test_within_limits_passes():
    result = assessment.assess(state(), SETTINGS)

    assert result["execution_status"] == "COMPLETED"
    assert result["analysis_result"] == "PASSED"
    assert result["sla_verdict"]["reasons"] == []


def test_a_breach_is_failed_and_names_the_metric():
    breach = {"service": "orders", "metric": "p95", "limit": 500.0, "peak": 900.0}
    result = assessment.assess(state(threshold_violations=[breach]), SETTINGS)

    assert result["analysis_result"] == "FAILED"
    assert "orders/p95" in result["sla_verdict"]["reasons"][0]


def test_diagnostic_completeness_is_separate_from_the_verdict():
    """Нет baseline — это пробел диагностики, а не нарушение SLA."""
    result = assessment.assess(state(baseline_metrics={}), SETTINGS)

    assert result["analysis_result"] == "PASSED"
    assert result["diagnostic_status"] == "PARTIAL"
    assert any("baseline" in gap for gap in result["diagnostic_gaps"])


# --------------------------------------------------------------------------
# Чем измерен вердикт
# --------------------------------------------------------------------------
def test_the_verdict_carries_its_source_period_and_service():
    verdict = assessment.assess(state(), SETTINGS)["sla_verdict"]

    assert verdict["service"] == "orders"
    assert verdict["environment"] == "nt01"
    assert verdict["sources"] == ["prometheus"]
    assert verdict["metrics"] == ["p95"]
    assert verdict["period"]["seconds"] == 600
    assert verdict["period"]["from"].startswith("1970-01-01T00:16:40")


def test_the_verdict_says_it_is_not_a_plateau_measurement():
    """Агрегат всего прогона не выдаётся за устойчивость плато."""
    verdict = assessment.assess(state(), SETTINGS)["sla_verdict"]

    assert verdict["basis"] == "whole_run_maxima"
    assert verdict["capacity"]["status"] == "ESTABLISHED"
    assert verdict["capacity"]["maximum_stable_rps"] == 1800


def test_missing_data_is_inconclusive_with_its_reasons():
    result = assessment.assess(
        state(missing_parameters=["нет ряда error_rate целевого сервиса"]), SETTINGS
    )

    assert result["analysis_result"] == "INCONCLUSIVE"
    assert "нет ряда error_rate целевого сервиса" in result["sla_verdict"]["reasons"]


def test_no_sla_at_all_is_inconclusive_not_passed():
    result = assessment.assess(state(sla_p95_ms=None), SETTINGS)

    assert result["analysis_result"] == "INCONCLUSIVE"
    assert any("не задан ни один SLA" in reason for reason in result["sla_verdict"]["reasons"])


# --------------------------------------------------------------------------
# Симулированная телеметрия
# --------------------------------------------------------------------------
def test_simulated_telemetry_never_proves_a_plateau():
    settings = Settings(step=30, simulated_sources=("prometheus",))

    result = assessment.assess(state(), settings)

    assert result["analysis_result"] == "INCONCLUSIVE"
    assert any("симулированной" in reason for reason in result["sla_verdict"]["reasons"])
    # Наблюдаемая устойчивая RPS — утверждение о плато: обосновать его нечем.
    assert result["maximum_stable_rps"] is None
    assert result["sla_verdict"]["simulated_sources"] == ["prometheus"]


def test_a_breach_on_simulated_telemetry_is_still_a_breach():
    """Отказ симулятор подтвердить может: нарушение порога остаётся нарушением."""
    settings = Settings(step=30, simulated_sources=("prometheus",))
    breach = {"service": "orders", "metric": "p95", "limit": 500.0, "peak": 900.0}

    result = assessment.assess(state(threshold_violations=[breach]), settings)

    assert result["analysis_result"] == "FAILED"


def test_an_undeclared_source_is_not_treated_as_simulated():
    settings = Settings(step=30, simulated_sources=("influx",))

    assert assessment.assess(state(), settings)["analysis_result"] == "PASSED"


# --------------------------------------------------------------------------
# Текст модели не спорит с числами
# --------------------------------------------------------------------------
def test_model_text_does_not_change_the_computed_verdict():
    computed = assessment.assess(state(test_status="stopped"), SETTINGS)
    hostile = {
        **state(test_status="stopped"),
        **computed,
        # Ровно то, что модель пишет чаще всего: уверенный вывод мимо чисел.
        "artifacts": {"report": "Тест пройден, SLA соблюдены, цель достигнута: PASSED."},
        "conclusion": "PASSED, система держит нагрузку",
        "hypothesis_assessment": {"summary": "PASSED"},
    }

    text = report.render_report(hostile)

    assert "## Result\n\nINCONCLUSIVE" in text
    assert "## Execution\n\nSTOPPED" in text
    assert "не меняется формулировками модели" in text


def test_the_report_shows_what_the_verdict_was_measured_by():
    computed = assessment.assess(state(), SETTINGS)

    text = report.render_report({**state(), **computed})

    assert "nt01" in text
    assert "prometheus" in text
    assert "максимумы рядов за период целиком" in text
