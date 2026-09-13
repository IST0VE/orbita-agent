"""The report is read by people: tables instead of state dumps, and nothing cited is dropped."""

from agent import confluence
from agent.nt.report import TOOL_TEXT_LIMIT, render_report


def metric(service="orders", name="cpu", **extra):
    return {"service": service, "metric": name, "count": 42, "median": 0.6555555555555554,
            "p95": 0.7999999999999999, "p99": 0.8, "min": 0.16, "max": 0.7999999999999999,
            "mad": 0.13, "unit": "ratio", "source": "prometheus", "start": 1789055100.0,
            "end": 1789056330.0, "invalid_points": 0, "partial": False, "max_gap": 30.0, **extra}


def finding(service="orders", name="error_rate", **extra):
    return {"service": service, "metric": name, "limit": 0.01, "peak": 0.03695006130634469,
            "first_at": 1789056120.0, "last_at": 1789056330.0, "count": 8, "unit": "ratio",
            "source": "prometheus", **extra}


def state(**extra):
    return {"precheck_result": {"success": True}, "target_service": "orders",
            "analysis_result": "FAILED", "current_metrics": {}, **extra}


def section(report, name):
    return report.split(f"## {name}")[1].split("\n## ")[0]


def test_evidence_is_a_table_and_keeps_every_cited_observation():
    """По evidence_id из гипотезы наблюдение обязано находиться — даже вне фокуса."""
    report = render_report(state(evidence={
        "metric:orders:cpu": metric(), "finding:0": finding(),
        "finding:1": finding(service="ledger"), "finding:2": finding(service="mailer")},
        root_cause_hypotheses=[{"service": "orders", "confidence": "probable",
            "description": "нехватка CPU", "evidence_ids": ["finding:1"],
            "counter_evidence_ids": [], "observations": [], "next_check": "профиль"}]))
    evidence = section(report, "Evidence")

    assert "| metric:orders:cpu | orders | cpu | ratio | 0.6556 |" in evidence
    assert "| finding:1 | ledger |" in evidence
    assert "finding:2" not in evidence
    assert "Свёрнуто улик: 1" in evidence
    assert "2026-09-10" in evidence  # first_at/last_at, а не секунды эпохи


def test_quality_column_appears_only_when_a_series_is_incomplete():
    complete = section(render_report(state(evidence={"metric:orders:cpu": metric()})), "Evidence")
    assert "качество" not in complete

    holed = section(render_report(state(evidence={
        "metric:orders:cpu": metric(invalid_points=1, partial=True, max_gap=300.0)})), "Evidence")
    assert "качество" in holed
    assert "частичный ряд; негодных точек: 1; пропуск до 300 с" in holed


def test_tool_evidence_keeps_its_shape_and_loses_only_the_tail():
    text = "ц" * (TOOL_TEXT_LIMIT + 40)
    report = render_report(state(evidence={"tool:call-1": {"success": True, "text": text}}))
    evidence = section(report, "Evidence")

    assert '"success": true' in evidence
    assert "ц" * TOOL_TEXT_LIMIT in evidence
    assert "ещё 40 знаков" in evidence
    assert text not in evidence


def test_cells_with_pipes_and_newlines_survive_as_confluence_tables():
    """Запрос модели с `|` разорвал бы строку таблицы, а вместе с ней и страницу."""
    report = render_report(state(evidence={
        "metric:orders|api:cpu": metric(service="orders|api", name="rps\nsum")}))
    storage = confluence.text_to_storage(section(report, "Evidence"))

    assert "<table>" in storage
    assert "<td>orders|api</td>" in storage
    assert "<td>rps sum</td>" in storage
    assert storage.count("<tr>") == 2  # шапка и единственный ряд


def test_comparisons_keep_the_omission_note_and_drop_the_float_tail():
    report = render_report(state(
        evidence={"metric:orders:cpu": metric()},
        baseline_comparison={
            "orders": {"cpu": {"baseline": 0.5333333333333333, "current": 0.6555555555555554,
                               "absolute": 0.12222222222222212, "percent": 22.91666666666665}},
            "ledger": {"cpu": {"baseline": 1.0, "current": 1.0, "absolute": 0.0, "percent": None}}}))
    comparison = section(report, "Baseline comparison")

    assert "| orders | cpu | 0.5333 | 0.6556 | 0.1222 | 22.92 |" in comparison
    assert "ledger" not in comparison
    assert "Показаны 1 из 2 сервисов" in comparison


def test_hypotheses_keep_fence_parity_and_the_headings_after_them():
    """Приклеенный к строке блок кода уводил весь дальнейший отчёт внутрь кода."""
    report = render_report(state(
        evidence={"metric:orders:cpu": metric(), "finding:0": finding()},
        root_cause_hypotheses=[{"service": "orders", "confidence": "possible",
            "mechanism": "saturation", "description": "CPU у предела",
            "evidence_ids": ["finding:0"], "counter_evidence_ids": [],
            "next_check": "профиль нагрузки",
            "observations": [{"evidence_id": "finding:0", "metric": "error_rate",
                              "peak": 0.03695006130634469, "limit": 0.01,
                              "first_at": 1789056120.0, "kind": "threshold"}]}]))

    assert report.count("```") % 2 == 0
    assert "| finding:0 | error_rate | — | 0.03695 | 0.01 | 2026-09-10" in report

    storage = confluence.text_to_storage(report)
    for heading in ("Evidence", "Baseline comparison", "Sources"):
        assert f"<h3>{heading}</h3>" in storage


def test_unexpected_comparison_shape_is_still_reported_in_full():
    """Незнакомая форма — повод показать её как есть, а не потерять в таблице."""
    report = render_report(state(evidence={"metric:orders:cpu": metric()},
                                 baseline_comparison={"orders": "NOT_COMPARABLE"}))
    assert '"orders": "NOT_COMPARABLE"' in section(report, "Baseline comparison")
