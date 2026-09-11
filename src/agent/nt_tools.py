"""Scoped, bounded read-only tools. LLM never supplies URLs, PromQL, SQL or shell."""

from __future__ import annotations

import json
import logging
import time
from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from agent import confluence
from agent import tools as common_tools
from agent.nt.baseline import summarize
from agent.nt.collection import Sources
from agent.nt.models import failure
from agent.nt.policy import allowed
from agent.nt.settings import load_settings

log = logging.getLogger(__name__)


def bounded(result: dict, limit: int = 12000) -> dict:
    text = confluence.mask_text(json.dumps(result, ensure_ascii=False, allow_nan=False))
    if len(text) > limit:
        return failure("OUTPUT_LIMIT", "response exceeds tool limit; narrow the request")
    try:
        return json.loads(text)
    except ValueError:
        return failure("REDACTION_ERROR", "response omitted after redaction")


def build_tools(sources: Sources | None = None, *, settings=None) -> list:
    def get_sources():
        return sources or Sources.from_env(settings or load_settings())

    def run(name, state, function):
        began = time.monotonic()
        result = failure("TOOL_ERROR", "read failed")
        try:
            if not allowed(name) or time.time() >= state.get("deadline_at", float("inf")):
                return failure("POLICY_DENIED", "operation denied or analysis timed out")
            result = bounded(function())
            return result
        except Exception:
            return result
        finally:
            log.info("nt_tool run_id=%s jira=%s test_id=%s tool=%s success=%s duration_ms=%d",
                     state.get("run_id"), state.get("jira_key"), state.get("test_id"), name,
                     result.get("success"), (time.monotonic() - began) * 1000)

    def query(metric, service, state, source):
        if service not in state.get("services", []):
            return failure("OUT_OF_SCOPE", "service is outside the analysis scope")
        result = get_sources().query(metric, {**state, "services": [service], "scope_explicit": True},
                                    state["started_at"], state["finished_at"], source=source)
        # Отказ без рядов приходит плоским (METRIC_NOT_ALLOWED и подобные).
        # Без переноса причины модель видит только «не получилось» и гадает,
        # метрики нет на сервере или нет данных по этому сервису.
        errors = list(result.get("errors", []))
        if not result["success"] and result.get("error_type"):
            errors.append({k: result[k] for k in ("error_type", "message") if k in result})
        return {"success": result["success"], "metrics": summarize(result.get("series", [])),
                "errors": errors, "period": "test"}

    @tool
    def prometheus_range_query(metric: str, service: str,
                               state: Annotated[dict, InjectedState]) -> dict:
        """Read an allowlisted metric for a scoped service over the test period; return statistics."""
        return run("prometheus_range_query", state, lambda: query(metric, service, state, "prometheus"))

    @tool
    def influx_query(metric: str, service: str, state: Annotated[dict, InjectedState]) -> dict:
        """Read an allowlisted Influx metric for a scoped service; return test-period statistics."""
        return run("influx_query", state, lambda: query(metric, service, state, "influx"))

    @tool
    def get_pods(service: str, state: Annotated[dict, InjectedState]) -> dict:
        """Read CURRENT pods for a scoped service. This is not historical evidence."""
        def fetch():
            backend = get_sources().kubernetes
            if service not in state.get("services", []) or not backend:
                return failure("UNAVAILABLE_OR_OUT_OF_SCOPE", "Kubernetes read is unavailable")
            return backend.get_pods(state["namespace"], service)
        return run("get_pods", state, fetch)

    @tool
    def get_pod_metrics(service: str, state: Annotated[dict, InjectedState]) -> dict:
        """Read CURRENT pod CPU/memory for a scoped service, bounded output."""
        def fetch():
            backend = get_sources().kubernetes
            if service not in state.get("services", []) or not backend:
                return failure("UNAVAILABLE_OR_OUT_OF_SCOPE", "Kubernetes metrics are unavailable")
            return backend.get_pod_metrics(state["namespace"], service)
        return run("get_pod_metrics", state, fetch)

    @tool
    def get_k8s_events(service: str, state: Annotated[dict, InjectedState]) -> dict:
        """Read bounded CURRENT Kubernetes events for the selected scoped service."""
        def fetch():
            backend = get_sources().kubernetes
            if service not in state.get("services", []) or not backend:
                return failure("UNAVAILABLE_OR_OUT_OF_SCOPE", "Kubernetes events are unavailable")
            return backend.get_events(state["namespace"], service)
        return run("get_k8s_events", state, fetch)

    @tool
    def get_test_status(state: Annotated[dict, InjectedState]) -> dict:
        """Read status of the current test ID using the configured load-testing gateway."""
        def fetch():
            backend = get_sources().load_testing
            return backend.get_test_status(state["test_id"]) if backend else failure(
                "NOT_CONFIGURED", "load-testing gateway not configured")
        return run("get_test_status", state, fetch)

    @tool
    def get_test_results(state: Annotated[dict, InjectedState]) -> dict:
        """Read bounded summary metadata for the current test; no raw load-generator output."""
        def fetch():
            backend = get_sources().load_testing
            return backend.get_test_results(state["test_id"]) if backend else failure(
                "NOT_CONFIGURED", "load-testing gateway not configured")
        return run("get_test_results", state, fetch)

    def text_read(name, argument, state):
        if len(argument) > 200:
            return failure("INPUT_LIMIT", "query too long")
        source_tool = getattr(common_tools, name)
        key = "key" if name == "jira_issue" else "page_id" if name == "confluence_page" else "query"
        text = source_tool.invoke({key: argument})
        if (text.startswith(("Jira не настроена", "Confluence не настроена"))
                or "не прочитана:" in text or "поиск в Jira не выполнен:" in text
                or "поиск в Confluence не выполнен:" in text):
            return failure("CONTEXT_UNAVAILABLE", "context source read failed")
        return {"success": True, "text": text[:8000], "truncated": len(text) > 8000,
                "note": "context text is not verified metric evidence"}

    @tool
    def jira_issue(key: str, state: Annotated[dict, InjectedState]) -> dict:
        """Read Jira issue through the existing Orbita integration, with bounded output."""
        return run("jira_issue", state, lambda: text_read("jira_issue", key, state))

    @tool
    def jira_search(query: str, state: Annotated[dict, InjectedState]) -> dict:
        """Search Jira for additional task context through the existing integration."""
        return run("jira_search", state, lambda: text_read("jira_search", query, state))

    @tool
    def confluence_search(query: str, state: Annotated[dict, InjectedState]) -> dict:
        """Search Confluence for service documentation through the existing integration."""
        return run("confluence_search", state, lambda: text_read("confluence_search", query, state))

    @tool
    def confluence_page(page_id: str, state: Annotated[dict, InjectedState]) -> dict:
        """Read a Confluence page with a strict output limit."""
        return run("confluence_page", state, lambda: text_read("confluence_page", page_id, state))

    return [jira_issue, jira_search, confluence_search, confluence_page, prometheus_range_query,
            influx_query, get_pods, get_pod_metrics, get_k8s_events, get_test_status, get_test_results]


NT_TOOLS = build_tools()
