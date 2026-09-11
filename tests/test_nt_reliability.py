"""Regression and scenario checks for repeatable historical NT analysis."""

from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from test_nt_graph import INPUT, START, model, run, setup_source

from agent import nt_graph
from agent.nt import comparison, hypotheses, phases
from agent.nt.anomaly_detector import detect
from agent.nt.metrics_analyzer import make_evidence
from agent.nt.models import MetricSeries
from agent.nt.query_guard import validate
from agent.nt.settings import Settings
from agent.nt_tools import build_tools


def metric(name, values, *, service="svc-0", start=START, step=30, **extra):
    unit = "requests/s" if name == "rps" else "ms" if name == "p95" else "ratio"
    return {**MetricSeries(name, service, [start + i * step for i in range(len(values))],
                          values, unit, "prometheus").to_dict(), **extra}


@pytest.mark.parametrize("query", [
    'sum by (service) (rate(x{exported_namespace="nt01",namespace="prod"}[5m]))',
    'sum by (service) (x{other_namespace="nt01"})',
    "sum by (service) (x{note='namespace=\"nt01\"'})",
    'sum by (service) (x{namespace="nt01",namespace="prod"})',
    'sum by (service) (x{namespace=~"nt01"})',
    'x{namespace="nt01",note="by (service)"}',
    'sum by (service) (x{namespace="nt01"}) + sum',
    'sum by (service) ({namespace="nt01"})',
])
def test_guard_rejects_namespace_and_aggregation_bypasses(query):
    assert validate(query, "nt01")


def app_for_repeat(sources):
    return nt_graph.build_graph(model(*[AIMessage(content="{}") for _ in range(5)]), sources=sources).compile(
        checkpointer=InMemorySaver())


def test_message_sla_correction_changes_verdict_in_same_thread():
    sources, _ = setup_source(anomaly=False)
    app = app_for_repeat(sources)
    config = {"configurable": {"thread_id": "correct", "publish": False}}
    first = app.invoke({**INPUT, "messages": [HumanMessage("Анализ")]}, config)
    second = app.invoke({"messages": [HumanMessage("sla_p95_ms=50")]}, config)
    assert first["analysis_result"] == "PASSED"
    assert second["sla_p95_ms"] == 50
    assert second["analysis_result"] == "FAILED"
    assert second["test_id"] == first["test_id"]


@pytest.mark.parametrize("direct", [False, True])
def test_missing_input_can_be_repaired_without_inheriting_not_started(direct):
    sources, _ = setup_source(anomaly=False)
    app = app_for_repeat(sources)
    config = {"configurable": {"thread_id": "repair", "publish": False}}
    app.invoke({**INPUT, "sla_p95_ms": None, "sla_error_rate": None,
                "messages": [HumanMessage("Анализ")]}, config)
    correction = {"sla_p95_ms": 500} if direct else {}
    state = app.invoke({**correction, "messages": [HumanMessage("sla_p95_ms=500")]}, config)
    assert state["precheck_result"]["success"]
    assert state["test_status"] == "completed"
    assert state["analysis_result"] == "PASSED"


def test_new_test_id_does_not_inherit_previous_window_or_sla():
    sources, prom = setup_source(anomaly=False)
    app = app_for_repeat(sources)
    config = {"configurable": {"thread_id": "new-test", "publish": False}}
    app.invoke({**INPUT, "messages": [HumanMessage("Анализ")]}, config)
    count = len(prom.calls)
    state = app.invoke({"messages": [HumanMessage("test_id=new-test")]}, config)
    assert state["test_id"] == "new-test"
    assert state["started_at"] is None
    assert state["sla_p95_ms"] is None
    assert not state["precheck_result"]["success"]
    assert len(prom.calls) == count


def test_gateway_metadata_is_refreshed_instead_of_becoming_operator_input():
    sources, _ = setup_source(anomaly=False)
    class Gateway:
        limit = 500
        def get_test_results(self, test_id):
            return {"success": True, "data": {**INPUT, "sla_p95_ms": self.limit, "test_status": "completed"}}
    gateway = Gateway()
    sources.load_testing = gateway
    app = app_for_repeat(sources)
    config = {"configurable": {"thread_id": "refresh", "publish": False}}
    app.invoke({"messages": [HumanMessage("test_id=load-123")]}, config)
    gateway.limit = 50
    state = app.invoke({"messages": [HumanMessage("Повтори анализ")]}, config)
    assert state["sla_p95_ms"] == 50
    assert state["analysis_result"] == "FAILED"


def test_current_kubernetes_inventory_does_not_define_historical_scope():
    sources, _ = setup_source(anomaly=False)
    class Kubernetes:
        def get_deployments(self, namespace):
            return {"success": True, "data": [{"service": "created-after-test"}]}
    sources.kubernetes = Kubernetes()
    state = run(sources, AIMessage(content="{}"))
    assert state["analysis_result"] == "PASSED"
    assert "created-after-test" not in state["services"]
    assert state["current_inventory"] == ["created-after-test"]


def test_explicit_dependency_without_metrics_limits_diagnosis_only():
    sources, _ = setup_source(anomaly=False)
    state = run(sources, AIMessage(content="{}"), extra={"services": ["svc-0", "database-missing"]})
    assert state["analysis_result"] == "PASSED"
    assert state["diagnostic_status"] == "PARTIAL"
    assert any("database-missing" in gap for gap in state["diagnostic_gaps"])


def test_generated_query_rejects_multiple_pods_per_service(monkeypatch):
    sources, prom = setup_source()
    sources.settings = replace(sources.settings, generated_queries=True)
    monkeypatch.setattr(prom, "range_query", lambda *a, **kw: {"success": True, "series": [
        metric("composed", [100] * 5), metric("composed", [1] * 5)]})
    query = next(t for t in build_tools(sources) if t.name == "run_metric_query")
    result = query.invoke({"promql": 'max by (service, pod) (queue_depth{namespace="nt01"})',
                           "purpose": "Очередь", "state": {**INPUT, "services": ["svc-0"]}})
    assert result["error_type"] == "AMBIGUOUS_SERIES"


def test_question_target_and_neighbours_survive_top_n_selection():
    state = {"task": "Проверь изменение пула соединений", "target_service": "target",
             "ranked_services": [{"service": "other", "score": 1}],
             "dependencies": [{"from": "target", "to": "db"}],
             "current_metrics": {name: {"cpu": {"max": .2}} for name in ("target", "db", "other")},
             "context_sources": [{"kind": "jira", "id": "NT-1", "text": "Изменён пул"}]}
    evidence, brief = make_evidence(state, 1)
    assert {f"metric:{s}:cpu" for s in ("target", "db", "other")} <= evidence.keys()
    assert brief["question"] == state["task"]
    assert brief["context"][0]["text"] == "Изменён пул"


def test_ramp_and_expected_traffic_growth_do_not_establish_capacity_or_anomalies():
    data = [metric("rps", [100 * i for i in range(1, 21)]), metric("p95", [10] * 20)]
    result = phases.analyze(data, "svc-0", {"p95": 50}, 30)
    assert result["maximum_stable_rps"] is None
    assert not detect([metric("rps", [1, 1, 1, 1, 1, 100])], {})
    assert not detect([metric("network", [1, 1, 1, 1, 1, 100])], {})


def test_late_degradation_disqualifies_whole_plateau():
    data = [metric("rps", [1000] * 20), metric("p95", [10] * 15 + [100] * 5)]
    result = phases.analyze(data, "svc-0", {"p95": 50}, 30)
    assert result["maximum_stable_rps"] is None
    assert result["phases"][0]["sla_status"] == "FAILED"


def test_capacity_uses_clean_lower_plateau_and_ignores_settling():
    data = [metric("rps", [500] * 10 + [1000] * 10),
            metric("p95", [100] + [10] * 9 + [100] * 10)]
    result = phases.analyze(data, "svc-0", {"p95": 50}, 30, settling_seconds=30)
    assert result["maximum_stable_rps"] == 500
    assert [p["sla_status"] for p in result["phases"]] == ["PASSED", "FAILED"]


@pytest.mark.parametrize("extra", [{"partial": True}, {"invalid_points": 1}])
def test_uncertain_rps_never_establishes_capacity(extra):
    data = [metric("rps", [1000] * 10, **extra), metric("p95", [10] * 10)]
    assert phases.analyze(data, "svc-0", {"p95": 50}, 30)["maximum_stable_rps"] is None


def test_missing_sla_sample_disqualifies_plateau():
    latency = metric("p95", [10] * 10)
    latency["timestamps"].pop(5)
    latency["values"].pop(5)
    result = phases.analyze([metric("rps", [1000] * 10), latency], "svc-0", {"p95": 50}, 30)
    assert result["maximum_stable_rps"] is None
    assert result["phases"][0]["missing_metrics"] == ["p95"]


def test_saturation_and_restart_scenarios_have_specific_observations():
    data = [metric("cpu", [.95] * 10), metric("pod_restarts", [7] * 5 + [8] * 5)]
    found = detect(data, {}, load_phases=[])
    assert {(f["metric"], f["kind"]) for f in found} == {("cpu", "saturation"), ("pod_restarts", "counter_increase")}
    assert next(f for f in found if f["metric"] == "pod_restarts")["count"] == 1
    assert not detect([metric("pod_restarts", [8] * 10)], {})
    assert not detect([metric("p95", [100] * 5 + [10])], {})


def comparison_fixture():
    current_series = [metric("rps", [1000] * 10), metric("p95", [100] * 10)]
    old_series = [metric("rps", [1000] * 10, start=START-3600), metric("p95", [50] * 10, start=START-3600)]
    state = {**INPUT, "sla_error_rate": None, "previous_test_id": "old", "scenario": "checkout", "environment_fingerprint": "4cpu-8gb",
             "current_metrics": {}, "capacity_assessment": phases.analyze(current_series, "svc-0", {"p95": 500}, 30)}
    old = {**state, "started_at": START-3600, "finished_at": START-3300}
    return state, old, old_series


def test_regression_requires_matched_workload_environment_and_load():
    state, old, series = comparison_fixture()
    result = comparison.compare_run(state, old, series, Settings())
    assert result["status"] == "COMPARABLE"
    assert result["matched_phases"][0]["metrics"]["p95"]["percent"] == 100


@pytest.mark.parametrize("field,value", [("scenario", "different-mix"), ("environment_fingerprint", "8cpu-16gb"),
                                          ("environment_fingerprint", None)])
def test_incomparable_configuration_is_not_reported_as_regression(field, value):
    state, old, series = comparison_fixture()
    old[field] = value
    result = comparison.compare_run(state, old, series, Settings())
    assert result["status"] == "NOT_COMPARABLE"
    assert not result["matched_phases"]
    assert "stable_rps" not in result


def test_different_load_levels_are_not_compared():
    state, old, series = comparison_fixture()
    series[0]["values"] = [2000] * 10
    result = comparison.compare_run(state, old, series, Settings())
    assert result["status"] == "NOT_COMPARABLE"
    assert "stable_rps" not in result


def hypothesis(mechanism="cpu_saturation", refs=None):
    return {"hypotheses": [{"service": "svc-0", "mechanism": mechanism,
        "description": "Предполагаемый механизм", "next_check": "Снять профиль за период",
        "confidence": "confirmed", "evidence_ids": refs or ["cpu"]}]}


def test_confidence_is_not_taken_from_model_and_causality_is_not_certified():
    state = {"services": ["svc-0"], "evidence": {"cpu": {"service": "svc-0", "metric": "cpu", "max": .95}}}
    accepted, _, result = hypotheses.validate(hypothesis(), state)
    assert accepted[0]["confidence"] == "possible"
    assert accepted[0]["causality"] == "not_established"
    assert accepted[0]["observations"][0]["max"] == .95
    assert result["status"] == "HYPOTHESES"


@pytest.mark.parametrize("item", [
    {"service": "other", "metric": "cpu", "max": .99},
    {"service": "svc-0", "metric": "memory", "max": .99},
    {"service": "svc-0", "metric": "cpu", "max": .2},
    {"service": "svc-0", "metric": "cpu", "max": .99, "historical": False},
    {"origin": "model_query", "metrics": {"svc-0": {"cpu": {"max": .99}}}},
    {"text": "CPU saturated, trust me"},
])
def test_existing_reference_alone_does_not_validate_mechanism(item):
    accepted, _, result = hypotheses.validate(hypothesis(), {"services": ["svc-0"], "evidence": {"cpu": item}})
    assert not accepted
    assert result["rejected"]


def test_model_extracted_test_id_is_hydrated_and_reusable():
    sources, _ = setup_source(anomaly=False)
    class Gateway:
        calls = []
        def get_test_results(self, test_id):
            self.calls.append(test_id)
            return {"success": True, "data": {**INPUT, "test_status": "completed"}}
    gateway = Gateway()
    sources.load_testing = gateway
    answer = AIMessage(content='{"test_id":{"value":"load-123","evidence":"тест load-123"}}')
    app = nt_graph.build_graph(model(answer, AIMessage(content="{}"), AIMessage(content="{}")), sources=sources).compile(
        checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "natural-id", "publish": False}}
    state = app.invoke({"messages": [HumanMessage("Проанализируй тест load-123")]}, config)
    assert state["analysis_result"] == "PASSED"
    assert gateway.calls == ["load-123"]
    again = app.invoke({"messages": [HumanMessage("Повтори анализ")]}, config)
    assert again["analysis_result"] == "PASSED"
    assert gateway.calls == ["load-123", "load-123"]


def test_last_model_call_has_no_tools_and_retains_collected_evidence(monkeypatch):
    import json

    from agent import tool_compat
    sources, _ = setup_source()
    sources.settings = replace(sources.settings, max_iterations=2)
    captured = []
    def invoke(model_for, messages, config, tools, *, allow_tools):
        captured.append((messages, tools, allow_tools))
        if len(captured) == 1:
            return AIMessage(content="", tool_calls=[{"name": "prometheus_range_query", "id": "cpu-final",
                "args": {"metric": "cpu", "service": "svc-0"}}])
        return AIMessage(content=json.dumps(hypothesis(refs=["tool:cpu-final"])))
    monkeypatch.setattr(tool_compat, "invoke", invoke)
    state = nt_graph.build_graph(sources=sources).compile().invoke(
        {**INPUT, "messages": [HumanMessage("Проверь троттлинг CPU")]}, {"configurable": {"publish": False}})
    assert len(captured) == 2
    assert captured[0][2] is True
    assert captured[1][1] == () and captured[1][2] is False
    final_input = json.loads(captured[1][0][-1].content)
    assert "tool:cpu-final" in final_input["evidence"]
    assert final_input["question"] == "Проверь троттлинг CPU"
    assert state["root_cause_hypotheses"][0]["confidence"] == "possible"


def test_compacting_context_keeps_full_ledger_and_recent_tool_results():
    import json

    from agent.nt.metrics_analyzer import compact_summary
    evidence = {f"metric:other:{i}": {"service": "other", "text": "a" * 2000} for i in range(30)}
    evidence["tool:recent"] = {"success": True, "metrics": {"svc-0": {"cpu": {"max": .95}}}}
    summary = {"question": "Investigate CPU", "task": {"target_service": "svc-0"}, "evidence": evidence}
    result = compact_summary(summary)
    assert len(json.dumps(result, ensure_ascii=False)) <= 48000
    assert len(evidence) == 31
    assert result["question"] == summary["question"]
    assert "tool:recent" in result["evidence"]
    assert result["omitted_evidence"] > 0


def test_failed_but_incomplete_plateau_cannot_be_used_for_comparison():
    state, old, series = comparison_fixture()
    state["sla_error_rate"] = .01
    # Old p95 breaches SLA while error_rate is missing: FAILED must not imply complete.
    series[1]["values"] = [600] * 10
    result = comparison.compare_run(state, old, series, Settings())
    assert result["status"] == "NOT_COMPARABLE"
    assert not result["matched_phases"]


def test_different_plateau_durations_are_not_used_as_regression_evidence():
    state, old, _ = comparison_fixture()
    series = [metric("rps", [1000] * 20, start=START-3600), metric("p95", [100] * 20, start=START-3600)]
    old["finished_at"] = START-3000
    assert comparison.compare_run(state, old, series, Settings())["status"] == "NOT_COMPARABLE"


def test_low_cpu_spike_does_not_support_cpu_saturation():
    item = {"service": "svc-0", "metric": "cpu", "kind": "spike", "first_at": START,
            "examples": [{"value": .2}]}
    accepted, _, result = hypotheses.validate(hypothesis(), {"services": ["svc-0"], "evidence": {"cpu": item}})
    assert not accepted and result["rejected"]


def test_incomplete_non_sla_metric_is_visible_in_diagnostic_status(monkeypatch):
    sources, prom = setup_source(anomaly=False)
    original = prom.range_query
    def query(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs["metric"] == "cpu":
            for row in result["series"]:
                row["invalid_points"] = 1
        return result
    monkeypatch.setattr(prom, "range_query", query)
    state = run(sources, AIMessage(content="{}"))
    assert state["analysis_result"] == "PASSED"
    assert state["diagnostic_status"] == "PARTIAL"
    assert any("/cpu" in gap for gap in state["diagnostic_gaps"])


@pytest.mark.parametrize("expression,expected", [("p(95)<500", True), ("p(95)<=500", False)])
def test_gateway_comparator_is_preserved_for_sla_and_capacity(expression, expected):
    from agent.integrations.load_testing import threshold_fields
    from agent.nt.thresholds import limits_from, violations
    comparators = {}
    fields, errors = threshold_fields([{"metric": "http_req_duration", "expression": expression}], comparators=comparators)
    assert not errors
    data = [metric("rps", [1000] * 10), metric("p95", [500] * 10)]
    breached = violations(data, {"svc-0": limits_from(fields)}, comparators=comparators)
    assert bool(breached) is expected
    result = phases.analyze(data, "svc-0", limits_from(fields), 30, comparators=comparators)
    assert (result["maximum_stable_rps"] is None) is expected


def test_plain_text_sla_keeps_strictness_and_accepts_less_or_equal():
    from agent.nt.context import explicit_fields
    strict = explicit_fields("p95 < 500ms\nerrors < 1%")
    inclusive = explicit_fields("p95 <= 500ms\nerrors <= 1%")
    assert strict["sla_comparators"] == {"p95": "<", "error_rate": "<"}
    assert inclusive["sla_comparators"] == {"p95": "<=", "error_rate": "<="}


def test_invalid_comparator_with_prose_is_rejected_without_crash():
    from agent.nt.context import explicit_fields, validate_fields
    fields = explicit_fields('```json\n{"sla_comparators": []}\n```\np95 < 500ms')
    _, errors = validate_fields(fields)
    assert errors == ["неверный параметр: sla_comparators"]


def test_gateway_strict_limit_and_operator_numeric_override_work_end_to_end():
    import responses

    from agent.integrations.load_testing import HTTPLoadTesting
    sources, _ = setup_source(anomaly=False)
    sources.load_testing = HTTPLoadTesting("https://gateway.test")
    card = {k: v for k, v in INPUT.items() if not k.startswith("sla_")}
    app = app_for_repeat(sources)
    config = {"configurable": {"thread_id": "strict-http", "publish": False}}
    with responses.RequestsMock() as mock:
        mock.get("https://gateway.test/tests/load-123", json={**card, "status": "completed"})
        mock.get("https://gateway.test/tests/load-123/results", json={
            "thresholds": [{"metric": "http_req_duration", "expression": "p(95)<100"}]})
        first = app.invoke({"messages": [HumanMessage("test_id=load-123")]}, config)
        assert first["analysis_result"] == "FAILED"
        assert first["sla_comparators"] == {"p95": "<"}
        assert first["threshold_violations"][0]["peak"] == 100
        corrected = app.invoke({"messages": [HumanMessage("sla_p95_ms=100")]}, config)
        assert corrected["analysis_result"] == "PASSED"
        assert corrected["maximum_stable_rps"] == 1000
        assert corrected["sla_comparators"].get("p95", "<=") == "<="


def test_sdk_sla_update_does_not_reparse_old_human_message():
    sources, _ = setup_source(anomaly=False)
    app = app_for_repeat(sources)
    config = {"configurable": {"thread_id": "sdk-correction", "publish": False}}
    app.invoke({**INPUT, "messages": [HumanMessage("Проверь CPU\nsla_p95_ms=500")]}, config)
    state = app.invoke({"sla_p95_ms": 50}, config)
    assert state["sla_p95_ms"] == 50
    assert state["analysis_result"] == "FAILED"


def test_followup_keeps_original_investigation_question():
    sources, _ = setup_source(anomaly=False)
    app = app_for_repeat(sources)
    config = {"configurable": {"thread_id": "question-correction", "publish": False}}
    app.invoke({**INPUT, "messages": [HumanMessage("Проверь изменение пула соединений")]}, config)
    state = app.invoke({"messages": [HumanMessage("sla_p95_ms=50")]}, config)
    assert "изменение пула соединений" in state["llm_summary"]["question"]
    assert "sla_p95_ms=50" in state["llm_summary"]["question"]


def test_extreme_numeric_input_produces_precheck_report():
    sources, prom = setup_source()
    state = run(sources, AIMessage(content="{}"), extra={"target_rps": 10 ** 500})
    assert not state["precheck_result"]["success"]
    assert not prom.calls
    assert "target_rps" in state["artifacts"]["report"]
