"""Manifest validation, canonical serialization and version handling."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from agent.ui_engine.models import Manifest

SUPPORTED_SCHEMA_VERSIONS = ("1.0",)
MAX_MANIFEST_BYTES = 512 * 1024
_VERSION = re.compile(r"^[0-9]+\.[0-9]+(?:\.[0-9A-Za-z_-]+)*$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_FORBIDDEN_KEYS = frozenset({"__proto__", "prototype", "constructor"})
_TOP_LEVEL = frozenset(
    {
        "schema_version",
        "manifest_version",
        "graph_id",
        "title",
        "description",
        "icon",
        "tags",
        "capabilities",
        "input",
        "nodes",
        "state",
        "interrupts",
        "surfaces",
        "actions",
        "redaction",
        "theme",
    }
)


class ManifestError(ValueError):
    """A manifest is unsafe or incompatible with the engine contract."""


@dataclass(frozen=True)
class ValidatedManifest:
    value: Manifest
    etag: str
    warnings: tuple[str, ...] = ()


FALLBACK_MANIFEST_VERSION = "0.0.fallback"


def fallback_manifest(graph_id: str) -> Manifest:
    """
    Контракт для графа, у которого своего манифеста нет.

    Фронтенд в этом случае показывает чат, JSON состояния и универсальную
    форму ответа на остановку. Отвечать на такие действия отказом «манифест
    не найден» значило бы обещать fallback mode и не дать в нём ничего
    сделать; ворота остаются на месте — просто с минимальным набором.
    """
    return {
        "schema_version": SUPPORTED_SCHEMA_VERSIONS[0],
        "manifest_version": FALLBACK_MANIFEST_VERSION,
        "graph_id": graph_id,
        "title": graph_id,
        "capabilities": {"new_thread": True, "stop_run": True, "resume_interrupt": True},
        "actions": [
            {"id": "new-thread", "kind": "thread.create", "label": "Новый тред"},
            {"id": "start-run", "kind": "run.start", "label": "Запустить"},
            {"id": "stop-run", "kind": "run.stop", "label": "Остановить"},
            {"id": "resume-interrupt", "kind": "interrupt.resume", "label": "Продолжить"},
        ],
        "interrupts": [],
    }


def canonical_json(value: Any) -> bytes:
    """Serialize deterministically for hashing, limits and HTTP responses."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def content_etag(value: Any) -> str:
    return f'"sha256:{hashlib.sha256(canonical_json(value)).hexdigest()}"'


def topology_hash(topology: dict[str, Any]) -> str:
    """Hash normalized node/edge identity without unstable presentation fields."""
    nodes = sorted(str(node.get("id", "")) for node in topology.get("nodes", []))
    edges = sorted(
        (
            str(edge.get("source", "")),
            str(edge.get("target", "")),
            str(edge.get("data", "")),
            bool(edge.get("conditional", False)),
        )
        for edge in topology.get("edges", [])
    )
    return f"sha256:{hashlib.sha256(canonical_json({'nodes': nodes, 'edges': edges})).hexdigest()}"


def _walk_safe(value: Any, *, depth: int = 0) -> None:
    if depth > 20:
        raise ManifestError("manifest nesting exceeds 20 levels")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ManifestError("manifest object keys must be strings")
            if key in _FORBIDDEN_KEYS:
                raise ManifestError(f"unsafe object key: {key}")
            _walk_safe(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            _walk_safe(child, depth=depth + 1)
    elif value is not None and not isinstance(value, (bool, int, float, str)):
        raise ManifestError(f"unsupported manifest value: {type(value).__name__}")


def _require_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ManifestError(f"{field} must be a safe non-empty identifier")
    return value


def _validate_binding(binding: Any, field: str) -> None:
    if not isinstance(binding, dict):
        raise ManifestError(f"{field} must be an object")
    _require_id(binding.get("widget"), f"{field}.widget")
    path = binding.get("path")
    if path is not None and (not isinstance(path, str) or len(path) > 512):
        raise ManifestError(f"{field}.path must be a string up to 512 characters")
    if binding.get("empty") not in (None, "hide", "placeholder", "show"):
        raise ManifestError(f"{field}.empty has an unsupported value")


def validate_manifest(raw: Manifest, *, expected_graph_id: str | None = None) -> ValidatedManifest:
    """Validate the security-critical subset and return an immutable copy boundary."""
    if not isinstance(raw, dict):
        raise ManifestError("manifest must be a JSON object")
    _walk_safe(raw)
    try:
        encoded = canonical_json(raw)
    except (TypeError, ValueError) as exc:
        raise ManifestError("manifest is not valid JSON") from exc
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise ManifestError(f"manifest exceeds {MAX_MANIFEST_BYTES} bytes")

    schema_version = raw.get("schema_version")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        supported_major = SUPPORTED_SCHEMA_VERSIONS[0].split(".", 1)[0]
        actual_major = str(schema_version).split(".", 1)[0]
        if actual_major != supported_major:
            raise ManifestError(f"unsupported manifest schema major: {schema_version!r}")
        raise ManifestError(f"unsupported manifest schema version: {schema_version!r}")
    graph_id = _require_id(raw.get("graph_id"), "graph_id")
    if expected_graph_id is not None and graph_id != expected_graph_id:
        raise ManifestError("manifest graph_id does not match registry key")
    version = raw.get("manifest_version")
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise ManifestError("manifest_version must be a dotted version string")
    title = raw.get("title")
    if not isinstance(title, (str, dict)) or not title:
        raise ManifestError("title must be a non-empty string or locale map")

    nodes = raw.get("nodes", {})
    if not isinstance(nodes, dict):
        raise ManifestError("nodes must be an object")
    for node_id, node in nodes.items():
        _require_id(node_id, "node id")
        if not isinstance(node, dict):
            raise ManifestError(f"nodes.{node_id} must be an object")
        for index, binding in enumerate(node.get("details", [])):
            _validate_binding(binding, f"nodes.{node_id}.details[{index}]")
        if "output" in node:
            _validate_binding(node["output"], f"nodes.{node_id}.output")

    seen: set[str] = set()
    for collection in ("input", "state", "interrupts", "surfaces", "actions", "redaction"):
        value = raw.get(collection, [])
        if not isinstance(value, list):
            raise ManifestError(f"{collection} must be an array")
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise ManifestError(f"{collection}[{index}] must be an object")
            item_id = item.get("id")
            if collection not in {"redaction"}:
                _require_id(item_id, f"{collection}[{index}].id")
                unique = f"{collection}:{item_id}"
                if unique in seen:
                    raise ManifestError(f"duplicate id {item_id!r} in {collection}")
                seen.add(unique)
            if collection == "state":
                _validate_binding(item, f"state[{index}]")
            if collection == "input":
                _require_id(item.get("widget"), f"input[{index}].widget")
                if not isinstance(item.get("target"), str):
                    raise ManifestError(f"input[{index}].target is required")
            if collection == "interrupts":
                _require_id(item.get("widget"), f"interrupts[{index}].widget")
                if not isinstance(item.get("resume_schema"), dict):
                    raise ManifestError(f"interrupts[{index}].resume_schema is required")
            if collection == "redaction":
                if not isinstance(item.get("path"), str):
                    raise ManifestError(f"redaction[{index}].path is required")
                if item.get("mode") not in {
                    "remove",
                    "mask",
                    "truncate",
                    "metadata_only",
                    "role",
                }:
                    raise ManifestError(f"redaction[{index}].mode is unsupported")

    warnings = tuple(f"unknown top-level field ignored: {key}" for key in raw if key not in _TOP_LEVEL)
    value = deepcopy(raw)
    return ValidatedManifest(value=value, etag=content_etag(value), warnings=warnings)
