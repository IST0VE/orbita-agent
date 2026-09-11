"""End-to-end historical workflow with 25 services and no real network or model."""

import json

import pytest
import responses
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent import nt_graph
from agent.integrations.prometheus import Prometheus
from agent.nt.collection import Sources
from agent.nt.models import MetricSeries
from agent.nt.settings import Settings

START = 1788220800.0
END = START + 300
INPUT = {"target_service": "svc-0", "environment": "nt", "namespace": "nt01", "test_id": "load-123",
         "started_at": START, "finished_at": END, "sla_p95_ms": 500, "sla_error_rate": .01}


class FakePrometheus:
    def __init__(self, *, anomaly=True, missing=None):
        self.calls = []
        self.anomaly, self.missing = anomaly, missing

    def range_query(self, query, start, end, step, *, metric, unit):
        self.calls.append((metric, start, end))
        if self.missing == metric:
            return {"success": False, "error_type": "PROMETHEUS_UNAVAILABLE", "message": "timeout"}
        rows = []
        for index in range(25):
            times = list(range(int(start), int(end), int(step)))
            normal = {"p95": 100, "error_rate": .001, "cpu": .2, "rps": 1000}[metric]
            values = [normal] * len(times)
            if start >= START and index == 0 and self.anomaly and metric in {"p95", "cpu"}:
                values[-3:] = [800 if metric == "p95" else .9] * 3
            rows.append(MetricSeries(metric, f"svc-{index}", times, values, unit, "prometheus").to_dict())
        return {"success": True, "series": rows}


def setup_source(**kwargs):
    settings = Settings(queries={m: {"prometheus": m} for m in ("p95", "error_rate", "cpu", "rps")})
    prom = FakePrometheus(**kwargs)
    return Sources(settings, prometheus=prom), prom


def model(*answers):
    return GenericFakeChatModel(messages=iter(answers))


def run(sources, *answers, extra=None, question="Проанализируй завершённое НТ"):
    return nt_graph.build_graph(model(*answers), sources=sources).compile().invoke(
        {**INPUT, **(extra or {}), "messages": [HumanMessage(question)]},
        {"configurable": {"publish": False}})


def test_25_services_ranked_without_raw_datapoints_to_llm():
    sources, prom = setup_source()
    state = run(sources, AIMessage(content='{"hypotheses": [], "recommendations": []}'))
    assert state["analysis_result"] == "FAILED"
    assert len(state["current_metrics"]) == 25
    assert state["ranked_services"][0]["service"] == "svc-0"
    assert len(prom.calls) == 8  # Four batched metric queries, two time windows.
    summary = json.loads(state["investigation_history"][0].content)
    assert summary["services_checked"] == 25
    assert len(summary["top_services"]) <= 5
    assert "timestamps" not in str(summary)
    assert "svc-24" not in str(summary)
    assert state["maximum_stable_rps"] == 1000
    assert "FAILED" in state["artifacts"]["report"]
    assert state["usage"]["calls"] == 1


def test_sequential_tools_then_evidence_backed_hypothesis():
    sources, _ = setup_source()
    state = run(sources,
        AIMessage(content="", tool_calls=[{"name": "prometheus_range_query",
                  "args": {"metric": "cpu", "service": "svc-0"}, "id": "cpu-read"}]),
        AIMessage(content="", tool_calls=[{"name": "prometheus_range_query",
                  "args": {"metric": "p95", "service": "svc-0"}, "id": "p95-read"}]),
        AIMessage(content=json.dumps({"hypotheses": [{"service": "svc-0", "confidence": "likely",
            "description": "Возможное насыщение CPU", "evidence_ids": ["tool:cpu-read", "tool:p95-read"]}],
            "recommendations": ["Проверить профиль CPU"]})))
    assert state["root_cause_hypotheses"][0]["confidence"] == "likely"
    assert "tool:cpu-read" in state["evidence"]
    assert state["iteration"] == 3
    assert state["usage"]["calls"] == 3


def test_missing_metrics_is_inconclusive_and_llm_cannot_override_verdict():
    sources, _ = setup_source(anomaly=False, missing="error_rate")
    state = run(sources, AIMessage(content='{"result":"PASSED","hypotheses":[]}'))
    assert state["analysis_result"] == "INCONCLUSIVE"
    assert state["source_errors"]
    assert any("error_rate" in item for item in state["missing_parameters"])


def test_clean_complete_test_passes():
    sources, _ = setup_source(anomaly=False)
    state = run(sources, AIMessage(content="{}"))
    assert state["analysis_result"] == "PASSED"
    assert not state["threshold_violations"]


def test_missing_input_and_running_test_do_not_fetch_metrics():
    sources, prom = setup_source()
    state = run(sources, extra={"test_status": "running"})
    assert state["analysis_result"] == "INCONCLUSIVE"
    assert not prom.calls
    state = run(sources, extra={"sla_p95_ms": None, "sla_error_rate": None})
    assert state["analysis_result"] == "INCONCLUSIVE"
    assert not state.get("usage")
    assert not prom.calls


@responses.activate
def test_precheck_explains_empty_queries_and_gateway_failure_despite_missing_dates():
    from agent.integrations.load_testing import HTTPLoadTesting

    prom = FakePrometheus()
    sources = Sources(Settings(), prometheus=prom,
                      load_testing=HTTPLoadTesting("https://load.test"))
    responses.get("https://load.test/tests/nt-run-2291/results", status=404)
    state = nt_graph.build_graph(model(), sources=sources).compile().invoke(
        {"messages": [HumanMessage("Проведи анализ НТ.\ntest_id=nt-run-2291")]},
        {"configurable": {"publish": False}},
    )

    assert not prom.calls
    assert not state.get("usage")
    assert not state["precheck_result"]["success"]
    assert "не настроен NT_METRIC_QUERIES" in state["missing_parameters"]
    assert "не указаны SLA с явными единицами" in state["missing_parameters"]
    assert "не указан started_at" in state["missing_parameters"]
    report = state["artifacts"]["report"]
    assert "Анализ не выполнен" in report
    assert "nt-run-2291" in report
    assert "LOAD_TESTING_UNAVAILABLE" in report
    assert "Одних URL Prometheus/InfluxDB недостаточно" in report
    assert "## Main anomalies" not in report


def test_invalid_llm_claims_are_not_promoted_to_facts():
    sources, _ = setup_source()
    state = run(sources, AIMessage(content=json.dumps({"hypotheses": [{"service": "svc-0",
        "confidence": "confirmed", "description": "CPU caused it", "evidence_ids": ["invented"]}]})))
    assert state["root_cause_hypotheses"] == []
    assert "unknown: причина не установлена" in state["artifacts"]["report"]


def test_tool_loop_is_bounded_and_unknown_shell_tool_does_not_execute():
    sources, _ = setup_source()
    calls = [AIMessage(content="", tool_calls=[{"name": "execute_command", "args": {"command": "ls"},
              "id": f"bad-{index}"}]) for index in range(4)]
    state = run(sources, *calls)
    assert state["iteration"] == 4
    assert state["analysis_result"] == "FAILED"
    assert state["source_errors"][-1]["error_type"] == "INVESTIGATION_SKIPPED"


def test_investigation_limit_keeps_collected_evidence_and_history():
    """Упор в лимит прекращает исследование, но не стирает оплаченную работу."""
    sources, _ = setup_source()
    turns = [AIMessage(content="", tool_calls=[{"name": "prometheus_range_query",
        "args": {"metric": "cpu", "service": "svc-0"}, "id": f"call-{index}"}]) for index in range(4)]
    state = run(sources, *turns)
    assert state["iteration"] == 4
    assert state["stop_reason"] == "investigation_limit"
    assert len(state["investigation_history"]) > 1
    assert not getattr(state["investigation_history"][-1], "tool_calls", None)
    assert [key for key in state["evidence"] if key.startswith("tool:")]
    assert "Исследование остановлено до вывода" in state["artifacts"]["report"]


def test_report_limits_per_service_dumps_and_names_the_omission():
    sources, _ = setup_source()
    state = run(sources, AIMessage(content='{"hypotheses": [], "recommendations": []}'))
    report = state["artifacts"]["report"]
    section = report.split("## Baseline comparison")[1].split("## Previous test comparison")[0]
    assert "svc-0" in section
    assert "svc-24" not in section
    assert "из 25 сервисов" in section


def test_tool_says_why_a_metric_was_refused():
    """«Не получилось» без причины заставляет модель гадать и жечь ходы."""
    sources, _ = setup_source()
    state = run(sources, AIMessage(content="", tool_calls=[{"name": "prometheus_range_query",
        "args": {"metric": "disk_latency", "service": "svc-0"}, "id": "unknown-metric"}]),
        AIMessage(content="{}"))
    result = json.loads(state["investigation_history"][2].content)
    assert not result["success"]
    assert result["errors"][0]["error_type"] == "METRIC_NOT_ALLOWED"


def test_turn_with_too_many_tool_calls_is_rejected_whole():
    """Запрос сверх лимита отклоняется целиком; лимит назван в промпте."""
    from agent import nt_prompts
    assert "не более трёх за один ход" in nt_prompts.INVESTIGATE
    sources, _ = setup_source()
    greedy = AIMessage(content="", tool_calls=[{"name": "prometheus_range_query",
        "args": {"metric": "cpu", "service": "svc-0"}, "id": f"call-{index}"} for index in range(4)])
    state = run(sources, greedy, AIMessage(content=json.dumps({"hypotheses": [{"service": "svc-0",
        "confidence": "likely", "description": "CPU", "evidence_ids": ["metric:svc-0:cpu"]}]})))
    assert state["iteration"] == 1
    assert not [k for k in state["evidence"] if k.startswith("tool:")]
    assert state["root_cause_hypotheses"] == []


def test_out_of_scope_and_unknown_metric_tools_return_structured_errors():
    sources, _ = setup_source()
    state = run(sources, AIMessage(content="", tool_calls=[{"name": "prometheus_range_query",
        "args": {"metric": "cpu", "service": "other-namespace-service"}, "id": "outside"}]),
        AIMessage(content="{}"))
    result = json.loads(state["investigation_history"][2].content)
    assert result["error_type"] == "OUT_OF_SCOPE"


def test_llm_unavailable_retains_deterministic_report():
    sources, _ = setup_source()
    state = run(sources)  # Fake model has no response, raises StopIteration.
    assert state["analysis_result"] == "FAILED"
    assert state["artifacts"]["report"]
    assert state["source_errors"][-1]["error_type"] == "LLM_UNAVAILABLE"


def test_budget_gate_prevents_model_but_not_analysis(monkeypatch):
    sources, _ = setup_source()
    monkeypatch.setenv("BUDGET_USD_PER_THREAD", "0.000000001")
    state = run(sources, extra={"usage": {"cache_miss": 1000000}})
    assert state["analysis_result"] == "FAILED"
    assert not state.get("iteration")


def test_publication_uses_existing_approval_and_resumes_without_reanalysis(monkeypatch):
    sources, prom = setup_source()
    monkeypatch.setenv("PUBLISH_REQUIRE_APPROVAL", "1")
    monkeypatch.setenv("PUBLISH_TARGET", "file")
    app = nt_graph.build_graph(model(AIMessage(content="{}")), sources=sources).compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "nt-approval", "publish": True}}
    state = app.invoke({**INPUT, "messages": [HumanMessage("Анализ НТ")]}, config)
    assert state.get("__interrupt__")
    assert state["__interrupt__"][0].value["action"] == "publish"
    count = len(prom.calls)
    final = app.invoke(Command(resume={"decision": "approved"}), config)
    assert final["publication"]["status"] in {"created", "published", "saved"}
    assert len(prom.calls) == count


def test_rejected_confluence_publication_saves_nt_report_as_a_file(monkeypatch):
    from agent import confluence, publishers

    sources, prom = setup_source()
    monkeypatch.setenv("PUBLISH_REQUIRE_APPROVAL", "1")
    monkeypatch.setenv("PUBLISH_TARGET", "confluence")
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://wiki.example.com")
    monkeypatch.setenv("CONFLUENCE_TOKEN", "token")
    monkeypatch.setenv("CONFLUENCE_SPACE_KEY", "NT")
    monkeypatch.setattr(
        publishers.ConfluencePublisher,
        "preview",
        lambda self, title: {"action": "create"},
    )
    monkeypatch.setattr(
        confluence,
        "publish_page",
        lambda *args, **kwargs: pytest.fail("must not publish"),
    )
    app = nt_graph.build_graph(model(AIMessage(content="{}")), sources=sources).compile(
        checkpointer=InMemorySaver()
    )
    config = {"configurable": {"thread_id": "nt-local-fallback", "publish": True}}

    pending = app.invoke({**INPUT, "messages": [HumanMessage("Анализ НТ")]}, config)
    payload = pending["__interrupt__"][0].value
    assert payload["reject_label"] == "не публиковать; сохранить файл"
    count = len(prom.calls)
    final = app.invoke(Command(resume={"decision": "rejected"}), config)

    assert final["publication"]["target"] == "file"
    assert final["publication"]["fallback_from"] == "confluence"
    assert final["publication"]["external_status"] == "rejected"
    assert final["publication"]["status"] == "created"
    assert len(final["publication"]["pages"]) == 1
    saved = publishers.documents()
    assert len(saved) == 1
    assert "# NT Report" in publishers.read_document(saved[0]["name"])
    assert len(prom.calls) == count


def test_missing_confluence_credentials_save_nt_report_as_a_file(monkeypatch):
    from agent import publishers

    sources, _ = setup_source()
    monkeypatch.setenv("PUBLISH_REQUIRE_APPROVAL", "1")
    monkeypatch.setenv("PUBLISH_TARGET", "confluence")
    state = nt_graph.build_graph(model(AIMessage(content="{}")), sources=sources).compile().invoke(
        {**INPUT, "messages": [HumanMessage("Анализ НТ")]},
        {"configurable": {"thread_id": "nt-no-confluence-token", "publish": True}},
    )

    assert "__interrupt__" not in state
    assert state["publication"]["target"] == "file"
    assert state["publication"]["external_status"] == "skipped"
    assert len(publishers.documents()) == 1


@responses.activate
def test_jira_only_question_extracts_explicit_context_with_mock_http(monkeypatch):
    from agent import jira
    sources, _ = setup_source()
    monkeypatch.setattr(jira, "fetch_issue", lambda key: {"key": key, "url": f"https://jira.test/browse/{key}",
        "summary": "NT", "description": "```json\n" + json.dumps(INPUT) + "\n```", "links": []})
    app = nt_graph.build_graph(model(AIMessage(content="{}")), sources=sources).compile()
    state = app.invoke({"messages": [HumanMessage("Проведи анализ НТ по NT-123.")]},
                       {"configurable": {"publish": False}})
    assert state["jira_key"] == "NT-123"
    assert state["target_service"] == "svc-0"
    assert state["analysis_result"] == "FAILED"


@responses.activate
def test_test_id_in_unclosed_json_fence_loads_gateway_metadata_and_metrics():
    from agent.integrations.load_testing import HTTPLoadTesting

    sources, prom = setup_source(anomaly=False)
    sources.load_testing = HTTPLoadTesting("https://load.test")
    responses.get("https://load.test/tests/nt-run-2291", json={
        **INPUT, "test_id": "nt-run-2291", "status": "completed",
    })
    responses.get("https://load.test/tests/nt-run-2291/results", json={
        **INPUT, "test_id": "nt-run-2291", "test_status": "completed",
    })
    state = nt_graph.build_graph(model(AIMessage(content="{}")), sources=sources).compile().invoke(
        {"messages": [HumanMessage(
            'Проведи анализ завершённого НТ. ```json {"test_id":"nt-run-2291"}'
        )]},
        {"configurable": {"publish": False}},
    )

    assert len(responses.calls) == 2
    assert state["test_id"] == "nt-run-2291"
    assert state["precheck_result"]["success"]
    assert len(prom.calls) == 8
    assert state["analysis_result"] == "PASSED"
    assert "test_id: nt-run-2291" in state["artifacts"]["report"]


@responses.activate
def test_split_gateway_metadata_and_thresholds_reach_metric_analysis():
    from agent.integrations.load_testing import HTTPLoadTesting

    sources, prom = setup_source()
    sources.load_testing = HTTPLoadTesting("https://load.test")
    card = {k: v for k, v in INPUT.items() if not k.startswith("sla_")}
    responses.get("https://load.test/tests/load-123", json={
        **card, "status": "completed", "baseline_start": START - 600, "baseline_end": START,
    })
    responses.get("https://load.test/tests/load-123/results", json={
        "test_id": "load-123", "status": "completed", "started_at": START, "finished_at": END,
        "thresholds": [
            {"metric": "p95_ms", "expression": "p95_ms < 500", "passed": True, "observed": 100},
            {"metric": "error_rate", "expression": "error_rate < 0.01", "passed": True},
        ],
    })
    state = nt_graph.build_graph(model(AIMessage(content="{}")), sources=sources).compile().invoke(
        {"messages": [HumanMessage("Проанализируй НТ.\ntest_id=load-123")]},
        {"configurable": {"publish": False}},
    )
    assert state["precheck_result"]["success"]
    assert state["test_status"] == "completed"
    assert state["baseline_start"] == START - 600
    assert state["sla_p95_ms"] == 500
    assert len(prom.calls) == 8
    assert len(state["current_metrics"]) == 25
    # Gateway flags cannot overrule violations found in the real metric series.
    assert state["analysis_result"] == "FAILED"


@responses.activate
def test_full_graph_uses_mock_prometheus_http():
    from urllib.parse import parse_qs, urlsplit
    fake = FakePrometheus()
    def callback(request):
        params = parse_qs(urlsplit(request.url).query)
        metric = params["query"][0]
        data = fake.range_query(metric, float(params["start"][0]), float(params["end"][0]),
                                float(params["step"][0]), metric=metric, unit="ms")
        rows = [{"metric": {"service": s["service"]}, "values": list(zip(s["timestamps"], s["values"], strict=True))}
                for s in data["series"]]
        return 200, {}, json.dumps({"status": "success", "data": {"resultType": "matrix", "result": rows}})
    responses.add_callback(responses.GET, "https://prom.test/api/v1/query_range", callback=callback)
    settings = Settings(queries={m: {"prometheus": m} for m in ("p95", "error_rate", "cpu", "rps")})
    state = run(Sources(settings, prometheus=Prometheus("https://prom.test")), AIMessage(content="{}"))
    assert state["analysis_result"] == "FAILED"
    assert len(responses.calls) == 8


@pytest.mark.parametrize("field,value", [("sla_p95_ms", float("nan")), ("namespace", ["nt"]),
                                       ("services", "all"), ("finished_at", float("inf"))])
def test_malformed_input_produces_a_report_instead_of_crashing(field, value):
    sources, prom = setup_source()
    state = run(sources, AIMessage(content="{}"), extra={field: value})
    assert state["analysis_result"] == "INCONCLUSIVE"
    assert state["artifacts"]["report"]
    assert not prom.calls


def test_sparse_baseline_cannot_pass(monkeypatch):
    sources, prom = setup_source(anomaly=False)
    original = prom.range_query
    def sparse(query, start, end, step, **kwargs):
        result = original(query, start, end, step, **kwargs)
        if start < START:
            for item in result["series"]:
                item["timestamps"] = item["timestamps"][:1]
                item["values"] = item["values"][:1]
        return result
    monkeypatch.setattr(prom, "range_query", sparse)
    state = run(sources, AIMessage(content="{}"))
    assert state["analysis_result"] == "INCONCLUSIVE"
    assert any("неполный baseline" in m for m in state["missing_parameters"])


def test_partial_prometheus_cannot_pass(monkeypatch):
    sources, prom = setup_source(anomaly=False)
    original = prom.range_query
    def partial(*args, **kwargs):
        return {**original(*args, **kwargs), "partial": True}
    monkeypatch.setattr(prom, "range_query", partial)
    state = run(sources, AIMessage(content="{}"))
    assert state["analysis_result"] == "INCONCLUSIVE"


def test_previous_test_comparison_uses_actual_metrics():
    sources, _ = setup_source(anomaly=False)
    class Gateway:
        def get_test_results(self, test_id):
            return {"success": True, "data": {**INPUT, "test_id": test_id,
                "started_at": START - 3600 if test_id == "previous" else START,
                "finished_at": END - 3600 if test_id == "previous" else END,
                "scenario": "checkout", "test_status": "completed"}}
    sources.load_testing = Gateway()
    state = run(sources, AIMessage(content="{}"), extra={"previous_test_id": "previous", "scenario": "checkout"})
    comparison = state["previous_comparison"]
    assert comparison["test_id"] == "previous"
    assert comparison["stable_rps"]["percent"] == 0
    assert comparison["metrics"]["svc-0"]["p95"]["percent"] == 0


def test_previous_run_regression_reaches_the_investigation_brief():
    """Сравнение с прошлым прогоном бесполезно, если приходит после гипотез."""
    sources, _ = setup_source(anomaly=False)
    class Gateway:
        def get_test_results(self, test_id):
            return {"success": True, "data": {**INPUT, "test_id": test_id,
                "started_at": START - 3600 if test_id == "previous" else START,
                "finished_at": END - 3600 if test_id == "previous" else END,
                "scenario": "checkout", "test_status": "completed"}}
    sources.load_testing = Gateway()
    state = run(sources, AIMessage(content="{}"),
                extra={"previous_test_id": "previous", "scenario": "checkout"})
    brief = json.loads(state["investigation_history"][0].content)
    assert brief["previous_comparison"]["test_id"] == "previous"
    assert brief["previous_comparison"]["stable_rps"]["percent"] == 0
    assert "previous_stable_rps" not in brief["previous_comparison"]
    # Сравнение по всему контуру переполняет бриф и вытесняет из него улики.
    assert set(brief["previous_comparison"]["metrics"]) == {"svc-0"}


def test_live_gateway_status_cannot_be_overridden_by_input():
    sources, prom = setup_source()
    class Gateway:
        def get_test_results(self, test_id):
            return {"success": True, "data": {"test_id": test_id, "test_status": "running"}}
    sources.load_testing = Gateway()
    state = run(sources, extra={"test_status": "completed"})
    assert state["test_status"] == "running"
    assert state["analysis_result"] == "INCONCLUSIVE"
    assert not prom.calls
