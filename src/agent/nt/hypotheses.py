"""Validate diagnostic observations independently of model wording/confidence.

These rules support investigation directions; they never certify causality.
Context text, live Kubernetes and unverified composed-query units cannot prove
historical resource saturation. Model explanations remain explicitly unverified.
"""

from agent import confluence

MECHANISMS = {
    "cpu_saturation": {"cpu", "cpu_throttling"},
    "memory_pressure": {"memory"},
    "connection_pool": {"connection_utilization", "waiting_connections"},
    "queue_backlog": {"consumer_lag", "waiting_connections", "locks"},
    "latency_regression": {"p95", "p99", "latency", "query_latency"},
    "error_increase": {"error_rate", "errors", "deadlocks"},
    "restarts": {"pod_restarts"},
    "unknown": set(),
}


def _historical_observations(item: dict, service: str) -> list[dict]:
    if item.get("historical") is False or item.get("origin") == "model_query":
        return []
    if item.get("service") == service and item.get("metric"):
        return [item]
    metrics = item.get("metrics", {})
    if isinstance(metrics, dict) and isinstance(metrics.get(service), dict):
        return [{"metric": m, **s} for m, s in metrics[service].items() if isinstance(s, dict)]
    return []


def _supports(observation: dict, mechanism: str) -> bool:
    metric = observation.get("metric")
    if metric not in MECHANISMS.get(mechanism, set()):
        return False
    if observation.get("partial") or observation.get("invalid_points"):
        return False
    limit = {"cpu": .9, "cpu_throttling": .2, "memory": .9, "connection_utilization": .85}.get(metric)
    if limit is not None:
        # A rise from 10% to 20% CPU is a spike, but is not saturation.
        return max(observation.get("max", -1), observation.get("peak", -1)) >= limit
    if observation.get("kind") in {"saturation", "counter_increase", "trend", "spike", "baseline"}:
        return True
    # Threshold violations are computed from samples, not model assertions.
    if "first_at" in observation and "limit" in observation and "peak" in observation:
        return True
    return False


def validate(data: object, state: dict) -> tuple[list[dict], list[str], dict]:
    accepted, rejected = [], []
    if not isinstance(data, dict) or not isinstance(data.get("hypotheses", []), list):
        return [], [], {"status": "INVALID_RESPONSE", "rejected": ["ожидался объект с hypotheses"]}
    evidence = state.get("evidence", {})
    for index, item in enumerate(data.get("hypotheses", [])[:5]):
        reason = ""
        if not isinstance(item, dict):
            rejected.append({"index": index, "reason": "hypothesis must be an object"})
            continue
        service, mechanism = item.get("service"), item.get("mechanism")
        refs, counters = item.get("evidence_ids"), item.get("counter_evidence_ids", [])
        if not isinstance(service, str) or service not in state.get("services", []):
            reason = "service is outside historical scope"
        elif not isinstance(mechanism, str) or mechanism not in MECHANISMS:
            reason = "mechanism is missing or unsupported"
        elif not all(isinstance(item.get(k), str) and item[k].strip() for k in ("description", "next_check")):
            reason = "description and next_check are required"
        elif (not isinstance(refs, list) or not refs or len(refs) > 10
              or not isinstance(counters, list) or len(counters) > 10
              or any(not isinstance(r, str) or r not in evidence for r in refs + counters)):
            reason = "invalid evidence references"
        if reason:
            rejected.append({"index": index, "reason": reason})
            continue
        observations = [(ref, observation) for ref in refs
                        for observation in _historical_observations(evidence[ref], service)]
        supported = [(ref, observation) for ref, observation in observations if _supports(observation, mechanism)]
        if mechanism != "unknown" and not supported:
            rejected.append({"index": index, "reason": "no matching historical observation supports this mechanism"})
            continue
        accepted.append({"service": service, "mechanism": mechanism,
            "confidence": "possible" if supported and not counters else "unknown",
            "description": confluence.mask_text(item["description"][:1500]),
            "next_check": confluence.mask_text(item["next_check"][:1000]),
            "evidence_ids": refs, "counter_evidence_ids": counters,
            "observations": [{"evidence_id": ref, "metric": o["metric"],
                              **{k: o[k] for k in ("max", "peak", "limit", "first_at", "kind") if k in o}}
                             for ref, o in supported][:10],
            "validation": "observations_supported" if supported else "unverified",
            "causality": "not_established"})
    recommendations = data.get("recommendations", [])
    recommendations = [confluence.mask_text(r[:1000]) for r in recommendations[:10] if isinstance(r, str)] if isinstance(recommendations, list) else []
    return accepted, recommendations, {"status": "HYPOTHESES" if accepted else "UNKNOWN",
        "accepted": len(accepted), "rejected": rejected,
        "note": "проверены наблюдения; причинная связь требует независимой проверки"}
