"""Diagnostics must expose the contract details needed to fix an integration."""

import json
import runpy
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import responses


@responses.activate
def test_details_preserve_unknown_sla_names_and_find_actual_status_label(monkeypatch, capsys):
    inspect_contract = runpy.run_path(str(
        Path(__file__).resolve().parents[1] / "scripts" / "diagnose_nt.py"
    ))["inspect_contract"]
    monkeypatch.setenv("NT_LOAD_TESTING_URL", "https://load.test")
    monkeypatch.setenv("NT_LOAD_TESTING_TOKEN", "do-not-print")
    monkeypatch.setenv("NT_PROMETHEUS_URL", "https://prom.test")
    thresholds = [{"metric": "custom_latency", "expression": "p(95)<500", "observed": 123}]
    responses.get("https://load.test/tests/123/results", json={"thresholds": thresholds})
    responses.get("https://load.test/tests/123", json={
        "test_id": "123", "status": "completed", "started_at": 100, "finished_at": 200,
        "baseline_start": 10, "baseline_end": 100, "namespace": 'nt"01', "target_service": "orders",
    })
    responses.get("https://prom.test/api/v1/series", json={"status": "success", "data": [
        {"service": "orders", "namespace": 'nt"01', "code": "200", "instance": "private-host"},
        {"service": "orders", "namespace": 'nt"01', "code": "503", "instance": "private-host"},
    ]})

    assert inspect_contract("123") == 0
    output = capsys.readouterr().out
    details, labels = map(json.loads, output.splitlines())
    assert details["thresholds"] == [{"metric": "custom_latency", "expression": "p(95)<500"}]
    assert labels["status_labels"] == {"code": ["200", "503"]}
    params = parse_qs(urlsplit(responses.calls[-1].request.url).query)
    assert params["match[]"] == ['http_requests_total{namespace="nt\\"01",service="orders"}']
    assert params["start"] == ["10.0"]
    assert params["end"] == ["200.0"]
    assert "do-not-print" not in output
    assert "private-host" not in output
    assert all(call.request.method == "GET" for call in responses.calls)
