"""UI semantics for the draw.io diagram graph."""

from agent import diagram_roles
from agent.ui_engine.graph_manifests.common import base_manifest, name_documents

MANIFEST = base_manifest(diagram_roles.PIPELINE)

# Источник у этого конвейера один и он же документ — сама схема, — поэтому
# общее поле «Документ» ему не нужно: выбирать текстовый файл там, где читается
# только .drawio, значит обещать несуществующее. Вместо него своё поле.
MANIFEST["input"] = [item for item in MANIFEST["input"] if item["id"] != "document"]

# Какую схему разбирать. Схема в чате одна — граф берёт её и называет; схем
# несколько, а отмеченной нет — отказывает до вызова модели, а не берёт первую
# по алфавиту: оператор получил бы документацию не по той (`drawio_graph._pick`).
#
# `depends_on` — id поля, из которого берётся список файлов: знание о том,
# какое это поле, остаётся в манифесте, чтобы виджет не знал про `task` по имени.
MANIFEST["input"].append(
    {
        "id": "diagram",
        "target": "configurable.diagram",
        "widget": "file-picker",
        "title": "Схема",
        "options": {"kind": "diagram", "depends_on": "task"},
    }
)

# Отметка .drawio в файлах чата выбирает схему — то же самое поле, что и строка
# выше. Выбор один, показан он дважды: там, где на файлы смотрят, и там, где
# выбранное видно одной строкой.
for item in MANIFEST["input"]:
    if item["id"] == "task":
        item["options"] = {
            **item["options"],
            "pick": [{"input": "diagram", "kind": "diagram", "multiple": False}],
        }

# Разобранную схему нода `source` кладёт в `artifacts` рядом с документами
# ролей (`drawio_graph.source_node`). Это данные, а не текст: в колонке они
# показываются как есть и скачиваются файлом своего формата.
name_documents(
    MANIFEST,
    {
        diagram_roles.DIAGRAM: {"title": "Данные схемы", "format": "json"},
        diagram_roles.IDS: {"title": "Идентификаторы схемы", "format": "text"},
    },
)

for surface in MANIFEST["surfaces"]:
    if surface["id"] == "left":
        surface["widgets"] = ["task", "diagram", "publication-links", "artifacts", "published"]
