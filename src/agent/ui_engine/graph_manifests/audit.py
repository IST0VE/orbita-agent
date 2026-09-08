"""UI semantics for the package audit graph."""

from agent import audit_roles
from agent.ui_engine.graph_manifests.common import base_manifest

MANIFEST = base_manifest(audit_roles.PIPELINE)
