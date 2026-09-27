"""Separate selectors for the editable original and supplemental materials."""

from agent import update_roles
from agent.ui_engine.graph_manifests.common import base_manifest, name_documents

MANIFEST = base_manifest(update_roles.PIPELINE)
MANIFEST["manifest_version"] = "2026.09.27.1"
name_documents(MANIFEST, {"updated": {"title": "Обновлённый документ"}})
# Файлы чата одни, а выбора в них два: основной документ, который обновляется,
# и новые материалы, по которым он обновляется. Обе отметки ставятся в одном
# списке — у каждого файла своя пара переключателей. Папку основного документа
# отдельно не шлём: граф берёт её там же, где материалы (`update_graph.read_node`).
MANIFEST["input"] = [
    MANIFEST["input"][0],
    {
        "id": "task",
        "target": "configurable.input_dir",
        "widget": "chat-files",
        "title": "Файлы чата",
        "required": True,
        "options": {
            "fixed": "@chat",
            "pick": [
                {"input": "base_document", "kind": "text", "multiple": False, "label": "основной"},
                {"input": "document", "kind": "text", "multiple": True, "label": "материал"},
            ],
        },
    },
    {
        "id": "base_document",
        "target": "configurable.base_file",
        "widget": "file-picker",
        "title": "Основной документ",
        "required": True,
        "options": {
            "kind": "text",
            "depends_on": "task",
            "multiple": False,
            "empty_hint": "Отметьте «основной» у файла в списке выше.",
        },
    },
    {
        "id": "document",
        "target": "configurable.input_file",
        "widget": "file-picker",
        "title": "Новые материалы",
        "required": True,
        "options": {
            "kind": "text",
            "depends_on": "task",
            "multiple": True,
            "empty_hint": "Отметьте «материал» у файлов в списке выше.",
        },
    },
]
MANIFEST["nodes"] = {
    "source": {"title": "Чтение документов", "kind": "system", "group": "input"},
    "changes": {"title": "Предложение изменений", "kind": "task", "group": "pipeline"},
    "apply": {
        "title": "Проверка и применение",
        "kind": "task",
        "group": "pipeline",
        "output": {"path": "artifacts.changes", "widget": "markdown"},
    },
    "prepare": {"title": "Подготовка новой версии", "kind": "system", "group": "finalization"},
    "approve": {"title": "Согласование сохранения", "kind": "approval", "color": "warning"},
    "publish": {"title": "Сохранение новой версии", "kind": "task", "color": "success"},
}
# Пауза оператора остаётся у всех конвейеров: остановиться посреди
# работы можно везде, где есть обращения к модели.
MANIFEST["interrupts"] = [
    rule
    for rule in MANIFEST["interrupts"]
    if rule["id"] in {"publish-approval", "operator-pause"}
]
for surface in MANIFEST["surfaces"]:
    if surface["id"] == "left":
        surface["widgets"] = [
            "task",
            "base_document",
            "document",
            "artifacts",
            "published",
        ]
