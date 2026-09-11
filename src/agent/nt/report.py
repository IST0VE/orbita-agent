"""The factual report is rendered by code. LLM contributes labelled hypotheses only."""

import json
from datetime import UTC, datetime

from agent import confluence


def _json(value) -> str:
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n```"


def render_report(state: dict) -> str:
    if state.get("precheck_result", {}).get("success") is False:
        lines = ["# NT Report", "## Анализ не выполнен", "INCONCLUSIVE",
            f"Тест: {state.get('test_id') or 'не указан'}.",
            "Проверка входных данных не пройдена. Сбор baseline и метрик не выполнялся; "
            "результат нагрузочного теста не оценён.", "## Что мешает анализу"]
        lines.extend(f"- {item}" for item in state.get("missing_parameters", []))
        if state.get("source_errors"):
            lines.extend(["## Ошибки источников", _json(state["source_errors"])])
        lines.extend(["## Как продолжить",
            "Дополните или исправьте перечисленные параметры в запросе либо в источнике "
            "данных теста. Для анализа нужны test_id, target_service, environment, namespace, "
            "started_at, finished_at и хотя бы один SLA."])
        if "не настроен NT_METRIC_QUERIES" in state.get("missing_parameters", []):
            lines.append("Задайте непустой NT_METRIC_QUERIES с запросами для ваших метрик "
                         "и перезапустите backend. Одних URL Prometheus/InfluxDB недостаточно.")
        if state.get("source_errors"):
            lines.append("Проверьте API источников из окружения, где запущен backend. "
                         "Доступность страницы в браузере не подтверждает доступ из backend.")
        lines.extend(["## Precheck", _json(state["precheck_result"])])
        return confluence.mask_text("\n\n".join(lines))
    lines = ["# NT Report", "## Task"]
    for key in ("jira_key", "test_id", "test_status", "environment", "namespace", "target_service", "started_at", "finished_at"):
        value = state.get(key, "не указано")
        if key.endswith("_at") and isinstance(value, (int, float)):
            value = datetime.fromtimestamp(value, UTC).isoformat()
        lines.append(f"- {key}: {value}")
    lines.extend(["## Test profile", _json({k: state.get(k) for k in (
        "target_rps", "duration_seconds", "ramp_up_seconds", "virtual_users", "scenario", "test_type")}),
        "## Result", state.get("analysis_result", "INCONCLUSIVE"),
        "Вердикт вычислен кодом по доступным SLA. Проверяются максимумы временных рядов "
        "за заданный период; p95/p99 ряда не являются перцентилями всех запросов теста.",
        "## Precheck", _json(state.get("precheck_result", {})), "## SLA"])
    focus = {key.split(":")[1] for key in state.get("evidence", {}) if key.startswith("metric:")}
    if state.get("target_service"):
        focus.add(state["target_service"])

    def limited(mapping, what):
        """Разбор по всему namespace тонет в дампах сервисов, где ничего не было."""
        mapping = mapping or {}
        shown = {service: value for service, value in mapping.items() if service in focus}
        note = (f"Показаны {len(shown)} из {len(mapping)} {what}: остальные вне фокуса "
                f"анализа. Полные ряды остались в состоянии прогона.")
        return [_json(shown), note] if len(shown) < len(mapping) else [_json(shown)]

    metrics = state.get("current_metrics", {}).get(state.get("target_service"), {})
    from agent.nt.thresholds import SLA_FIELDS
    for field, metric in SLA_FIELDS.items():
        if state.get(field) is not None:
            lines.append(f"- {metric}: peak={metrics.get(metric, {}).get('max', 'нет данных')}; "
                         f"limit={state[field]}; unit={metrics.get(metric, {}).get('unit', 'нет данных')}")
    lines.append(_json(state.get("threshold_violations", [])))
    stable = state.get("maximum_stable_rps")
    lines.extend(["## Maximum stable load", f"{stable} RPS" if stable is not None else "Не установлена.",
        "Наблюдаемая устойчивая нагрузка: минимум RPS в непрерывном окне соблюдения всех "
        "заданных SLA. Это нижняя оценка по наблюдениям, а не доказанный предел мощности.",
        "## Main anomalies", f"Сервисов с данными: {len(state.get('current_metrics', {}))}."])
    ranked = [r for r in state.get("ranked_services", []) if r["score"] > 0]
    lines.append(_json(ranked[:20]))
    if len(ranked) > 20:
        lines.append(f"Показаны 20 сервисов с наибольшим score из {len(ranked)} со срабатываниями.")
    timeline = [e for e in state.get("timeline", []) if not e.get("service") or e["service"] in focus]
    lines.extend(["## Timeline", _json(timeline)])
    if len(timeline) < len(state.get("timeline", [])):
        lines.append(f"Показаны {len(timeline)} из {len(state.get('timeline', []))} событий: "
                     f"остальные относятся к сервисам вне фокуса анализа.")
    lines.append("## Root cause analysis")
    hypotheses = state.get("root_cause_hypotheses", [])
    if not hypotheses and state.get("stop_reason"):
        lines.append("Исследование остановлено до вывода: улики собраны, но не "
                     "интерпретированы. Причина остановки — в разделе Unverified assumptions.")
    elif not hypotheses:
        lines.append("unknown: причина не установлена.")
    for hypothesis in hypotheses:
        lines.append(f"- Гипотеза ({hypothesis['confidence']}), {hypothesis['service']}: "
                     f"{hypothesis['description']}\n  Evidence: " + ", ".join(hypothesis["evidence_ids"]))
    lines.extend(["## Evidence", _json(state.get("evidence", {})), "## Baseline comparison",
                  *limited(state.get("baseline_comparison"), "сервисов")])
    previous = dict(state.get("previous_comparison") or {})
    lines.append("## Previous test comparison")
    if previous:
        lines.extend([_json({k: v for k, v in previous.items() if k != "metrics"}),
                      *limited(previous.get("metrics"), "сервисов прошлого прогона")])
    else:
        lines.append(_json({}))
    lines.append("## Recommendations")
    lines.extend(f"- Предложение LLM, требует проверки: {r}" for r in state.get("recommendations", []))
    if not state.get("recommendations"):
        lines.append("Дополнительные рекомендации не сформированы.")
    lines.extend(["## Unverified assumptions", _json(state.get("missing_parameters", [])),
        _json(state.get("source_errors", [])),
        "Precheck относится к данным завершённого теста. Текущие DNS/HTTP/pods не подтверждают "
        "их состояние в прошлом. Запуск и остановка НТ этим графом не выполнялись.",
        "## Sources", _json([{k: v for k, v in s.items() if k != "text"}
                               for s in state.get("context_sources", [])]),
        _json({"baseline_start": state.get("baseline_start"), "baseline_end": state.get("baseline_end"),
               "metric_sources": sorted({m["source"] for metrics in state.get("current_metrics", {}).values()
                                         for m in metrics.values()})})])
    return confluence.mask_text("\n\n".join(lines))
