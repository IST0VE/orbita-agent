"""UI semantics for the task preparation graph."""

from agent import prep_roles
from agent.ui_engine.graph_manifests.common import base_manifest, name_documents

MANIFEST = base_manifest(prep_roles.PIPELINE)
name_documents(
    MANIFEST,
    {
        prep_roles.TICKET: {"title": "Задача из Jira"},
        prep_roles.LINKED: {"title": "Связанные задачи"},
        prep_roles.PAGES: {"title": "Страницы по ссылкам"},
        prep_roles.FILES: {"title": "Материалы оператора"},
        prep_roles.SOURCES: {"title": "Источники прогона"},
    },
)
