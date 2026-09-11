"""Hydrate a test ID from the gateway without trusting conflicting identities."""

from agent.nt.context import INPUT_FIELDS, validate_fields
from agent.nt.models import failure, timestamp
from agent.nt.thresholds import SLA_FIELDS


def hydrate(gateway, fields: dict) -> tuple[dict, list, list, dict | None]:
    if gateway is None or not fields.get("test_id"):
        return fields, [], [], None
    try:
        result = gateway.get_test_results(fields["test_id"])
    except Exception:
        result = failure("LOAD_TESTING_UNAVAILABLE", "test metadata unavailable")
    if not isinstance(result, dict):
        return fields, [], [failure("INVALID_TEST_METADATA", "invalid gateway response")], None
    if not result.get("success"):
        return fields, [], [result], None
    data = result.get("data")
    if not isinstance(data, dict):
        return fields, [], [failure("INVALID_TEST_METADATA", "invalid metadata object")], None
    clean, invalid = validate_fields(data)
    missing = [*result.get("missing_parameters", []), *invalid]
    errors = list(result.get("errors", []))
    if clean.get("test_id", fields["test_id"]) != fields["test_id"]:
        return fields, [*missing, "gateway вернул другой test_id"], errors, None
    merged = dict(fields)
    for field, value in clean.items():
        if field in fields and field in {"started_at", "finished_at", "environment", "namespace", "target_service"}:
            same = (timestamp(fields[field]) == timestamp(value)) if field.endswith("_at") else fields[field] == value
            if not same:
                missing.append(f"параметр теста противоречит gateway: {field}")
        if field in INPUT_FIELDS:
            merged.setdefault(field, value)
    if clean.get("test_status") is not None:
        merged["test_status"] = clean["test_status"]
    # An explicitly replaced numeric SLA defaults to an inclusive upper limit;
    # gateway comparators belong only to the limits supplied by the gateway.
    operators = {metric: op for metric, op in clean.get("sla_comparators", {}).items()
                 if next((field for field, m in SLA_FIELDS.items() if m == metric), "") not in fields}
    merged["sla_comparators"] = {**operators, **fields.get("sla_comparators", {})}
    return merged, missing, errors, clean
