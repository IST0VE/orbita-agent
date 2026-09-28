"""UI semantics for the architectural analysis graph."""

from agent import roles
from agent.ui_engine.graph_manifests.common import base_manifest, name_documents

MANIFEST = base_manifest(roles.PIPELINE)
# Что кладёт в `artifacts` код, а не роль: прелюдия чтения материалов и реестр
# источников аналитика. Без записи здесь они показывались бы ключами.
name_documents(
    MANIFEST,
    {
        roles.LINKS: {"title": roles.LINKS_TITLE},
        roles.FILES: {"title": "Материалы оператора"},
        roles.SOURCES: {"title": "Источники прогона"},
    },
)
