"""Historical NT analysis: explicit workflow, deterministic facts, bounded LLM research.

START -> context -> load_context -> understand_task -> discover_scope -> precheck
  -> collect_baseline -> collect_metrics -> detect_anomalies -> compare_baseline
  -> evaluate_test -> investigate <-> additional_tools -> final_analysis
  -> report -> remember -> approve -> publish -> END

Missing inputs go directly to report. This MVP never starts or stops a test.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import replace
from functools import partial
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from agent import confluence, jira, nodes, nt_roles
from agent.nt import (
    anomaly_detector,
    baseline,
    metrics_analyzer,
    query_guard,
    ranking,
    thresholds,
)
from agent.nt.collection import Sources
from agent.nt.context import (
    INPUT_FIELDS,
    NUMBER_FIELDS,
    explicit_fields,
    validate_fields,
    validated_extraction,
)
from agent.nt.metric_profiles import PROFILES
from agent.nt.models import failure, timestamp, window
from agent.nt.report import render_report
from agent.nt.settings import Settings, load_settings
from agent.nt_state import State
from agent.nt_tools import build_tools
from agent.routes import budget_gate
from agent.runtime import options

PIPELINE = nt_roles.PIPELINE
log = logging.getLogger(__name__)


def _json(data) -> str:
    return confluence.mask_text(json.dumps(data, ensure_ascii=False, allow_nan=False))


def _error(state, kind, message):
    return [*state.get("source_errors", []), failure(kind, message)]


# Инструмент, текст которого составила модель, а не администратор сервера.
# Такой вызов показывается оператору до выполнения.
COMPOSED_TOOLS = frozenset({"run_metric_query"})


def pending_calls(state: State) -> list[dict]:
    """Вызовы инструментов в последнем ответе модели."""
    history = state.get("investigation_history", [])
    last = history[-1] if history else None
    return list(getattr(last, "tool_calls", None) or [])


def investigation_route(state: State) -> str:
    return "approve_tools" if pending_calls(state) else "final_analysis"


def build_graph(llm: Any = None, *, sources: Sources | None = None,
                settings: Settings | None = None) -> StateGraph:
    def settings_for():
        return settings or (sources.settings if sources else load_settings())

    def sources_for():
        return sources or Sources.from_env(settings_for())

    toolset = build_tools(sources, settings=settings)
    research_pipeline = replace(PIPELINE, tools=tuple(toolset), roles=(nt_roles.INVESTIGATE,))
    understand_pipeline = replace(PIPELINE, roles=(nt_roles.UNDERSTAND,))
    understand_role = nodes.make_role_node(nt_roles.UNDERSTAND, llm=llm, pipeline=understand_pipeline)
    investigate_role = nodes.make_role_node(nt_roles.INVESTIGATE, llm=llm, pipeline=research_pipeline)
    tool_node = ToolNode(toolset, messages_key="investigation_history",
                         handle_tool_errors=lambda exc: _json(failure("TOOL_ERROR", "invalid tool request")))

    def load_context(state: State, config: RunnableConfig) -> dict:
        task = state.get("task", "")
        update = {"run_id": str(uuid.uuid4()), "iteration": 0, "investigation_history": [],
            "root_cause_hypotheses": [], "recommendations": [], "missing_parameters": [],
            "source_errors": [], "context_sources": [], "metric_snapshots": [], "baseline_metrics": {},
            "current_metrics": {}, "anomalies": [], "threshold_violations": [], "ranked_services": [],
            "timeline": [], "evidence": {}, "baseline_comparison": {}, "previous_comparison": {},
            "llm_summary": {}, "correlations": [], "tool_approval": {},
            "maximum_stable_rps": None, "analysis_result": "INCONCLUSIVE", "precheck_result": {},
            "artifacts": {"report": "", "investigate": "", "understand_task": ""},
            "stage": "load_context"}
        explicit = {**explicit_fields(task), **{k: state[k] for k in INPUT_FIELDS if k in state},
                    **{k: v for k, v in options(config).items() if k in INPUT_FIELDS}}
        explicit, input_errors = validate_fields(explicit)
        # Explicitly clear malformed values inherited from the unvalidated input state.
        update.update({k: None for k in INPUT_FIELDS if k in state and k not in explicit})
        update["missing_parameters"].extend(input_errors)
        try:
            limits = settings_for()
            update.update(max_iterations=limits.max_iterations, global_timeout_seconds=limits.timeout_seconds,
                          deadline_at=time.time() + limits.timeout_seconds)
            backend = sources_for()
        except (ValueError, TypeError, KeyError):
            update.update(explicit)
            update["source_errors"] = [failure("CONFIGURATION_ERROR", "invalid NT server settings")]
            update["missing_parameters"] = ["исправьте настройки NT на сервере"]
            return update

        keys = jira.find_keys(task)
        key = explicit.get("jira_key") or (keys[0] if keys else "")
        contexts = [{"kind": "input", "id": "operator", "text": confluence.mask_text(task[:12000])}]
        if key:
            explicit["jira_key"] = key
            try:
                issue = jira.fetch_issue(key)
                text = confluence.mask_text(jira.format_issue(issue)[:12000])
                contexts.append({"kind": "jira", "id": key, "url": issue["url"], "text": text})
                # Follow a bounded number of linked tasks, without recursive discovery.
                for link in issue.get("links", [])[:2]:
                    try:
                        linked = jira.fetch_issue(link["key"])
                        contexts.append({"kind": "jira", "id": link["key"], "url": linked["url"],
                                         "text": confluence.mask_text(jira.format_issue(linked)[:6000])})
                    except Exception:
                        update["source_errors"].append(failure("JIRA_LINK_UNAVAILABLE", "linked issue unavailable"))
            except Exception:
                update["source_errors"].append(failure("JIRA_UNAVAILABLE", "Jira issue could not be read"))

        combined = "\n".join(c["text"] for c in contexts)
        pages = confluence.find_page_ids(combined)[:2]
        if confluence.is_configured():
            try:
                if not pages:
                    query = explicit.get("target_service") or key
                    if query:
                        pages = [str(p["id"]) for p in confluence.search(str(query))[:2]]
                for page_id in pages:
                    page = confluence.fetch_page(page_id)
                    contexts.append({"kind": "confluence", "id": page_id, "url": page.get("url"),
                                     "text": confluence.mask_text(confluence.format_page(page)[:8000])})
            except Exception:
                update["source_errors"].append(failure("CONFLUENCE_UNAVAILABLE", "documentation unavailable"))

        extracted = {}
        for context in contexts[1:]:
            for field, value in explicit_fields(context["text"]).items():
                if field in extracted and extracted[field] != value and field not in explicit:
                    update["missing_parameters"].append(f"конфликт источников: {field}; задайте явно")
                else:
                    extracted[field] = value
        extracted.update(explicit)
        extracted, input_errors = validate_fields(extracted)
        update["missing_parameters"].extend(input_errors)
        if backend.load_testing and extracted.get("test_id"):
            try:
                result = backend.load_testing.get_test_results(extracted["test_id"])
            except Exception:
                result = failure("LOAD_TESTING_UNAVAILABLE", "test metadata unavailable")
            if result.get("success"):
                update["missing_parameters"].extend(result.get("missing_parameters", []))
                update["source_errors"].extend(result.get("errors", []))
                data = result["data"]
                if data.get("test_id", extracted["test_id"]) != extracted["test_id"]:
                    update["missing_parameters"].append("gateway вернул другой test_id")
                else:
                    for field, value in data.items():
                        if field in INPUT_FIELDS:
                            if field in extracted and field in {"started_at", "finished_at"}:
                                try:
                                    same = timestamp(extracted[field]) == timestamp(value)
                                except ValueError:
                                    same = False
                                if not same:
                                    update["missing_parameters"].append(f"период теста противоречит gateway: {field}")
                            extracted.setdefault(field, value)
                    # The gateway status is authoritative, including when state
                    # still contains a default from an earlier failed precheck.
                    if data.get("test_status") is not None:
                        extracted["test_status"] = data["test_status"]
                    contexts.append({"kind": "load_testing", "id": extracted["test_id"], "text": _json(data)})
            else:
                update["source_errors"].append(result)
        extracted, input_errors = validate_fields(extracted)
        update["missing_parameters"].extend(input_errors)
        update.update(extracted)
        update["context_sources"] = contexts
        update["task_description"] = "\n\n".join(c["text"] for c in contexts)[:40000]
        return update

    def understand_task(state: State, config: RunnableConfig) -> dict:
        essential = {"target_service", "environment", "namespace", "test_id", "started_at", "finished_at"}
        if (all(state.get(k) is not None for k in essential) or not state.get("deadline_at")
                or not settings_for().queries
                or budget_gate(state) == "over_budget" or time.time() >= state["deadline_at"]):
            return {"stage": "understand_task"}
        try:
            result = understand_role({**state, "task": state.get("task_description", "")}, config)
            response = result["messages"][-1]
            values = validated_extraction(nodes.text_of(response), state.get("task_description", ""))
            return {**{k: v for k, v in values.items() if not state.get(k)},
                    "usage": result["usage"], "cost": result["cost"], "stage": "understand_task"}
        except Exception:
            return {"stage": "understand_task", "source_errors": _error(state, "LLM_UNAVAILABLE",
                                                                           "context extraction unavailable")}

    def discover_scope(state: State) -> dict:
        services = state.get("services") or []
        dependencies = state.get("dependencies") or []
        profiles = state.get("component_profiles") or {}
        missing = list(state.get("missing_parameters", []))
        if (not isinstance(services, list) or not all(isinstance(s, str) and s for s in services)
                or not isinstance(dependencies, list) or not all(isinstance(e, dict)
                    and isinstance(e.get("from"), str) and isinstance(e.get("to"), str) for e in dependencies)
                or not isinstance(profiles, dict) or any(p not in PROFILES for p in profiles.values())):
            return {"services": [], "dependencies": [], "component_profiles": {},
                    "missing_parameters": [*missing, "неверный scope или metric profile"], "stage": "discover_scope"}
        explicit_scope = bool(services or dependencies)
        names = set(services)
        for edge in dependencies:
            names.update((edge["from"], edge["to"]))
        for field in ("databases", "queues", "infrastructure_components"):
            for name in state.get(field) or []:
                if isinstance(name, str):
                    names.add(name)
        if isinstance(state.get("target_service"), str):
            names.add(state["target_service"])
        if not explicit_scope and state.get("namespace"):
            try:
                backend = sources_for().kubernetes
                if backend:
                    result = backend.get_deployments(state["namespace"])
                    if result["success"]:
                        names.update(r["service"] for r in result["data"])
            except Exception:
                pass  # Historical telemetry remains the namespace discovery fallback.
        if len(names) > 500 or len(dependencies) > 500:
            missing.append("scope превышает лимит 500 компонентов/связей")
        return {"services": sorted(names)[:500], "scope_explicit": explicit_scope,
                "dependencies": dependencies[:500], "component_profiles": profiles,
                "missing_parameters": missing, "stage": "discover_scope"}

    def precheck(state: State) -> dict:
        missing = list(state.get("missing_parameters", []))
        checks, update = [], {}
        for key in ("target_service", "environment", "namespace", "test_id", "started_at", "finished_at"):
            if state.get(key) is None or state.get(key) == "":
                missing.append(f"не указан {key}")
        try:
            settings = settings_for()
            if not settings.queries:
                missing.append("не настроен NT_METRIC_QUERIES")
        except (ValueError, TypeError, KeyError):
            settings = None
            missing.append("неверные настройки NT на сервере")
        try:
            if not thresholds.limits_from(state):
                missing.append("не указаны SLA с явными единицами")
            for field in NUMBER_FIELDS:
                if state.get(field) is not None and (isinstance(state[field], bool)
                    or not isinstance(state[field], (int, float)) or not 0 <= state[field] < float("inf")):
                    raise ValueError("invalid numeric parameter")
        except (ValueError, TypeError, OverflowError):
            missing.append("неверные SLA или числовые параметры НТ")
        if state.get("test_status") == "running":
            missing.append("MVP 1 анализирует только завершённые тесты")
        try:
            # These checks are independent: missing dates must not hide an empty
            # metric query map or missing SLA from the operator.
            if settings is None:
                raise ValueError("invalid server settings")
            start, end = window(state.get("started_at"), state.get("finished_at"), max_seconds=settings.max_window)
            if end > time.time():
                raise ValueError("test period is in the future")
            base_start = state.get("baseline_start")
            base_end = state.get("baseline_end")
            base_start = start - settings.baseline_seconds if base_start is None else base_start
            base_end = start if base_end is None else base_end
            base_start, base_end = window(base_start, base_end, max_seconds=settings.max_window)
            if base_end > start:
                raise ValueError("baseline overlaps the test")
            update.update(started_at=start, finished_at=end, baseline_start=base_start, baseline_end=base_end,
                          elapsed_seconds=end - start)
            checks.append({"name": "historical_window", "success": True})
        except (ValueError, TypeError, OverflowError):
            if settings is not None and all(state.get(k) is not None for k in ("started_at", "finished_at")):
                missing.append("неверный период теста или baseline")
            checks.append({"name": "historical_window", "success": False})
        checks.append({"name": "required_parameters", "success": not missing, "missing": missing})
        return {**update, "precheck_result": {"success": not missing, "checks": checks},
                "missing_parameters": list(dict.fromkeys(missing)), "stage": "precheck",
                "test_status": state.get("test_status") or ("completed" if not missing else "not_started")}

    def collect_baseline(state: State) -> dict:
        result = sources_for().collect(state, state["baseline_start"], state["baseline_end"])
        return {"baseline_metrics": baseline.summarize(result["series"]),
                "source_errors": [*state.get("source_errors", []),
                                  *[{**e, "period": "baseline"} for e in result["errors"]]],
                "stage": "collect_baseline"}

    def collect_metrics(state: State) -> dict:
        result = sources_for().collect(state, state["started_at"], state["finished_at"])
        return {"metric_snapshots": result["series"], "current_metrics": baseline.summarize(result["series"]),
                "services": sorted(set(state["services"]) | {s["service"] for s in result["series"]}),
                "source_errors": [*state.get("source_errors", []),
                                  *[{**e, "period": "test"} for e in result["errors"]]],
                "stage": "collect_metrics"}

    def detect_anomalies(state: State) -> dict:
        series = state["metric_snapshots"]
        violations = thresholds.violations(series, {state["target_service"]: thresholds.limits_from(state)})
        anomalies = anomaly_detector.detect(series, state["baseline_metrics"])
        ranked = ranking.rank_services(state["services"], anomalies, violations,
                                       weights=settings_for().anomaly_weights or None)
        timeline = [{"at": state["started_at"], "event": "test period started"},
                    {"at": state["finished_at"], "event": "test period ended"}]
        timeline.extend({"at": v["first_at"], "event": v.get("kind", "sla_violation"),
                         "service": v["service"], "metric": v["metric"]} for v in violations + anomalies)
        return {"threshold_violations": violations, "anomalies": anomalies, "ranked_services": ranked,
                "correlations": anomaly_detector.correlations(series),
                "baseline_comparison": baseline.compare(state["current_metrics"], state["baseline_metrics"]),
                "timeline": sorted(timeline, key=lambda e: (e["at"], e["event"])), "stage": "detect_anomalies"}

    def evaluate_test(state: State) -> dict:
        limits = thresholds.limits_from(state)
        missing = list(state.get("missing_parameters", []))
        settings = settings_for()
        missing.extend(metrics_analyzer.coverage_gaps(state["metric_snapshots"], state["target_service"],
            list(limits), state["started_at"], state["finished_at"], settings.step))
        if not state.get("baseline_metrics"):
            missing.append("baseline не получен")
        missing.extend(metrics_analyzer.baseline_gaps(state.get("baseline_metrics", {}), state["target_service"],
            list(limits), state["baseline_start"], state["baseline_end"], settings.step))
        unseen = sorted(set(state["services"]) - state["current_metrics"].keys())
        if unseen:
            missing.append("нет метрик компонентов scope: " + ", ".join(unseen[:20]))
        verdict = thresholds.verdict(state["threshold_violations"], missing,
            completed=state.get("test_status") == "completed", has_sla=bool(limits))
        update = {"analysis_result": verdict, "missing_parameters": list(dict.fromkeys(missing)),
            "maximum_stable_rps": metrics_analyzer.stable_load(state["metric_snapshots"],
                state["target_service"], limits, settings.step, settings.stable_seconds), "stage": "evaluate_test"}
        # Устойчивую нагрузку считает этот узел, он же сравнивает её с прошлым
        # прогоном — до того, как бриф уйдёт модели.
        previous = dict(state.get("previous_comparison") or {})
        if previous.get("previous_stable_rps") is not None and update["maximum_stable_rps"] is not None:
            previous["stable_rps"] = baseline.deviation(update["maximum_stable_rps"],
                                                        previous.pop("previous_stable_rps"))
            update["previous_comparison"] = previous
        evidence, summary = metrics_analyzer.make_evidence({**state, **update}, settings.top_n)
        # Bound the serialized input independently of datapoint and service limits.
        # Улики — то, на что обязана ссылаться гипотеза, и пустой их список
        # выключает исследование целиком. Поэтому первым уходит контекст.
        for field in ("timeline", "correlations", "previous_comparison", "baseline_comparison",
                      "dependencies"):
            if len(_json(summary)) <= 48000:
                break
            summary[field] = "omitted: LLM context capped"
        while len(_json(summary)) > 48000 and len(evidence) > 1:
            evidence.pop(next(reversed(evidence)))
            summary["evidence"] = evidence
        if len(_json(summary)) > 48000:
            summary = {"task": summary["task"], "result": verdict, "evidence": evidence,
                       "limitations": ["LLM context capped; see deterministic report"]}
        # Этот узел — последний потребитель сырых точек: дальше идут агрегаты,
        # улики и отчёт. Мегабайты рядов после вердикта только переливаются
        # в каждый кадр потока состояния и в каждый чекпоинт.
        return {**update, "evidence": evidence, "llm_summary": summary, "metric_snapshots": []}

    def settled(history):
        """Закрыть цикл, не забывая ход исследования.

        Маршрут выбирается по висячим вызовам в последнем ответе: снимаем их —
        и цикл завершается. Стирать историю нельзя, улики уже оплачены.
        """
        if history and getattr(history[-1], "tool_calls", None):
            return [*history[:-1], nodes.without_tool_calls(history[-1])]
        return list(history)

    def investigate(state: State, config: RunnableConfig) -> dict:
        history = state.get("investigation_history") or []
        if (state.get("iteration", 0) >= state.get("max_iterations", 4)
                or time.time() >= state.get("deadline_at", 0) or budget_gate(state) == "over_budget"
                or not state.get("evidence")):
            return {"stage": "investigate", "investigation_history": settled(history),
                    "stop_reason": "investigation_limit",
                    "source_errors": _error(state, "INVESTIGATION_SKIPPED", "no evidence, budget or iteration/time limit")}
        history = history or [HumanMessage(content=_json(state["llm_summary"]))]
        try:
            result = investigate_role({**state, "messages": history}, config)
            response = result["messages"][-1]
            calls = getattr(response, "tool_calls", [])
            # Do not silently drop extra calls: remove the whole invalid request and stop research.
            if len(calls) > 3:
                response = nodes.without_tool_calls(response)
                response = response.model_copy(update={"content": "{}"})
            return {"usage": result["usage"], "cost": result["cost"], "stage": "investigate",
                    "investigation_history": [*history, response], "iteration": state.get("iteration", 0) + 1}
        except Exception:
            return {"investigation_history": settled(history), "stop_reason": "investigation_error",
                    "source_errors": _error(state, "LLM_UNAVAILABLE",
                    "investigation unavailable; deterministic report retained"), "stage": "investigate"}

    def approve_tools(state: State, config: RunnableConfig) -> dict:
        """
        Остановка перед выполнением инструментов: решение принимает оператор.

        Спрашивают не про всё подряд. По умолчанию решение нужно там, где текст
        запроса составила модель: остальные инструменты ходят по серверной карте
        метрик и границам контура, и разрешение на них оператор уже дал
        настройками. `NT_TOOL_APPROVAL=all` расширяет вопрос на любой вызов,
        `off` убирает остановку совсем.
        """
        limits = settings_for()
        mode = limits.tool_approval
        calls = pending_calls(state)

        def asks(call) -> bool:
            # Про выключенный инструмент оператора не спрашивают: он ответит
            # NOT_ENABLED и без решения. Негодный запрос тоже не показывают —
            # инструмент откажет сам и объяснит модели, что именно нарушено.
            if mode == "all":
                return True
            if not (mode == "generated" and call["name"] in COMPOSED_TOOLS
                    and limits.generated_queries):
                return False
            return not query_guard.validate(str((call.get("args") or {}).get("promql", "")),
                                            str(state.get("namespace") or ""),
                                            max_range_seconds=limits.query_range_seconds)

        waiting = [call for call in calls if asks(call)]
        if mode == "off" or not waiting:
            return {"stage": "approve_tools", "tool_approval": {}}
        payload = {
            "action": "query",
            "title": "Модель просит выполнить запросы к источникам",
            "queries": [{"tool": call["name"],
                         "query": str(call.get("args", {}).get("promql", "")),
                         "purpose": str(call.get("args", {}).get("purpose", "")),
                         "arguments": {k: v for k, v in (call.get("args") or {}).items()
                                       if k not in {"promql", "purpose"}}}
                        for call in waiting],
            "warnings": ["Запрос составила модель. Единицы измерения не проверяются, "
                         "и на вердикт по SLA такой ряд не влияет — он идёт в улики."],
            "hint": 'ответьте true/false или {"decision": "rejected", "reason": ...}',
        }
        answer = interrupt(payload)
        decision = nodes.approval_of(answer)
        identifiers = [call["id"] for call in waiting]
        approved = decision["decision"] == "approved"
        return {"stage": "approve_tools", "tool_approval": {
            "approved": identifiers if approved else [],
            "rejected": [] if approved else identifiers,
            "reason": decision.get("reason", "")}}

    def denied_responses(calls, reason):
        """Ответ инструмента на отклонённый вызов: висячих вызовов остаться не должно."""
        text = _json(failure("POLICY_DENIED", "operator rejected this call"
                             + (f": {reason}" if reason else "")))
        return [ToolMessage(content=text, tool_call_id=call["id"], name=call["name"])
                for call in calls]

    def additional_tools(state: State, config: RunnableConfig) -> dict:
        history = state["investigation_history"]
        approval = state.get("tool_approval") or {}
        denied = set(approval.get("rejected") or [])
        refusals = []
        if denied:
            calls = pending_calls(state)
            refusals = denied_responses([c for c in calls if c["id"] in denied],
                                        approval.get("reason", ""))
            kept = [c for c in calls if c["id"] not in denied]
            if not kept:
                return {"investigation_history": [*history, *refusals],
                        "stop_reason": "operator_rejected", "stage": "additional_tools"}
            # Отклонённые вызовы снимаются с ответа модели, иначе ToolNode
            # выполнит их вместе с разрешёнными.
            extra = dict(getattr(history[-1], "additional_kwargs", None) or {})
            extra.pop("tool_calls", None)
            trimmed = history[-1].model_copy(update={"tool_calls": kept, "additional_kwargs": extra})
            state = {**state, "investigation_history": [*history[:-1], trimmed]}
        result = tool_node.invoke(state, {**config, "max_concurrency": settings_for().concurrency})
        responses = [*result["investigation_history"], *refusals]
        evidence = dict(state.get("evidence", {}))
        enriched = []
        for response in responses:
            if isinstance(response, ToolMessage):
                try:
                    data = json.loads(response.content)
                    if data.get("success"):
                        evidence_id = f"tool:{response.tool_call_id}"
                        data["evidence_id"] = evidence_id
                        evidence[evidence_id] = data
                        response = response.model_copy(update={"content": _json(data)})
                except (ValueError, TypeError, AttributeError):
                    pass
            enriched.append(response)
        return {"investigation_history": [*history, *enriched],
                "evidence": evidence, "stage": "additional_tools"}

    def final_analysis(state: State) -> dict:
        history = state.get("investigation_history", [])
        hypotheses, recommendations = [], []
        if history and isinstance(history[-1], AIMessage):
            try:
                text = nodes.text_of(history[-1]).strip().removeprefix("```json").removesuffix("```").strip()
                data = json.loads(text)
                for item in data.get("hypotheses", [])[:5]:
                    refs = item.get("evidence_ids", [])
                    if (item.get("service") in state.get("services", [])
                            and item.get("confidence") in {"likely", "possible", "unknown"}
                            and isinstance(item.get("description"), str) and isinstance(refs, list)
                            and refs and all(isinstance(r, str) and r in state.get("evidence", {}) for r in refs)):
                        hypotheses.append({"service": item["service"], "confidence": item["confidence"],
                            "description": confluence.mask_text(item["description"][:1500]), "evidence_ids": refs[:10]})
                recommendations = [confluence.mask_text(r[:1000]) for r in data.get("recommendations", [])[:10]
                                   if isinstance(r, str)]
            except (ValueError, TypeError, AttributeError):
                pass
        return {"root_cause_hypotheses": hypotheses, "recommendations": recommendations, "stage": "final_analysis"}

    def compare_baseline(state: State) -> dict:
        previous_id = state.get("previous_test_id")
        if not previous_id:
            return {"stage": "compare_baseline"}
        try:
            backend = sources_for()
            result = backend.load_testing.get_test_results(previous_id) if backend.load_testing else {}
            data = result.get("data", {})
            if not result.get("success") or data.get("test_status") != "completed":
                raise ValueError("previous test unavailable")
            # Контур обязан совпадать. Сценарий — нет: прогоны регрессии
            # именуются по своей ступени, и именно разный профиль нагрузки
            # делает сравнение устойчивой нагрузки осмысленным.
            for key in ("environment", "namespace", "target_service"):
                if not data.get(key) or data[key] != state.get(key):
                    raise ValueError("previous test is not comparable")
            start, end = window(data["started_at"], data["finished_at"], max_seconds=settings_for().max_window)
            previous = backend.collect(state, start, end)
            if previous["errors"]:
                raise ValueError("incomplete previous test")
            comparison = baseline.compare(state["current_metrics"], baseline.summarize(previous["series"]))
            stable = metrics_analyzer.stable_load(previous["series"], state["target_service"],
                thresholds.limits_from(state), settings_for().step, settings_for().stable_seconds)
            payload = {"test_id": previous_id, "metrics": comparison,
                       "note": "stable load compared using current SLA limits"}
            if stable is not None:
                payload["previous_stable_rps"] = stable
            if data.get("scenario") != state.get("scenario"):
                payload["scenario_differs"] = {"current": state.get("scenario"),
                                               "previous": data.get("scenario")}
            return {"previous_comparison": payload, "stage": "compare_baseline"}
        except Exception:
            return {"stage": "compare_baseline", "source_errors": _error(state, "PREVIOUS_TEST_UNAVAILABLE",
                                                                 "previous test could not be compared")}

    def report(state: State) -> dict:
        text = render_report(state)
        return {"artifacts": {"report": text}, "messages": [AIMessage(content=text)], "stage": "report"}

    def audited(name, function):
        def node(state: State, config: RunnableConfig):
            began = time.monotonic()
            try:
                if name in {"load_context", "understand_task", "investigate", "approve_tools",
                            "additional_tools"}:
                    return function(state, config)
                return function(state)
            finally:
                log.info("nt_node run_id=%s jira=%s test_id=%s node=%s duration_ms=%d",
                         state.get("run_id"), state.get("jira_key"), state.get("test_id"),
                         name, (time.monotonic() - began) * 1000)
        return node

    builder = StateGraph(State)
    builder.add_node("context", nodes.context_node)
    stages = {"load_context": load_context, "understand_task": understand_task,
        "discover_scope": discover_scope, "precheck": precheck, "collect_baseline": collect_baseline,
        "collect_metrics": collect_metrics, "detect_anomalies": detect_anomalies, "evaluate_test": evaluate_test,
        "investigate": investigate, "approve_tools": approve_tools,
        "additional_tools": additional_tools, "final_analysis": final_analysis,
        "compare_baseline": compare_baseline, "report": report}
    for name, function in stages.items():
        builder.add_node(name, audited(name, function))
    builder.add_node("remember", nodes.remember_node)
    builder.add_node("approve", partial(nodes.approve_node, pipeline=PIPELINE))
    builder.add_node("publish", partial(nodes.publish_node, pipeline=PIPELINE))
    prefix = [START, "context", "load_context", "understand_task", "discover_scope", "precheck"]
    for left, right in zip(prefix, prefix[1:], strict=False):
        builder.add_edge(left, right)
    builder.add_conditional_edges("precheck", lambda s: "collect_baseline" if s["precheck_result"]["success"] else "report",
                                  {"collect_baseline": "collect_baseline", "report": "report"})
    middle = ["collect_baseline", "collect_metrics", "detect_anomalies", "compare_baseline",
              "evaluate_test", "investigate"]
    for left, right in zip(middle, middle[1:], strict=False):
        builder.add_edge(left, right)
    builder.add_conditional_edges("investigate", investigation_route,
                                  {"approve_tools": "approve_tools", "final_analysis": "final_analysis"})
    builder.add_edge("approve_tools", "additional_tools")
    builder.add_edge("additional_tools", "investigate")
    tail = ["final_analysis", "report", "remember", "approve", "publish", END]
    for left, right in zip(tail, tail[1:], strict=False):
        builder.add_edge(left, right)
    return builder


graph = build_graph().compile()
