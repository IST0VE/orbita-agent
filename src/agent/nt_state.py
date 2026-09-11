"""NT state extends Orbita's reducers, cost tracking and publication state."""

from typing import Literal

from agent.state import State as CommonState


class State(CommonState, total=False):
    jira_key: str
    task_description: str
    environment: str
    namespace: str
    target_service: str
    target_url: str
    test_type: str
    target_rps: float | None
    duration_seconds: int | None
    ramp_up_seconds: int | None
    virtual_users: int | None
    scenario: str
    sla_p95_ms: float | None
    sla_p99_ms: float | None
    sla_error_rate: float | None
    sla_max_cpu: float | None
    sla_max_memory: float | None
    services: list[str]
    dependencies: list[dict]
    databases: list[str]
    queues: list[str]
    infrastructure_components: list[str]
    component_profiles: dict
    scope_explicit: bool
    test_id: str
    test_status: Literal["not_started", "precheck", "ready", "running", "failed", "stopped", "completed"]
    started_at: float | str
    finished_at: float | str
    elapsed_seconds: float
    baseline_start: float | str
    baseline_end: float | str
    baseline_metrics: dict
    current_metrics: dict
    metric_snapshots: list[dict]
    threshold_violations: list[dict]
    anomalies: list[dict]
    ranked_services: list[dict]
    investigation_history: list
    # Решение оператора по вызовам инструментов последнего ответа модели:
    # какие id разрешены, какие отклонены и с какой причиной.
    tool_approval: dict
    root_cause_hypotheses: list[dict]
    recommendations: list[str]
    requires_approval: bool
    approved: bool
    stop_reason: str
    iteration: int
    max_iterations: int
    global_timeout_seconds: int
    deadline_at: float
    run_id: str
    context_sources: list[dict]
    missing_parameters: list[str]
    source_errors: list[dict]
    precheck_result: dict
    baseline_comparison: dict
    previous_test_id: str
    previous_comparison: dict
    timeline: list[dict]
    correlations: list[dict]
    analysis_result: Literal["PASSED", "FAILED", "INCONCLUSIVE"]
    maximum_stable_rps: float | None
    evidence: dict
    llm_summary: dict
