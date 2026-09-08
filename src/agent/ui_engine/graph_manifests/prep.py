"""UI semantics for the task preparation graph."""

from agent import prep_roles
from agent.ui_engine.graph_manifests.common import base_manifest

MANIFEST = base_manifest(prep_roles.PIPELINE)
