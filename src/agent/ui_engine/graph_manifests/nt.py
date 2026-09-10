"""Historical NT stages rendered by Orbita's existing UI engine."""

from agent.nt_roles import PIPELINE
from agent.ui_engine.graph_manifests.common import base_manifest

MANIFEST = base_manifest(PIPELINE)
MANIFEST["manifest_version"] = "2026.09.10.1"
MANIFEST["input"] = [MANIFEST["input"][0]]
MANIFEST["input"][0]["title"] = "Задача НТ: Jira, test_id, started_at / finished_at с часовым поясом"
MANIFEST["nodes"] = {
    key: {"title": title, "kind": kind, "group": "analysis"}
    for key, title, kind in (
        ("context", "Контекст Orbita", "system"),
        ("load_context", "Jira и документация", "system"),
        ("understand_task", "Параметры НТ", "task"),
        ("discover_scope", "Сервисы и зависимости", "system"),
        ("precheck", "Проверка входных данных", "system"),
        ("collect_baseline", "Baseline", "system"),
        ("collect_metrics", "Метрики завершённого НТ", "system"),
        ("detect_anomalies", "Аномалии и ranking", "system"),
        ("evaluate_test", "SLA и итоговый статус", "router"),
        ("investigate", "Исследование причин", "task"),
        ("additional_tools", "Диагностические инструменты", "tool"),
        ("final_analysis", "Проверка гипотез", "system"),
        ("compare_baseline", "Сравнение с предыдущим НТ", "system"),
        ("report", "Итоговый отчёт", "task"),
        ("remember", "Память", "system"),
        ("approve", "Подтверждение публикации", "approval"),
        ("publish", "Публикация", "task"),
    )
}
MANIFEST["nodes"]["report"]["output"] = {"path": "artifacts.report", "widget": "markdown"}
for key, title, widget in (
    ("precheck_result", "Precheck", "json"),
    ("analysis_result", "Итог НТ", "text"),
    ("ranked_services", "Подозрительные сервисы", "table"),
    ("baseline_metrics", "Baseline", "json"),
    ("timeline", "Хронология НТ", "table"),
):
    MANIFEST["state"].append({"id": key, "path": key, "title": title,
                              "widget": widget, "surface": "right", "empty": "hide"})
for surface in MANIFEST["surfaces"]:
    if surface["id"] == "left":
        surface["widgets"] = ["artifacts", "published"]
    elif surface["id"] == "right":
        surface["widgets"] += ["analysis_result", "precheck_result", "ranked_services", "baseline_metrics", "timeline"]
MANIFEST["interrupts"] = [r for r in MANIFEST["interrupts"] if r["id"] == "publish-approval"]
MANIFEST["redaction"].append({"path": "metric_snapshots", "mode": "metadata_only"})
