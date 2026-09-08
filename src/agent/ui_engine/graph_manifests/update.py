"""Separate selectors for the editable original and supplemental materials."""

from agent import update_roles
from agent.ui_engine.graph_manifests.common import base_manifest

MANIFEST = base_manifest(update_roles.PIPELINE)
MANIFEST["manifest_version"] = "2026.09.06.1"
MANIFEST["input"] = [MANIFEST["input"][0]]
for folder_id, file_id, folder_target, file_target, title, multiple in (
    ("base_task", "base_document", "base_dir", "base_file", "Основной документ", False),
    ("task", "document", "input_dir", "input_file", "Новые материалы", True),
):
    MANIFEST["input"].extend(
        [
            {
                "id": folder_id,
                "target": f"configurable.{folder_target}",
                "widget": "task-picker",
                "title": f"{title}: папка",
                "required": True,
                "source": {"resource_id": "orbita.tasks", "operation": "list"},
                "options": {
                    "document_input": file_id,
                    "document_kind": "text",
                    "document_multiple": multiple,
                },
            },
            {
                "id": file_id,
                "target": f"configurable.{file_target}",
                "widget": "file-picker",
                "title": title,
                "required": True,
                "source": {"resource_id": "orbita.tasks", "operation": "list"},
                "options": {
                    "kind": "text",
                    "depends_on": folder_id,
                    "multiple": multiple,
                    "empty_hint": "Выберите файл в дереве выше.",
                },
            },
        ]
    )
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
MANIFEST["interrupts"] = [
    rule for rule in MANIFEST["interrupts"] if rule["id"] == "publish-approval"
]
for surface in MANIFEST["surfaces"]:
    if surface["id"] == "left":
        surface["widgets"] = [
            "base_task",
            "base_document",
            "task",
            "document",
            "artifacts",
            "published",
        ]
