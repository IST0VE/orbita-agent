"""No executable commands or controlled actions are exposed in the historical MVP."""

from enum import StrEnum


class Risk(StrEnum):
    READ_ONLY = "READ_ONLY"
    CONTROLLED = "CONTROLLED"
    DANGEROUS = "DANGEROUS"


READ_OPERATIONS = frozenset({"jira_issue", "jira_search", "confluence_search", "confluence_page",
    "prometheus_query", "prometheus_range_query", "influx_query", "get_pods", "get_pod_metrics",
    "get_k8s_events", "describe_pod", "get_deployments", "get_replicas", "get_logs",
    "get_test_status", "get_test_results",
    # Чтение метаданных и выполнение проверенного запроса, составленного моделью:
    # оба только читают, и оба ограничены серверными настройками и guard'ом.
    "discover_metrics", "run_metric_query"})
CONTROL_OPERATIONS = frozenset({"prepare_test", "start_test", "stop_test", "change_load"})


def risk_of(operation: str) -> Risk:
    if operation in READ_OPERATIONS:
        return Risk.READ_ONLY
    if operation in CONTROL_OPERATIONS:
        return Risk.CONTROLLED
    return Risk.DANGEROUS


def allowed(operation: str, *, approved: bool = False, allow_control: bool = False) -> bool:
    risk = risk_of(operation)
    return risk == Risk.READ_ONLY or (risk == Risk.CONTROLLED and allow_control and approved)
