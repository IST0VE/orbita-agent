"""UI semantics for the project manager graph."""

from agent import pm_roles
from agent.ui_engine.graph_manifests.common import base_manifest, name_documents

MANIFEST = base_manifest(pm_roles.PIPELINE)
name_documents(MANIFEST, {pm_roles.BOARD: {"title": pm_roles.BOARD_TITLE}})

# Вход — номер доски и, по желанию, ёмкость словами запроса. Файлов у
# конвейера нет: доску читает код из Jira, и поля файлов чата остались бы
# пустым списком, который ничего не меняет.
MANIFEST["input"] = [MANIFEST["input"][0]]
MANIFEST["input"][0]["title"] = (
    "Доска и вопрос: «доска 42: где мы и что брать в следующий спринт», "
    "можно с ёмкостью — «ёмкость 30»"
)

# Главное посчитанное — рядом со стоимостью: этап спринта, скорость, ёмкость
# и сколько вошло в план видны, не открывая документ.
MANIFEST["state"].append(
    {
        "id": "pm",
        "path": "pm_view",
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
        surface["widgets"] = ["notes", "summary", "pm", "cost", "publication"]
