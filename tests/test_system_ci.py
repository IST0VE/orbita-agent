from __future__ import annotations

import json

from agent import checks, system_ci


def _package(tmp_path):
    (tmp_path / "requirements.md").write_text(
        "- REQ-1. Create an order.\n- REQ-2. Cancel an order.\n",
        encoding="utf-8",
    )
    (tmp_path / "api.md").write_text(
        "POST /orders implements REQ-1.\n",
        encoding="utf-8",
    )


def test_analysis_builds_graph_and_new_findings(tmp_path):
    _package(tmp_path)
    result = system_ci.analyse(tmp_path)

    labels = {node["label"] for node in result["graph"]["nodes"]}
    assert "REQ-1" in labels
    assert "REQ-2" in labels
    assert "POST /orders" in labels
    finding = next(
        item
        for item in result["delta"]["new"]
        if item["kind"] == "requirement_without_design"
    )
    assert finding["severity"] == checks.MAJOR
    assert system_ci.should_fail(result, "major") is True


def test_baseline_turns_existing_findings_into_non_blocking_debt(tmp_path):
    _package(tmp_path)
    first = system_ci.analyse(tmp_path)
    baseline = system_ci.baseline_from(first)

    second = system_ci.analyse(tmp_path, baseline=baseline)

    assert second["delta"]["new"] == []
    assert second["delta"]["existing"]
    assert system_ci.should_fail(second, "minor") is False


def test_resolved_finding_is_reported_against_baseline(tmp_path):
    _package(tmp_path)
    first = system_ci.analyse(tmp_path)
    baseline = system_ci.baseline_from(first)

    (tmp_path / "architecture.md").write_text(
        "Cancellation flow implements REQ-2.\n",
        encoding="utf-8",
    )
    second = system_ci.analyse(tmp_path, baseline=baseline)

    assert any(
        item["kind"] == "requirement_without_design"
        for item in second["delta"]["resolved"]
    )


def test_impact_walks_requirement_to_documents_and_endpoints(tmp_path):
    _package(tmp_path)
    result = system_ci.analyse(tmp_path)

    impacted = system_ci.impact(result, "REQ-1", depth=2)
    labels = {node["label"] for node in impacted["nodes"]}

    assert "REQ-1" in labels
    assert "requirements.md" in labels
    assert "api.md" in labels
    assert "POST /orders" in labels


def test_baseline_schema_is_validated(tmp_path):
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"schema_version": 999, "findings": []}), encoding="utf-8")

    try:
        system_ci.load_baseline(path)
    except ValueError as exc:
        assert "unsupported baseline schema" in str(exc)
    else:
        raise AssertionError("invalid schema must be rejected")
