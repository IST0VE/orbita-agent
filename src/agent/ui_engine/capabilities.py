"""Honest capability negotiation for the currently installed server adapter."""

from __future__ import annotations

from copy import deepcopy

_CAPABILITIES = {
    "engine": "orbita-ui",
    "engine_version": "1.0.0",
    "manifest_versions": ["1.0"],
    "event_versions": ["1.0"],
    "features": {
        # SDK 1.10 exposes the `tasks` stream mode for exact live node
        # start/result/error events. It still does not provide durable replay.
        "event_replay": False,
        "node_lifecycle": True,
        "historical_manifest": False,
        "thread_fork": False,
        "node_retry": False,
        "snapshot_reconciliation": True,
        "resource_adapters": True,
    },
    "limits": {
        "manifest_bytes": 512 * 1024,
        "state_preview_bytes": 1024 * 1024,
        "timeline_page_size": 200,
        "form_depth": 10,
        "form_array_items": 1000,
    },
}


def capabilities() -> dict:
    return deepcopy(_CAPABILITIES)
