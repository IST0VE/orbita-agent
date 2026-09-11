"""Keep operator inputs separate from resolved metadata and derived run state."""

from agent.nt.context import INPUT_FIELDS, explicit_fields, validate_fields
from agent.nt.thresholds import SLA_FIELDS


def request_inputs(state: dict, options: dict) -> tuple[dict, list[str]]:
    """Merge a follow-up without promoting last run's inferred values to inputs.

    Direct SDK state changes are detected against the last resolved snapshot.
    Message corrections override saved inputs; configurable is explicitly highest
    priority. A new test ID starts with fresh test-specific inputs.
    """
    snapshot = state.get("resolved_inputs")
    direct = {k: state[k] for k in INPUT_FIELDS if k in state
              and (snapshot is None or state[k] != snapshot.get(k))}
    messages = state.get("messages") or []
    fresh_message = bool(messages and getattr(messages[-1], "type", "") == "human")
    task = state.get("task", "")
    task_changed = snapshot is None or task != snapshot.get("_task")
    incoming = {**direct, **explicit_fields(task if fresh_message or task_changed else ""),
                **{k: v for k, v in options.items() if k in INPUT_FIELDS}}
    saved = dict(state.get("requested_inputs") or {})
    previous_id = (snapshot or {}).get("test_id")
    if incoming.get("test_id") and previous_id and incoming["test_id"] != previous_id:
        saved = {}
    operators = dict(saved.get("sla_comparators") or {})
    for field, metric in SLA_FIELDS.items():
        if field in incoming:
            operators.pop(metric, None)
    supplied_operators = incoming.get("sla_comparators", {})
    if isinstance(supplied_operators, dict):
        incoming["sla_comparators"] = {**operators, **supplied_operators}
    merged = {**saved, **incoming}
    clean, errors = validate_fields(merged)
    return clean, errors


def snapshot(state: dict, update: dict) -> dict:
    resolved = {**state, **update}
    return {**{key: resolved.get(key) for key in INPUT_FIELDS}, "_task": resolved.get("task", "")}


def question_for(state: dict, fields: dict) -> str:
    """Keep the original investigation question alongside same-test corrections."""
    previous = state.get("resolved_inputs") or {}
    old_question, task = state.get("analysis_question", ""), state.get("task", "")
    new_test = bool(previous.get("test_id") and fields.get("test_id")
                    and previous["test_id"] != fields["test_id"])
    if not old_question or new_test:
        question = task
    elif task != previous.get("_task"):
        question = old_question + "\nУточнение оператора:\n" + task
    else:
        question = old_question
    return question if len(question) <= 6000 else question[:3000] + "\n…\n" + question[-2800:]
