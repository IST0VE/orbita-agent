"""UI semantics for the package audit graph."""

from agent import audit_roles
from agent.ui_engine.graph_manifests.common import base_manifest, name_documents

MANIFEST = base_manifest(audit_roles.PIPELINE)
name_documents(
    MANIFEST,
    {
        audit_roles.PACKAGE: {"title": audit_roles.PACKAGE_TITLE},
        audit_roles.CHECKS: {"title": audit_roles.CHECKS_TITLE},
    },
)
