"""UI semantics for the flow metrics graph."""

from agent import flow_roles
from agent.ui_engine.graph_manifests.common import base_manifest, name_documents

MANIFEST = base_manifest(flow_roles.PIPELINE)
name_documents(MANIFEST, {flow_roles.FLOW: {"title": flow_roles.FLOW_TITLE}})

# Вход — номер доски словами запроса, файлов у конвейера нет: доску читает
# код из Jira. Поля файлов чата остались бы пустым списком, который ничего
# не меняет.
MANIFEST["input"] = [MANIFEST["input"][0]]
MANIFEST["input"][0]["title"] = "Доска и период: «доска 42 за квартал» или адрес доски"

# Одна роль — ворот между этапами нет, и спросить «продолжать ли» негде.
MANIFEST["interrupts"] = [
    item for item in MANIFEST["interrupts"] if item["id"] != "stage-approval"
]

# Итог посчитанного — рядом со стоимостью: какая доска, какой период и
# главные числа видны, не открывая документ.
MANIFEST["state"].append(
    {
        "id": "flow",
        "path": "flow_view",
        "title": "Посчитано",
        "widget": "key-value",
        "surface": "right",
        "order": 15,
        "empty": "hide",
    }
)

for surface in MANIFEST["surfaces"]:
    if surface["id"] == "left":
        surface["widgets"] = ["publication-links", "artifacts", "published"]
    elif surface["id"] == "right":
        surface["widgets"] = ["notes", "summary", "flow", "cost", "publication"]
