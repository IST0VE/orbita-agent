from __future__ import annotations

from agent import system_ci, system_ci_pr


def _package(tmp_path):
    root = tmp_path / "docs" / "orders"
    root.mkdir(parents=True)
    (root / "requirements.md").write_text(
        "- REQ-1. Create an order.\n- REQ-2. Cancel an order.\n",
        encoding="utf-8",
    )
    (root / "api.md").write_text(
        "POST /orders implements REQ-1.\n",
        encoding="utf-8",
    )
    return root


def test_changed_paths_are_scoped_to_package():
    changed = [
        "src/app.py",
        "docs/orders/api.md",
        "docs/orders/diagram.png",
        "docs/orders/requirements.md",
    ]
    assert system_ci_pr.changed_in_package(changed, "docs/orders") == [
        "api.md",
        "requirements.md",
    ]


def test_review_reports_impact_for_changed_document(tmp_path, monkeypatch):
    root = _package(tmp_path)
    monkeypatch.chdir(tmp_path)

    first = system_ci.analyse(root)
    baseline = system_ci.baseline_from(first)
    result = system_ci_pr.review(
        "docs/orders",
        ["docs/orders/api.md"],
        baseline=baseline,
        depth=2,
    )

    assert result["status"] == "clean"
    labels = {node["label"] for node in result["impacted_nodes"]}
    assert "api.md" in labels
    assert "REQ-1" in labels
    assert "POST /orders" in labels


def test_unrelated_pr_is_not_applicable(tmp_path, monkeypatch):
    _package(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = system_ci_pr.review(
        "docs/orders",
        ["src/app.py"],
        baseline={"schema_version": 1, "findings": []},
    )

    assert result["status"] == "not_applicable"
    assert result["new_findings"] == []
    assert system_ci_pr.should_fail(result, "blocker") is False


def test_changed_package_can_block_on_new_major(tmp_path, monkeypatch):
    _package(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = system_ci_pr.review(
        "docs/orders",
        ["docs/orders/requirements.md"],
        baseline={"schema_version": 1, "findings": []},
    )

    assert result["status"] == "findings"
    assert system_ci_pr.should_fail(result, "major") is True
    markdown = system_ci_pr.render_markdown(result)
    assert system_ci_pr.COMMENT_MARKER in markdown
    assert "REQ-2" in markdown
