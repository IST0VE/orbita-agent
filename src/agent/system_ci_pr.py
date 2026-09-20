"""Pull-request scoping for Orbita System Analysis CI."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from agent import system_ci

COMMENT_MARKER = "<!-- orbita-system-analysis-ci -->"


def _normalise(path: str) -> str:
    return PurePosixPath(path.replace("\\", "/")).as_posix().lstrip("./")


def changed_in_package(changed_paths: list[str], package_root: str) -> list[str]:
    """Return changed file names relative to one configured engineering package."""
    prefix = _normalise(package_root).rstrip("/")
    out: list[str] = []
    for raw in changed_paths:
        path = _normalise(raw)
        if prefix in {"", "."}:
            relative = path
        elif path.startswith(prefix + "/"):
            relative = path[len(prefix) + 1 :]
        else:
            continue
        suffix = PurePosixPath(relative).suffix.lower()
        if (
            suffix in system_ci.TEXT_SUFFIXES
            and PurePosixPath(relative).name != system_ci.BASELINE_FILENAME
        ):
            out.append(relative)
    return sorted(set(out))


def review(
    package_root: str,
    changed_paths: list[str],
    *,
    baseline: dict[str, Any] | None = None,
    depth: int = 2,
) -> dict[str, Any]:
    """Analyse a package and scope impact to files changed by a pull request."""
    analysed = system_ci.analyse(package_root, baseline=baseline)
    relevant = changed_in_package(changed_paths, package_root)
    graph_labels = {
        node["label"]: node
        for node in analysed["graph"].get("nodes") or []
        if node.get("kind") == "document"
    }

    impacts: dict[str, dict[str, Any]] = {}
    impacted_nodes: dict[str, dict[str, Any]] = {}
    for relative in relevant:
        if relative not in graph_labels:
            continue
        item = system_ci.impact(analysed, relative, depth=depth)
        impacts[relative] = item
        for node in item["nodes"]:
            previous = impacted_nodes.get(node["id"])
            if previous is None or node["distance"] < previous["distance"]:
                impacted_nodes[node["id"]] = node

    new_findings = analysed["delta"]["new"] if relevant else []
    status = "not_applicable"
    if relevant:
        status = "findings" if new_findings else "clean"

    return {
        "package_root": package_root,
        "changed_paths": sorted(set(_normalise(path) for path in changed_paths)),
        "relevant_changes": relevant,
        "status": status,
        "new_findings": new_findings,
        "accepted_findings": analysed["delta"]["existing"],
        "resolved_findings": analysed["delta"]["resolved"] if relevant else [],
        "impacted_nodes": [
            impacted_nodes[key]
            for key in sorted(
                impacted_nodes,
                key=lambda node_id: (
                    impacted_nodes[node_id]["distance"],
                    impacted_nodes[node_id]["kind"],
                    impacted_nodes[node_id]["label"],
                ),
            )
        ],
        "impacts": impacts,
        "analysis": analysed,
    }


def should_fail(review_result: dict[str, Any], fail_on: str) -> bool:
    if review_result["status"] == "not_applicable":
        return False
    shadow = {"delta": {"new": review_result["new_findings"]}}
    return system_ci.should_fail(shadow, fail_on)


def render_markdown(review_result: dict[str, Any]) -> str:
    lines = [
        COMMENT_MARKER,
        "## Orbita System Analysis Review",
        "",
        f"Package: '{review_result['package_root']}'",
    ]

    if review_result["status"] == "not_applicable":
        lines.extend(
            [
                "",
                "No supported engineering artifacts in this package changed in the PR.",
            ]
        )
        return "\n".join(lines) + "\n"

    lines.extend(["", "### Changed artifacts"])
    for name in review_result["relevant_changes"]:
        lines.append(f"- '{name}'")

    lines.extend(["", "### Impact"])
    nodes = review_result["impacted_nodes"]
    if not nodes:
        lines.append(
            "No surviving graph node matched the changed artifact. "
            "This can happen when an artifact was deleted."
        )
    else:
        lines.append("| Distance | Kind | Artifact |")
        lines.append("|---:|---|---|")
        for node in nodes[:40]:
            lines.append(
                f"| {node['distance']} | {node['kind']} | '{node['label']}' |"
            )
        if len(nodes) > 40:
            lines.append(f"|  |  | ... {len(nodes) - 40} more |")

    lines.extend(["", "### New deterministic findings"])
    findings = review_result["new_findings"]
    if not findings:
        lines.append("No new deterministic findings.")
    else:
        for finding in findings:
            where = ", ".join(finding.get("where") or [])
            suffix = f" — {where}" if where else ""
            lines.append(
                f"- **{finding.get('severity')}** '{finding.get('kind')}'{suffix}: "
                f"{finding.get('text')}"
            )

    resolved = review_result["resolved_findings"]
    if resolved:
        lines.extend(["", "### Resolved since baseline"])
        for finding in resolved:
            lines.append(
                f"- '{finding.get('kind')}' ({finding.get('severity')}): "
                f"{finding.get('text')}"
            )

    lines.extend(
        [
            "",
            "> Orbita only blocks on reproducible deterministic checks. "
            "Semantic AI review is a separate advisory layer.",
        ]
    )
    return "\n".join(lines) + "\n"
