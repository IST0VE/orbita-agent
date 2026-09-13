"""Visible preparation, execution, live metrics and approval for nt_run."""

from agent.nt_run_graph import PIPELINE
from agent.ui_engine.graph_manifests.common import base_manifest

MANIFEST = base_manifest(PIPELINE)
MANIFEST["manifest_version"] = "2026.09.12.1"
MANIFEST["nodes"] = {
    key: {"title": title, "kind": kind, "group": group}
    for key, title, kind, group in (
        ("context", "Материалы задачи", "system", "preparation"),
        ("initialize", "Доступные стенды и инструменты", "system", "preparation"),
        ("plan_next", "ИИ: следующий шаг", "task", "preparation"),
        ("execute_tools", "Чтение материалов и создание сценария", "tool", "preparation"),
        ("repair_prompt", "Уточнение плана", "router", "preparation"),
        ("clarify", "Вопрос оператору", "approval", "preparation"),
        ("validate", "Проверка плана и лимитов", "system", "preparation"),
        ("approve_run", "Разрешение пробного и основного НТ", "approval", "execution"),
        ("prepare", "Файлы сценария и k6 inspect", "tool", "execution"),
        ("smoke", "Пробный прогон", "tool", "execution"),
        ("start_load", "Запуск нагрузки", "tool", "execution"),
        ("monitor", "Статус и живые метрики", "system", "execution"),
        ("stop_test", "Остановка и подтверждение", "tool", "execution"),
        ("collect_results", "Результаты прогона", "tool", "analysis"),
        ("analyze", "Подграф nt: анализ метрик", "task", "analysis"),
        ("review_run", "Оценка необходимости следующего прогона", "router", "analysis"),
        ("report", "Отчёт о проведении НТ", "task", "analysis"),
        ("prepare_publish", "Подготовка публикации", "system", "finalization"),
        ("approve", "Подтверждение публикации", "approval", "finalization"),
        ("publish", "Публикация отчёта", "task", "finalization"),
    )
}
MANIFEST["nodes"]["report"]["output"] = {"path": "artifacts.report", "widget": "markdown"}
for key, title, widget in (
    ("candidate", "План НТ", "json"), ("active_test_id", "Текущий test_id", "text"),
    ("active_status", "Статус и метрики", "json"), ("runs", "Выполненные прогоны", "json"),
    ("run_analysis", "Анализ НТ", "json"), ("execution_log", "Журнал действий", "table"),
    ("last_error", "Ограничение", "text"),
):
    MANIFEST["state"].append({"id": key, "path": key, "title": title, "widget": widget,
                              "surface": "right", "empty": "hide"})
    next(s for s in MANIFEST["surfaces"] if s["id"] == "right")["widgets"].append(key)
MANIFEST["interrupts"] = [r for r in MANIFEST["interrupts"] if r["id"] == "publish-approval"]
MANIFEST["interrupts"] += [
    {"id": "query-approval", "priority": 6, "match": {"path": "action", "equals": "query"},
     "widget": "approval", "resume_schema": {"type": "object", "required": ["decision"],
        "additionalProperties": False, "properties": {
            "decision": {"enum": ["approved", "rejected"]}, "reason": {"type": "string", "maxLength": 4000}}}},
    {"id": "nt-launch", "priority": 5, "match": {"path": "action", "equals": "nt_launch"},
     "widget": "approval", "resume_schema": {"type": "object", "required": ["decision"],
        "additionalProperties": False, "properties": {
            "decision": {"enum": ["approved", "rejected"]}, "reason": {"type": "string", "maxLength": 4000}}}},
    {"id": "nt-clarify", "priority": 4, "match": {"path": "action", "equals": "nt_clarify"},
     "widget": "form", "resume_schema": {"type": "object", "required": ["answer"],
        "additionalProperties": False, "properties": {
            "answer": {"type": "string", "title": "Ответ", "format": "multiline", "minLength": 1, "maxLength": 12000}}}},
]
