"""HTTP integration contracts run entirely against mocked responses."""

import json

import pytest
import responses

from agent.integrations.http import HTTPClient
from agent.integrations.influx import InfluxDB
from agent.integrations.load_testing import HTTPLoadTesting
from agent.integrations.prometheus import Prometheus
from agent.nt.collection import Sources
from agent.nt.settings import Settings


def prom_response(rows):
    return {"status": "success", "data": {"resultType": "matrix", "result": rows}}


@responses.activate
def test_prometheus_range_normalization_and_request_parameters():
    responses.get("https://prom.test/api/v1/query_range", json=prom_response([
        {"metric": {"service": "orders"}, "values": [[100, "1"], [130, "NaN"], [160, "3"]]}]))
    result = Prometheus("https://prom.test").range_query("sum by(service)(m)", 100, 200, 30,
                                                       metric="cpu", unit="ratio")
    assert result["success"]
    assert result["series"][0]["values"] == [1., 3.]
    assert result["series"][0]["invalid_points"] == 1
    assert "start=100.0" in responses.calls[0].request.url
    assert "step=30" in responses.calls[0].request.url


@responses.activate
def test_prometheus_instant_vector_and_unknown_series():
    responses.get("https://prom.test/api/v1/query", json={"status": "success", "data": {
        "resultType": "vector", "result": [{"metric": {"service": "a"}, "value": [1, "2"]}]}})
    assert Prometheus("https://prom.test").instant_query("m", metric="cpu", unit="ratio")["success"]


@responses.activate
def test_prometheus_errors_retries_and_secret_redaction():
    responses.get("https://prom.test/api/v1/query_range", status=503, body="secret-token")
    result = Prometheus("https://prom.test", "secret-token").range_query("q", 1, 10, 1, metric="cpu", unit="ratio")
    assert not result["success"]
    assert len(responses.calls) == 2
    assert "secret-token" not in str(result)


@responses.activate
def test_prometheus_limits_and_bad_labels():
    responses.get("https://prom.test/api/v1/query_range", json=prom_response([
        {"metric": {"job": "orders"}, "values": [[100, "1"]]}]))
    assert not Prometheus("https://prom.test").range_query("q", 100, 200, 30, metric="cpu", unit="ratio")["success"]
    assert not Prometheus("https://prom.test").range_query("q", 200, 100, 30, metric="cpu", unit="ratio")["success"]


@responses.activate
def test_flux_annotated_csv_and_escaped_filters():
    body = '#datatype,string,long,dateTime:RFC3339,double,string\n,result,table,_time,_value,service\n,,0,2026-09-01T00:00:00Z,0.3,orders\n'
    responses.post("https://influx.test/api/v2/query", body=body, content_type="application/csv")
    adapter = InfluxDB("https://influx.test", "token", database="metrics", org="org",
                      mappings={"cpu": {"measurement": "cpu"}})
    result = adapter.query_metric("cpu", "2026-09-01T00:00:00Z", "2026-09-01T00:01:00Z",
                                  {"namespace": 'nt"or true'}, unit="ratio")
    assert result["series"][0]["values"] == [.3]
    request = json.loads(responses.calls[0].request.body)
    assert 'nt\\"or true' in request["query"]
    assert responses.calls[0].request.headers["Authorization"] == "Token token"


@responses.activate
def test_influx3_sql_parameters_and_identifier_rejection():
    responses.post("https://influx.test/api/v3/query_sql", json=[
        {"_time": "2026-09-01T00:00:00Z", "_value": 123, "service": "orders"}])
    adapter = InfluxDB("https://influx.test", version=3, database="metrics",
                      mappings={"p95": {"measurement": "http_metrics", "field": "p95_ms"}})
    result = adapter.query_metric("p95", "2026-09-01T00:00:00Z", "2026-09-01T00:01:00Z",
                                  {"namespace": "nt' OR true"}, unit="ms")
    assert result["series"][0]["values"] == [123]
    request = json.loads(responses.calls[0].request.body)
    assert "nt' OR true" not in request["q"]
    assert request["params"]["f0"] == "nt' OR true"
    assert not adapter.query_metric("p95", 1, 10, {"bad; DROP": "x"}, unit="ms")["success"]


@responses.activate
def test_load_testing_read_only_contract():
    responses.get("https://load.test/tests/123/results", json={"test_id": "123", "test_status": "completed",
                   "started_at": 1, "finished_at": 10, "raw_results": "secret"})
    adapter = HTTPLoadTesting("https://load.test")
    assert "raw_results" not in adapter.get_test_results("123")["data"]
    assert not adapter.start_test("123")["success"]
    assert not adapter.stop_test("123")["success"]
    assert not adapter.get_test_results("../../admin")["success"]
    assert len(responses.calls) == 1


@responses.activate
def test_transport_rejects_redirects_and_oversized_output():
    responses.get("https://prom.test/redirect", status=302, headers={"Location": "https://evil.test/"})
    with pytest.raises(RuntimeError, match="HTTP_302"):
        HTTPClient("https://prom.test").request("GET", "/redirect")
    responses.get("https://prom.test/large", body="a" * 100)
    with pytest.raises(RuntimeError, match="RESPONSE_TOO_LARGE"):
        HTTPClient("https://prom.test", max_bytes=10).request("GET", "/large")


@responses.activate
def test_primary_failure_falls_back_to_influx_without_summing_quantiles():
    responses.get("https://prom.test/api/v1/query_range", status=503)
    responses.post("https://influx.test/api/v3/query_sql", json=[
        {"_time": "2026-09-01T00:00:00Z", "_value": 123, "service": "orders"}])
    settings = Settings(queries={"p95": {"prometheus": 'p95{namespace={{namespace}}}',
                                        "influx": {"measurement": "p95"}}})
    sources = Sources(settings, prometheus=Prometheus("https://prom.test", retries=0),
                      influx=InfluxDB("https://influx.test", version=3,
                                     mappings={"p95": {"measurement": "p95"}}))
    from agent.nt.models import timestamp
    result = sources.query("p95", {"namespace": "nt"}, timestamp("2026-09-01T00:00:00Z"),
                           timestamp("2026-09-01T00:01:00Z"))
    assert result["success"]
    assert result["series"][0]["source"] == "influx"
    assert result["errors"][0]["error_type"] == "PROMETHEUS_UNAVAILABLE"


def test_settings_require_namespace_scope_and_valid_profiles(monkeypatch):
    from agent.nt.settings import load_settings
    monkeypatch.setenv("NT_METRIC_QUERIES", '{"p95":{"prometheus":"unscoped"}}')
    with pytest.raises(ValueError, match="namespace"):
        load_settings()
    monkeypatch.setenv("NT_METRIC_QUERIES", '{"p95":{"prometheus":"p95{namespace={{namespace}}}","profile":"redis"}}')
    with pytest.raises(ValueError, match="does not belong"):
        load_settings()


@responses.activate
def test_flux_retry_preserves_content_type_headers():
    responses.post("https://influx.test/api/v2/query", status=503)
    responses.post("https://influx.test/api/v2/query", body=',result,table,_time,_value,service\n,,0,2026-09-01T00:00:00Z,0.3,orders\n')
    adapter = InfluxDB("https://influx.test", mappings={"cpu": {"measurement": "cpu"}})
    result = adapter.query_metric("cpu", "2026-09-01T00:00:00Z", "2026-09-01T00:01:00Z", {}, unit="ratio")
    assert result["success"]
    assert responses.calls[1].request.headers["Content-Type"] == "application/json"


@responses.activate
def test_kubernetes_returns_only_readonly_pod_summary():
    from agent.integrations.kubernetes import Kubernetes
    responses.get("https://kube.test/api/v1/namespaces/nt/pods", json={"items": [
        {"metadata": {"name": "orders-1"}, "spec": {"env": "should not leak"}, "status": {
            "phase": "Running", "containerStatuses": [{"restartCount": 2,
                "state": {"waiting": {"reason": "CrashLoopBackOff"}}}]}}]})
    result = Kubernetes("https://kube.test").get_pods("nt", "orders")
    assert result["data"]["restarts"] == 2
    assert result["historical"] is False
    assert "should not leak" not in str(result)
    assert responses.calls[0].request.method == "GET"


def test_log_grouping_bounds_lines_and_size():
    from agent.integrations.logs import group_errors
    result = group_errors(iter(["timeout"] * 10000), max_lines=10)
    assert result["truncated"]
    assert result["groups"] == [{"message": "timeout", "count": 10}]
