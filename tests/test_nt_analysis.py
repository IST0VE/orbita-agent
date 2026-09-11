"""Deterministic statistics, incomplete evidence and conservative policy decisions."""

import math

import pytest

from agent.nt.anomaly_detector import correlations, detect
from agent.nt.baseline import compare, deviation, statistics, summarize
from agent.nt.context import explicit_fields, validated_extraction
from agent.nt.metrics_analyzer import coverage_gaps, stable_load
from agent.nt.models import MetricSeries, timestamp, window
from agent.nt.policy import Risk, allowed, risk_of
from agent.nt.ranking import rank_services
from agent.nt.thresholds import evaluate_test, limits_from, verdict, violations


def series(metric="cpu", values=None, service="orders", times=None, unit="ratio"):
    values = [1., 1., 1., 1., 1., 10.] if values is None else values
    return MetricSeries(metric, service, times if times is not None else list(range(len(values))),
                        values, unit, "fixture").to_dict()


def test_statistics_and_zero_baseline_are_finite():
    result = statistics([1, 2, 3, 4, 5, float("nan")])
    assert result == {"count": 5, "median": 3, "p95": 4.8, "p99": 4.96, "min": 1, "max": 5, "mad": 1}
    assert deviation(10, 0) == {"baseline": 0, "current": 10, "absolute": 10, "percent": None}
    assert deviation(2250, 1900)["percent"] == pytest.approx(18.4210526)


def test_normalization_sorts_and_marks_nonfinite():
    result = series(values=[1, math.nan, 3], times=[3, 2, 1])
    assert result["timestamps"] == [1., 3.]
    assert result["values"] == [3., 1.]
    assert result["invalid_points"] == 1
    with pytest.raises(ValueError, match="duplicate"):
        series(values=[1, 2], times=[1, 1])


@pytest.mark.parametrize("value", ["2026-09-01T12:00:00", "NaN", True])
def test_timestamp_requires_explicit_timezone_and_finite_values(value):
    with pytest.raises(ValueError):
        timestamp(value)


def test_window_and_sla_validation():
    with pytest.raises(ValueError):
        window(10, 5)
    with pytest.raises(ValueError):
        limits_from({"sla_error_rate": 10})
    with pytest.raises(ValueError):
        limits_from({"sla_p95_ms": True})
    assert limits_from({"sla_error_rate": 0}) == {"error_rate": 0}


def test_threshold_equality_is_compliant_and_first_violation_is_preserved():
    found = violations([series(values=[.5, .6, .7])], {"orders": {"cpu": .5}})
    assert found[0]["count"] == 2
    assert found[0]["first_at"] == 1
    assert found[0]["peak"] == .7
    assert violations([series()], {"other": {"cpu": .1}}) == []


def test_mad_zero_and_nonzero_spikes_and_upward_trend():
    assert any(a["kind"] == "spike" for a in detect([series()], {}))
    assert any(a["kind"] == "spike" for a in detect([series(values=[1, 2, 1, 2, 1, 50])], {}))
    assert any(a["kind"] == "trend" for a in detect([series(values=list(range(1, 10)))], {}))
    assert detect([series(values=[2] * 20)], {}) == []


def test_baseline_comparison_and_units():
    current, before = summarize([series(values=[3] * 6)]), summarize([series(values=[1] * 6)])
    assert compare(current, before)["orders"]["cpu"]["percent"] == 200
    assert detect([series(values=[3] * 6)], before)[0]["kind"] == "baseline"
    before["orders"]["cpu"]["unit"] = "cores"
    assert compare(current, before) == {}


def test_correlations_align_timestamps_not_positions():
    a = series("cpu", [1, 2, 3, 4, 5, 6])
    b = series("p95", [2, 4, 6, 8, 10, 12])
    assert correlations([a, b])[0]["coefficient"] == pytest.approx(1)
    b["timestamps"] = [100 + t for t in b["timestamps"]]
    assert correlations([a, b]) == []


def test_ranking_is_stable_bounded_and_deduplicated():
    anomalies = [{"service": "orders", "metric": "cpu", "kind": "spike"}] * 100
    result = rank_services(["z", "a", "orders"], anomalies, [])
    assert result[0]["score"] == .2
    assert [r["service"] for r in result] == ["orders", "a", "z"]
    with pytest.raises(ValueError):
        rank_services([], [], [], weights={"spike": math.inf})


@pytest.mark.parametrize(("found", "missing", "completed", "sla", "expected"), [
    ([], [], True, True, "PASSED"), ([{}], ["gaps"], True, True, "FAILED"),
    ([], ["gaps"], True, True, "INCONCLUSIVE"), ([], [], False, True, "INCONCLUSIVE"),
    ([], [], True, False, "INCONCLUSIVE"),
])
def test_verdict(found, missing, completed, sla, expected):
    assert verdict(found, missing, completed=completed, has_sla=sla) == expected


def test_coverage_rejects_sparse_and_missing_points():
    assert coverage_gaps([series(values=[1] * 10)], "orders", ["cpu"], 0, 10, 1) == []
    assert coverage_gaps([series(values=[1] * 3)], "orders", ["cpu"], 0, 10, 1)
    assert coverage_gaps([], "orders", ["cpu"], 0, 10, 1)


def test_stable_load_requires_sustained_aligned_compliance():
    data = [series("rps", [100, 100, 100, 200, 200, 200], times=[0, 30, 60, 90, 120, 150]),
            series("p95", [10, 10, 10, 10, 100, 10], times=[0, 30, 60, 90, 120, 150])]
    assert stable_load(data, "orders", {"p95": 50}, 30) == 100
    assert stable_load(data, "orders", {"p95": 50, "error_rate": .01}, 30) is None
    assert stable_load(data, "orders", {}, 30) is None


@pytest.mark.parametrize(("state", "expected"), [
    ({"test_status": "completed", "critical_failure": True}, "finished"),
    ({"test_status": "failed"}, "failed"), ({"iteration": 10, "max_iterations": 10}, "stop"),
    ({"elapsed_seconds": 60, "global_timeout_seconds": 60}, "stop"),
    ({"critical_failure": True}, "stop"), ({"anomalies": [{}]}, "investigate"), ({}, "continue"),
])
def test_monitoring_policy_routes_are_deterministic(state, expected):
    assert evaluate_test(state) == expected


def test_stop_threshold_and_command_policy():
    assert evaluate_test({"metric_snapshots": [series(values=[.2])]},
                         critical_limits={"orders": {"cpu": .1}}) == "stop"
    assert allowed("get_pods")
    assert risk_of("start_test") == Risk.CONTROLLED
    assert not allowed("start_test", approved=True)
    assert allowed("stop_test", approved=True, allow_control=True)
    for command in ("execute_command", "kubectl delete", "bash", "get_pods; rm -rf /", "scale"):
        assert risk_of(command) == Risk.DANGEROUS
        assert not allowed(command, approved=True, allow_control=True)


@pytest.mark.parametrize("closing", ["", "\n", "```", "\n```"])
def test_json_input_accepts_missing_markdown_closing_fence(closing):
    text = 'Проведи анализ завершённого НТ. ```json {"test_id":"nt-run-2291"}' + closing
    assert explicit_fields(text) == {"test_id": "nt-run-2291"}


@pytest.mark.parametrize("payload", [
    '{"test_id":"nt-run-2291"',
    '{"test_id":"nt-run-2291",}',
])
def test_missing_markdown_fence_does_not_allow_invalid_json(payload):
    assert explicit_fields("Проведи анализ завершённого НТ. ```json " + payload) == {}


def test_explicit_input_and_llm_evidence_validation():
    fields = explicit_fields("service = orders\np95 < 500ms\nerrors < 1%\nstarted_at: 2026-09-01T12:00:00Z")
    assert fields["sla_p95_ms"] == 500
    assert fields["sla_error_rate"] == .01
    assert fields["target_service"] == "orders"
    assert validated_extraction('{"target_service":{"value":"orders","evidence":"service orders"}}',
                                "service orders") == {"target_service": "orders"}
    assert validated_extraction('{"sla_p95_ms":{"value":"500","evidence":"500"}}', "500") == {}
    assert validated_extraction('{"namespace":{"value":"prod","evidence":"prod"}}', "staging") == {}
