"""UI semantics for the draw.io diagram graph."""

from agent import diagram_roles
from agent.ui_engine.graph_manifests.common import base_manifest

MANIFEST = base_manifest(diagram_roles.PIPELINE)

# Источник у этого конвейера один и он же документ — сама схема, — поэтому
# общее поле «Документ» ему не нужно: выбирать текстовый файл там, где читается
# только .drawio, значит обещать несуществующее. Вместо него своё поле.
MANIFEST["input"] = [item for item in MANIFEST["input"] if item["id"] != "document"]

# Какую схему разбирать. Поле было в `drawio_graph.Options` с самого начала, и
# сообщение об ошибке отправляло оператора «выбрать её в интерфейсе» — а выбрать
# было нечем: без этой строки значение никуда не уезжало, и граф всегда брал
# первый найденный файл. В папке с одной схемой разницы не видно; в папке, где
# схем две, оператор получал документацию не по той.
#
# `depends_on` — id поля, от которого зависит список: файлы показываются из
# выбранной папки задачи, а не из всех сразу. Знание о том, какое это поле,
# остаётся в манифесте, чтобы виджет не знал про `task` по имени.
MANIFEST["input"].append(
    {
        "id": "diagram",
        "target": "configurable.diagram",
        "widget": "file-picker",
        "title": "Схема",
        "source": {"resource_id": "orbita.tasks", "operation": "list"},
        "options": {"kind": "diagram", "depends_on": "task"},
    }
)

# Клик по .drawio в дереве папки выбирает схему — то же самое поле, что и
# список выше. Выбор один, показан он дважды: там, где на файлы смотрят, и
# там, где выбранное видно одной строкой.
for item in MANIFEST["input"]:
    if item["id"] == "task":
        item["options"] = {"document_input": "diagram", "document_kind": "diagram"}

for surface in MANIFEST["surfaces"]:
    if surface["id"] == "left":
        surface["widgets"] = ["task", "diagram", "artifacts", "published"]
