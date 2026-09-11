"""PromQL, составленный моделью: что проходит проверку, а что не доходит до источника."""

import pytest

from agent.nt.query_guard import validate

NAMESPACE = "nt01"


@pytest.mark.parametrize("query", [
    'sum by (service) (rate(http_requests_total{namespace="nt01"}[5m]))',
    'sum(rate(http_requests_total{namespace="nt01"}[5m])) by (service)',
    'histogram_quantile(0.95, sum by (service, le) '
    '(rate(http_request_duration_seconds_bucket{namespace="nt01"}[5m])))',
    'sum by(service)(rate(a{namespace="nt01", code=~"5.."}[1m])) '
    '/ sum by(service)(rate(a{namespace="nt01"}[1m]))',
    'max by (service) (container_memory_working_set_bytes{namespace="nt01"}) / 1024',
    'sum by (service) (label_replace(kube_deployment_status_replicas_available{namespace="nt01"}, '
    '"service", "$1", "deployment", "(.*)"))',
])
def test_scoped_and_aggregated_queries_pass(query):
    assert validate(query, NAMESPACE) == ""


@pytest.mark.parametrize("query,reason", [
    ('sum by (service) (rate(http_requests_total[5m]))', "label selector"),
    ('sum by (service) (rate(a{namespace="nt01"}[1m])) / sum by (service) (rate(b{job="x"}[1m]))',
     'namespace="nt01"'),
    ('sum by (service) (rate(a{namespace="prod"}[1m]))', 'namespace="nt01"'),
    ('sum by (service) ({__name__=~".+", namespace="nt01"})', "__name__"),
    ('rate(a{namespace="nt01"}[1m])', "by (service)"),
    ('sum by (service) (rate(a{namespace="nt01"}[5m:1m]))', "subqueries"),
    ('sum by (service) (rate(a{namespace="nt01"}[1m] @ 1788220800))', "unsupported characters"),
    ('sum by (service) (rate(a{namespace="nt01"}[1m] offset 1h))', "label selector"),
    ('sum by (service) (rate(a{namespace="nt01"}[6h]))', "range must not exceed"),
    ('sum by (service) (absent(a{namespace="nt01"}))', "absent is not allowed"),
    ('sum by (service) (rate(a{namespace="nt01"}[1m])) or vector(1)', "vector is not allowed"),
    ("", "empty"),
    ('sum by (service) (rate(a{namespace="nt01"}[1m])) # ' + "x" * 600, "characters"),
])
def test_unscoped_expensive_or_unknown_queries_are_refused(query, reason):
    assert reason in validate(query, NAMESPACE)


def test_range_limit_is_configurable():
    query = 'sum by (service) (rate(a{namespace="nt01"}[30m]))'
    assert validate(query, NAMESPACE) != ""
    assert validate(query, NAMESPACE, max_range_seconds=3600) == ""


def test_length_limit_counts_the_query_itself_not_the_padding():
    query = 'sum by (service) (rate(a{namespace="nt01"}[1m]))'
    assert validate(query + " " * 800, NAMESPACE) == ""
    assert "exceeds" in validate(query + " + " + " + ".join([query] * 20), NAMESPACE)
