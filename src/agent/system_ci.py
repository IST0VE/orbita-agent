"""Deterministic System Analysis CI on top of Orbita package checks.

This layer keeps LLMs out of blocking decisions. It turns the existing
agent.checks report into a repository/PR workflow with an inspectable graph,
accepted baseline, new-vs-existing delta, and deterministic impact queries.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from pathlib import Path
from typing import Any

from agent import checks

SCHEMA_VERSION = 1
TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".json", ".yaml", ".yml"}
SKIP_DIRS = {
    ".git",
    ".idea",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "build",
    "dist",
    "node_modules",
    "venv",
}
BASELINE_FILENAME = ".orbita-ci-baseline.json"
SEVERITY_ORDER = {checks.BLOCKER: 0, checks.MAJOR: 1, checks.MINOR: 2}


def _read_text(path: Path, *, max_file_bytes: int) -> str | None:
    try:
        if path.stat().st_size > max_file_bytes:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def load_package(
    root: str | Path,
    *,
    max_file_bytes: int = 1_000_000,
) -> tuple[dict[str, str], list[str]]:
    """Load supported text artifacts under root in deterministic path order."""
    base = Path(root).resolve()
    if not base.exists():
        raise FileNotFoundError(f"package root does not exist: {base}")
    if not base.is_dir():
        raise NotADirectoryError(f"package root is not a directory: {base}")

    package: dict[str, str] = {}
    skipped: list[str] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(base)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        if path.name == BASELINE_FILENAME or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = _read_text(path, max_file_bytes=max_file_bytes)
        name = rel.as_posix()
        if text is None:
            skipped.append(name)
            continue
        package[name] = text
    return package, skipped


def finding_fingerprint(finding: dict[str, Any]) -> str:
    """Stable identifier for one deterministic finding."""
    payload = {
        "kind": finding.get("kind") or "",
        "severity": finding.get("severity") or "",
        "text": finding.get("text") or "",
        "where": sorted(finding.get("where") or []),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _finding_with_id(finding: dict[str, Any]) -> dict[str, Any]:
    return {**finding, "fingerprint": finding_fingerprint(finding)}


def _node_id(kind: str, value: str) -> str:
    return f"{kind}:{value}"


def build_graph(report: dict[str, Any]) -> dict[str, Any]:
    """Build an inspectable graph only from evidence produced by checks."""
    nodes: dict[str, dict[str, Any]] = {}
    edges: set[tuple[str, str, str]] = set()

    for document in report.get("documents") or []:
        name = str(document["name"])
        node_id = _node_id("doc", name)
        nodes[node_id] = {
            "id": node_id,
            "kind": "document",
            "label": name,
            "metadata": {
                "chars": document.get("chars", 0),
                "headings": document.get("headings") or [],
            },
        }

    for requirement in report.get("requirements") or []:
        key = str(requirement["key"])
        req_id = _node_id("req", key)
        nodes[req_id] = {
            "id": req_id,
            "kind": "requirement",
            "label": key,
            "metadata": {"covered": bool(requirement.get("covered"))},
        }
        for name in requirement.get("declared_in") or []:
            edges.add((_node_id("doc", str(name)), req_id, "declares"))
        for name in requirement.get("mentioned_in") or []:
            edges.add((_node_id("doc", str(name)), req_id, "mentions"))

    for route, names in (report.get("endpoints") or {}).items():
        endpoint_id = _node_id("endpoint", str(route))
        nodes[endpoint_id] = {
            "id": endpoint_id,
            "kind": "endpoint",
            "label": str(route),
            "metadata": {},
        }
        for name in names:
            edges.add((_node_id("doc", str(name)), endpoint_id, "contains"))

    for finding in report.get("findings") or []:
        fingerprint = finding_fingerprint(finding)
        finding_id = _node_id("finding", fingerprint)
        nodes[finding_id] = {
            "id": finding_id,
            "kind": "finding",
            "label": str(finding.get("kind") or "finding"),
            "metadata": {
                "severity": finding.get("severity"),
                "text": finding.get("text"),
            },
        }
        for name in finding.get("where") or []:
            doc_id = _node_id("doc", str(name))
            if doc_id in nodes:
                edges.add((finding_id, doc_id, "reported_in"))

    return {
        "nodes": [nodes[key] for key in sorted(nodes)],
        "edges": [
            {"source": source, "target": target, "relation": relation}
            for source, target, relation in sorted(edges)
        ],
    }


def load_baseline(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {"schema_version": SCHEMA_VERSION, "findings": []}
    baseline = Path(path)
    if not baseline.exists():
        return {"schema_version": SCHEMA_VERSION, "findings": []}
    data = json.loads(baseline.read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported baseline schema: {data.get('schema_version')!r}; "
            f"expected {SCHEMA_VERSION}"
        )
    if not isinstance(data.get("findings"), list):
        raise ValueError("baseline field 'findings' must be a list")
    return data


def compare_with_baseline(
    report: dict[str, Any],
    baseline: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Split current findings into new/existing and find resolved baseline items."""
    current = [_finding_with_id(item) for item in report.get("findings") or []]
    accepted = [
        _finding_with_id(item)
        for item in (baseline or {}).get("findings", [])
        if isinstance(item, dict)
    ]
    current_by_id = {item["fingerprint"]: item for item in current}
    accepted_by_id = {item["fingerprint"]: item for item in accepted}
    return {
        "new": [
            item for fingerprint, item in current_by_id.items() if fingerprint not in accepted_by_id
        ],
        "existing": [
            item for fingerprint, item in current_by_id.items() if fingerprint in accepted_by_id
        ],
        "resolved": [
            item for fingerprint, item in accepted_by_id.items() if fingerprint not in current_by_id
        ],
    }


def analyse(
    root: str | Path,
    *,
    baseline: dict[str, Any] | None = None,
    max_file_bytes: int = 1_000_000,
) -> dict[str, Any]:
    package, skipped = load_package(root, max_file_bytes=max_file_bytes)
    report = checks.run(package)
    return {
        "schema_version": SCHEMA_VERSION,
        "root": str(Path(root)),
        "skipped": skipped,
        "report": report,
        "graph": build_graph(report),
        "delta": compare_with_baseline(report, baseline),
    }


def baseline_from(result: dict[str, Any]) -> dict[str, Any]:
    """Build the accepted-finding baseline from an analysis result."""
    return {
        "schema_version": SCHEMA_VERSION,
        "findings": [
            {
                "kind": item.get("kind"),
                "severity": item.get("severity"),
                "text": item.get("text"),
                "where": item.get("where") or [],
            }
            for item in result["report"].get("findings") or []
        ],
    }


def write_baseline(path: str | Path, result: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(baseline_from(result), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def should_fail(result: dict[str, Any], fail_on: str) -> bool:
    """Return True when a new finding reaches the configured threshold."""
    if fail_on == "none":
        return False
    if fail_on not in SEVERITY_ORDER:
        raise ValueError(f"unknown severity threshold: {fail_on}")
    threshold = SEVERITY_ORDER[fail_on]
    return any(
        SEVERITY_ORDER.get(str(item.get("severity")), 99) <= threshold
        for item in result["delta"]["new"]
    )


def _severity_counts(items: list[dict[str, Any]]) -> str:
    counts = {name: 0 for name in SEVERITY_ORDER}
    for item in items:
        severity = str(item.get("severity"))
        if severity in counts:
            counts[severity] += 1
    return ", ".join(
        f"{name}: {counts[name]}"
        for name in (checks.BLOCKER, checks.MAJOR, checks.MINOR)
    )


def render_markdown(result: dict[str, Any]) -> str:
    report = result["report"]
    delta = result["delta"]
    requirements = report.get("requirements") or []
    covered = sum(1 for item in requirements if item.get("covered"))
    graph = result["graph"]
    lines = [
        "# Orbita System Analysis CI",
        "",
        f"- Documents: **{len(report.get('documents') or [])}**",
        f"- Requirements: **{len(requirements)}**; linked to design: **{covered}**",
        f"- Endpoints: **{len(report.get('endpoints') or {})}**",
        f"- Graph: **{len(graph['nodes'])} nodes / {len(graph['edges'])} edges**",
        f"- New findings: **{len(delta['new'])}** ({_severity_counts(delta['new'])})",
        f"- Accepted findings: **{len(delta['existing'])}**",
        f"- Resolved since baseline: **{len(delta['resolved'])}**",
    ]
    if result.get("skipped"):
        lines.extend(["", "## Skipped files"])
        lines.extend(f"- '{name}'" for name in result["skipped"])

    lines.extend(["", "## New findings"])
    if not delta["new"]:
        lines.append("No new deterministic findings.")
    else:
        for finding in delta["new"]:
            where = ", ".join(finding.get("where") or [])
            suffix = f" — {where}" if where else ""
            lines.append(
                f"- **{finding.get('severity')}** '{finding.get('kind')}'{suffix}: "
                f"{finding.get('text')}"
            )

    if delta["resolved"]:
        lines.extend(["", "## Resolved since baseline"])
        for finding in delta["resolved"]:
            lines.append(
                f"- '{finding.get('kind')}' ({finding.get('severity')}): "
                f"{finding.get('text')}"
            )

    lines.extend(
        [
            "",
            "> Blocking is based only on deterministic checks and only on findings "
            "that are new relative to the accepted baseline.",
        ]
    )
    return "\n".join(lines) + "\n"


def _resolve_node(graph: dict[str, Any], query: str) -> str:
    nodes = {item["id"]: item for item in graph.get("nodes") or []}
    if query in nodes:
        return query
    candidates = [
        item["id"]
        for item in nodes.values()
        if item.get("label") == query
        or item["id"].lower() == query.lower()
        or str(item.get("label", "")).lower() == query.lower()
    ]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise KeyError(f"graph node not found: {query}")
    raise KeyError(f"graph node is ambiguous: {query}; matches: {', '.join(candidates)}")


def impact(result: dict[str, Any], query: str, *, depth: int = 2) -> dict[str, Any]:
    """Traverse evidence relations around a requirement, endpoint or document."""
    if depth < 0:
        raise ValueError("depth must be >= 0")
    graph = result["graph"]
    start = _resolve_node(graph, query)
    nodes = {item["id"]: item for item in graph.get("nodes") or []}
    adjacency: dict[str, list[tuple[str, str]]] = {}
    for edge in graph.get("edges") or []:
        source = str(edge["source"])
        target = str(edge["target"])
        relation = str(edge["relation"])
        adjacency.setdefault(source, []).append((target, relation))
        adjacency.setdefault(target, []).append((source, relation))

    seen = {start: 0}
    queue: deque[str] = deque([start])
    traversed: set[tuple[str, str, str]] = set()
    while queue:
        current = queue.popleft()
        current_depth = seen[current]
        if current_depth >= depth:
            continue
        for neighbour, relation in sorted(adjacency.get(current, [])):
            traversed.add((current, neighbour, relation))
            if neighbour not in seen:
                seen[neighbour] = current_depth + 1
                queue.append(neighbour)

    return {
        "query": query,
        "start": start,
        "depth": depth,
        "nodes": [
            {**nodes[node_id], "distance": distance}
            for node_id, distance in sorted(seen.items(), key=lambda item: (item[1], item[0]))
        ],
        "edges": [
            {"source": source, "target": target, "relation": relation}
            for source, target, relation in sorted(traversed)
            if source in seen and target in seen
        ],
    }


def render_impact(result: dict[str, Any]) -> str:
    lines = [
        f"# Orbita impact: {result['query']}",
        "",
        f"Start: '{result['start']}'; depth: **{result['depth']}**",
        "",
        "| Distance | Kind | Artifact |",
        "|---:|---|---|",
    ]
    for node in result["nodes"]:
        lines.append(f"| {node['distance']} | {node['kind']} | '{node['label']}' |")
    return "\n".join(lines) + "\n"
