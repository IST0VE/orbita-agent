"""Type definitions for the JSON contracts exposed by the UI engine.

The transport is intentionally made of ordinary dictionaries.  This keeps the
contract independent from a particular validation library and makes the JSON
Schema in ``schemas/`` the canonical cross-language description.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class ManifestCapabilities(TypedDict, total=False):
    new_thread: bool
    stop_run: bool
    resume_interrupt: bool
    history: bool
    retry_node: bool


class WidgetBinding(TypedDict, total=False):
    path: str
    widget: str
    title: str
    surface: str
    order: int
    visible_when: dict[str, JsonValue]
    options: dict[str, JsonValue]
    empty: Literal["hide", "placeholder", "show"]


class NodeManifest(TypedDict, total=False):
    title: str
    description: str
    icon: str
    kind: Literal["task", "router", "tool", "approval", "system"]
    group: str
    color: Literal["neutral", "info", "success", "warning", "danger"]
    hidden: bool
    output: WidgetBinding
    badges: list[WidgetBinding]
    details: list[WidgetBinding]
    layout: dict[str, int | bool]


class InputBinding(TypedDict, total=False):
    id: str
    target: str
    widget: str
    title: str
    required: bool
    source: dict[str, str]
    options: dict[str, JsonValue]


class StateBinding(WidgetBinding, total=False):
    id: str


class InterruptBinding(TypedDict, total=False):
    id: str
    priority: int
    match: dict[str, JsonValue]
    widget: str
    resume_schema: dict[str, JsonValue]
    bindings: list[WidgetBinding]


class SurfaceManifest(TypedDict, total=False):
    id: str
    title: str
    order: int
    widgets: list[str]
    collapsible: bool


class ActionManifest(TypedDict, total=False):
    id: str
    kind: str
    label: str
    permission: str
    confirm: bool
    input_schema: dict[str, JsonValue]
    visible_when: dict[str, JsonValue]


class RedactionRule(TypedDict, total=False):
    path: str
    mode: Literal["remove", "mask", "truncate", "metadata_only", "role"]
    max_length: int
    roles: list[str]


class UiManifest(TypedDict):
    schema_version: str
    manifest_version: str
    graph_id: str
    title: str | dict[str, str]
    description: NotRequired[str | dict[str, str]]
    icon: NotRequired[str]
    tags: NotRequired[list[str]]
    capabilities: NotRequired[ManifestCapabilities]
    input: NotRequired[list[InputBinding]]
    nodes: NotRequired[dict[str, NodeManifest]]
    state: NotRequired[list[StateBinding]]
    interrupts: NotRequired[list[InterruptBinding]]
    surfaces: NotRequired[list[SurfaceManifest]]
    actions: NotRequired[list[ActionManifest]]
    redaction: NotRequired[list[RedactionRule]]
    theme: NotRequired[dict[str, JsonValue]]


Manifest = dict[str, Any]
