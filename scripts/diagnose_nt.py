"""Read-only NT diagnostics; run from the same terminal as the backend."""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.integrations.http import AdapterError, HTTPClient  # noqa: E402
from agent.integrations.load_testing import HTTPLoadTesting  # noqa: E402
from agent.nt.collection import Sources  # noqa: E402
from agent.nt.context import INPUT_FIELDS  # noqa: E402
from agent.nt.metrics_analyzer import coverage_gaps  # noqa: E402
from agent.nt.models import window  # noqa: E402
from agent.nt.settings import load_settings  # noqa: E402

SAFE_FIELDS = INPUT_FIELDS | {
    "id", "status", "state", "service", "start", "end", "start_time", "end_time",
    "p95", "p99", "p95_ms", "p99_ms", "error_rate", "cpu", "memory", "metric", "expression",
}


def metadata_shape(value, key="", depth=0):
    """Keep metadata values and container shape, without arbitrary response text."""
    if depth > 6:
        return "<nested>"
    if any(part in key.lower() for part in ("token", "password", "secret", "authorization")):
        return "<redacted>"
    if isinstance(value, dict):
        return {k: metadata_shape(v, k, depth + 1) for k, v in list(value.items())[:100]}
    if isinstance(value, list):
        return {"count": len(value), "sample": [metadata_shape(v, key, depth + 1) for v in value[:3]]}
    if key in SAFE_FIELDS:
        return value[:300] if isinstance(value, str) else value
    return f"<{type(value).__name__}>"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-id")
    parser.add_argument("--check-metrics", action="store_true",
                        help="check normalized test data and configured historical metric queries")
    parser.add_argument("--details", action="store_true",
                        help="read actual SLA expressions and HTTP counter label names/values")
    parser.add_argument("--model-input", action="store_true",
                        help="estimate the model input of one investigation call; reads no test data")
    parser.add_argument("--window", type=int, default=0,
                        help="context window of the model in tokens; with --model-input suggests limits")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    if args.model_input:
        return model_input(args.window)
    import re
    if not args.test_id:
        parser.error("--test-id is required unless --model-input is given")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", args.test_id):
        parser.error("invalid test ID")
    if args.details:
        return inspect_contract(args.test_id)
    if args.check_metrics:
        return check_metrics(args.test_id)
    try:
        print(json.dumps({"configured_metrics": list(load_settings().queries)}, ensure_ascii=False))
    except (ValueError, TypeError, KeyError):
        print('{"settings_error": "invalid NT settings"}')
    probes = [
        ("test_metadata", "NT_LOAD_TESTING", f"/tests/{args.test_id}"),
        ("test_results", "NT_LOAD_TESTING", f"/tests/{args.test_id}/results"),
        ("metric_names", "NT_PROMETHEUS", "/api/v1/label/__name__/values"),
    ]
    for name, prefix, path in probes:
        url = os.getenv(prefix + "_URL")
        result = {"source": name, "path": path}
        try:
            if not url:
                raise AdapterError("NOT_CONFIGURED")
            client = HTTPClient(url, os.getenv(prefix + "_TOKEN", ""), timeout=5,
                                retries=0, max_bytes=1_000_000)
            data = client.json("GET", path)
            result["data"] = data if name == "metric_names" else metadata_shape(data)
        except (AdapterError, ValueError) as exc:
            result["error"] = str(exc) if isinstance(exc, AdapterError) else "INVALID_URL"
        print(json.dumps(result, ensure_ascii=False), flush=True)


def inspect_contract(test_id):
    """Fetch the exact missing contract details, without credentials or raw metrics."""
    try:
        gateway = HTTPLoadTesting(os.getenv("NT_LOAD_TESTING_URL", ""),
                                  os.getenv("NT_LOAD_TESTING_TOKEN", ""))
        raw = gateway.http.json("GET", f"/tests/{test_id}/results")
        items = raw.get("thresholds", [])
        print(json.dumps({"thresholds": [
            {k: item.get(k) for k in ("metric", "expression")} for item in items[:64]
            if isinstance(item, dict)
        ]}, ensure_ascii=False), flush=True)
        metadata = gateway.get_test_status(test_id)
        if not metadata["success"]:
            print(json.dumps(metadata, ensure_ascii=False), flush=True)
            return 1
        state = metadata["data"]
        start, end = window(state.get("baseline_start", state["started_at"]), state["finished_at"])
        selector = "http_requests_total{" + ",".join(
            f"{label}={json.dumps(state[key])}" for label, key in
            (("namespace", "namespace"), ("service", "target_service"))
        ) + "}"
        client = HTTPClient(os.getenv("NT_PROMETHEUS_URL", ""),
                            os.getenv("NT_PROMETHEUS_TOKEN", ""), max_bytes=1_000_000)
        response = client.json("GET", "/api/v1/series", params={
            "match[]": selector, "start": start, "end": end,
        })
        if response.get("status") != "success":
            print('{"error": "Prometheus series query failed"}')
            return 1
        rows = response["data"]
        labels = sorted({key for row in rows for key in row})
        statuses = {label: sorted({row[label] for row in rows if label in row})[:100]
                    for label in ("status", "code", "status_code", "http_status_code", "response_code")
                    if label in labels}
        print(json.dumps({"http_counter": "http_requests_total", "series_count": len(rows),
                          "label_names": labels, "status_labels": statuses}, ensure_ascii=False), flush=True)
        return 0
    except (AdapterError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc) if isinstance(exc, AdapterError)
                          else "invalid metadata or series response"}), flush=True)
        return 1


def model_input(window_tokens):
    """
    Что уходит в модель и помещается ли это в её окно.

    Провайдеры отказывают по-разному, а иногда молча обрезают; сравнивать размер
    запроса с окном приходится руками. Постоянная часть вызова считается по
    самому коду — промпт роли и схемы включённых сейчас инструментов, — поэтому
    NT_GENERATED_QUERIES и прочие настройки уже учтены. Счёт приближённый: тот
    же, которым подрезается история.
    """
    from langchain_core.messages import HumanMessage
    from langchain_core.messages.utils import count_tokens_approximately
    from langchain_core.utils.function_calling import convert_to_openai_tool

    from agent import nt_prompts
    from agent.nt_tools import build_tools

    def tokens(text):
        return count_tokens_approximately([HumanMessage(text)])

    settings = load_settings()
    schemas = json.dumps([convert_to_openai_tool(t) for t in build_tools(settings=settings)],
                         ensure_ascii=False)
    fixed = tokens(nt_prompts.prompt_for("investigate")) + tokens(schemas)
    history_limit = int(os.getenv("LLM_MAX_HISTORY_TOKENS", "0") or 0)
    growth = settings.max_iterations * 3 * settings.tool_result_tokens
    worst = fixed + (history_limit if history_limit else settings.brief_tokens + growth)
    report = {"fixed_tokens": fixed, "first_call_tokens": fixed + settings.brief_tokens,
              "worst_call_tokens": worst, "settings": {
                  "NT_BRIEF_TOKENS": settings.brief_tokens,
                  "NT_TOOL_RESULT_TOKENS": settings.tool_result_tokens,
                  "LLM_MAX_HISTORY_TOKENS": history_limit}}
    if window_tokens:
        # Запас на служебные поля запроса: имена ролей, идентификаторы вызовов.
        available = window_tokens - fixed - 200
        report["window"] = window_tokens
        report["fits"] = worst <= window_tokens
        if available <= 0:
            report["suggest"] = "окно меньше постоянной части вызова: нужна модель с большим окном"
        elif not report["fits"]:
            report["suggest"] = {"NT_BRIEF_TOKENS": int(available * .6),
                                 "NT_TOOL_RESULT_TOKENS": max(100, int(available * .12)),
                                 "LLM_MAX_HISTORY_TOKENS": available}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("fits", True) else 1


def check_metrics(test_id):
    """No model calls or publications: validate actual historical reads only."""
    try:
        settings = load_settings()
        gateway = HTTPLoadTesting(os.getenv("NT_LOAD_TESTING_URL", ""),
                                  os.getenv("NT_LOAD_TESTING_TOKEN", ""))
        result = gateway.get_test_results(test_id)
        print(json.dumps({"normalized_test": result}, ensure_ascii=False), flush=True)
        if not result["success"]:
            return 1
        state = result["data"]
        missing = [k for k in ("target_service", "environment", "namespace", "started_at", "finished_at")
                   if state.get(k) is None]
        if missing or not settings.queries:
            print(json.dumps({"missing_fields": missing, "configured_metrics": list(settings.queries)}))
            return 1
        start, end = window(state["started_at"], state["finished_at"], max_seconds=settings.max_window)
        base_start, base_end = window(state.get("baseline_start", start - settings.baseline_seconds),
                                      state.get("baseline_end", start), max_seconds=settings.max_window)
        sources = Sources.from_env(settings)
        failed = bool(result.get("missing_parameters"))
        for period, left, right in (("baseline", base_start, base_end), ("test", start, end)):
            metrics = sources.collect(state, left, right)
            gaps = coverage_gaps(metrics["series"], state["target_service"], list(settings.queries),
                                 left, right, settings.step)
            counts = {s["metric"]: len(s["values"]) for s in metrics["series"]
                      if s["service"] == state["target_service"]}
            print(json.dumps({"period": period, "target_service": state["target_service"],
                              "samples": counts, "gaps": gaps, "errors": metrics["errors"]},
                             ensure_ascii=False), flush=True)
            failed = failed or bool(gaps or metrics["errors"])
        return int(failed)
    except (ValueError, TypeError, KeyError):
        print('{"error": "invalid NT configuration or test period"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
