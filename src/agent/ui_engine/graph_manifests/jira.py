"""UI semantics for the Jira decomposition graph."""

from agent import jira_roles
from agent.ui_engine.graph_manifests.common import base_manifest

MANIFEST = base_manifest(jira_roles.PIPELINE)

# Ключи заведённых задач — результат узла, а не отчёт о нём: интерфейс рисует
# их тем же виджетом, что и список слева. Общий манифест этого не выводит:
# путь в состоянии и виджет знает только тот конвейер, у которого они есть.
MANIFEST["nodes"]["create"]["output"] = {"path": "issues", "widget": "issue-list"}

# Единственный вопрос конвейера к оператору. Стоит рядом с папкой задачи, а не
# в окне подтверждения, потому что ответ на него известен до прогона: проект
# выбирают один раз и на весь тред. Окно подтверждения спросит его повторно
# только тогда, когда здесь и в JIRA_PROJECT_KEY пусто.
MANIFEST["input"].append(
    {
        "id": "jira_project",
        "target": "configurable.jira_project",
        "widget": "jira-project",
        "title": "Проект Jira",
        "source": {"resource_id": "orbita.jira", "operation": "projects"},
    }
)

# Ключи и ссылки — то, ради чего конвейер запускали. Стоят выше документов
# этапов: документы можно перечитать, а ссылку на заведённую задачу ищут сразу.
MANIFEST["state"].append(
    {
        "id": "issues",
        "path": "issues",
        "title": "Задачи Jira",
        "widget": "issue-list",
        "surface": "left",
        "order": 25,
        "empty": "hide",
    }
)

MANIFEST["interrupts"].append(
    {
        "id": "jira-create",
        # Приоритет выше остальных: остановка перед внешним побочным эффектом
        # сильнее подтверждения этапа и публикации.
        "priority": 30,
        "match": {"path": "action", "equals": "jira"},
        "widget": "approval",
        "resume_schema": {
            "type": "object",
            "required": ["decision"],
            "additionalProperties": False,
            "properties": {
                "decision": {"enum": ["approved", "rejected", "drafts"]},
                # Ключ проекта: буквы, цифры и подчёркивание, как в самой Jira.
                "project": {
                    "type": "string",
                    "maxLength": 40,
                    "pattern": "^[A-Za-z][A-Za-z0-9_]*$",
                },
                "reason": {"type": "string", "maxLength": 4000},
            },
        },
    }
)

for surface in MANIFEST["surfaces"]:
    if surface["id"] == "left":
        surface["widgets"] = [
            "task",
            "document",
            "jira_project",
            "issues",
            "artifacts",
            "published",
        ]
