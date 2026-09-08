"""Compile-time allowlist mapping graph IDs to validated manifests/resources."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass

from agent.ui_engine.graph_manifests import MANIFESTS
from agent.ui_engine.manifests import ManifestError, ValidatedManifest, validate_manifest

ManifestFactory = Callable[[], dict]


class ManifestNotFound(KeyError):
    pass


@dataclass(frozen=True)
class ResourceAdapter:
    """
    Разрешённый адаптер ресурса.

    Ролей в служебном API нет — есть один общий bearer-токен, — поэтому и
    поля роли здесь нет: объявить его и не проверять значило бы обещать
    разграничение доступа, которого нет. Границу держит сам список: чего в
    нём не записано, того endpoint не выполнит.
    """

    resource_id: str
    operations: frozenset[str]


class UiRegistry:
    def __init__(self) -> None:
        self._manifests: dict[str, ValidatedManifest] = {}
        self._resources: dict[str, ResourceAdapter] = {}

    def register_manifest(self, graph_id: str, manifest: dict | ManifestFactory) -> None:
        if graph_id in self._manifests:
            raise ManifestError(f"manifest is already registered for {graph_id}")
        raw = manifest() if callable(manifest) else manifest
        self._manifests[graph_id] = validate_manifest(raw, expected_graph_id=graph_id)

    def resolve(self, graph_id: str) -> ValidatedManifest:
        try:
            item = self._manifests[graph_id]
        except KeyError as exc:
            raise ManifestNotFound(graph_id) from exc
        return ValidatedManifest(deepcopy(item.value), item.etag, item.warnings)

    def graph_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._manifests))

    def register_resource(self, resource: ResourceAdapter) -> None:
        if resource.resource_id in self._resources:
            raise ValueError(f"resource is already registered: {resource.resource_id}")
        self._resources[resource.resource_id] = resource

    def resource(self, resource_id: str, operation: str) -> ResourceAdapter:
        try:
            resource = self._resources[resource_id]
        except KeyError as exc:
            raise ManifestNotFound(resource_id) from exc
        if operation not in resource.operations:
            raise ManifestNotFound(f"{resource_id}:{operation}")
        return resource


registry = UiRegistry()
for _manifest in MANIFESTS:
    registry.register_manifest(_manifest["graph_id"], _manifest)
registry.register_resource(ResourceAdapter("orbita.tasks", frozenset({"list", "read", "create"})))
registry.register_resource(ResourceAdapter("orbita.publications", frozenset({"list", "read"})))
# Справочник проектов трекера — единственное, что интерфейс спрашивает у Jira.
registry.register_resource(ResourceAdapter("orbita.jira", frozenset({"projects"})))
