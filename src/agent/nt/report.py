"""The factual report is rendered by code. LLM contributes labelled hypotheses only."""

import json
from datetime import UTC, datetime

from agent import confluence

# Полный текст источника нужен модели во время исследования, но в отчёте он
# вытесняет разбор: приложение Evidence занимало 59% страницы. В улике остаётся
# начало, по которому её узнают; сам источник перечитывается по ссылке в Sources.
TOOL_TEXT_LIMIT = 600


def _json(value) -> str:
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n```"


def _num(value) -> str:
    """Агрегаты приходят с хвостом float: 180.9523809523809 не читают, а пролистывают."""
    if value is None:
        return "—"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    return str(value) if isinstance(value, int) else f"{value:.4g}"


def _time(value) -> str:
    """Секунды эпохи в таблице ничего не сообщают тому, кто читает отчёт."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%d %H:%M:%SZ")
    return "—" if value is None else str(value)


def _cell(value) -> str:
    """Вертикальная черта и перевод строки внутри ячейки разрывают строку таблицы."""
    text = value if isinstance(value, str) else _num(value)
    return text.replace("|", "\\|").replace("\n", " ").strip() or "—"


def _table(headers: list[str], rows: list[list]) -> str:
    """Те же поля, что в дампе, но без кавычек, отступов и повторения имён ключей."""
    if not rows:
        return "Нет данных."
    return "\n".join(["| " + " | ".join(headers) + " |",
                      "| " + " | ".join("---" for _ in headers) + " |",
                      *("| " + " | ".join(_cell(cell) for cell in row) + " |" for row in rows)])


def _shortened(item):
    """Улику инструмента показываем целиком по структуре и урезанно по тексту."""
    if not isinstance(item, dict):
        return item
    return {key: value[:TOOL_TEXT_LIMIT] + f"… (ещё {len(value) - TOOL_TEXT_LIMIT} знаков)"
            if isinstance(value, str) and len(value) > TOOL_TEXT_LIMIT else value
            for key, value in item.items()}


def _quality(item: dict) -> str:
    """Неполный ряд меняет цену наблюдения, и знать об этом надо в той же строке."""
    notes = []
    if item.get("partial"):
        notes.append("частичный ряд")
    if item.get("invalid_points"):
        notes.append(f"негодных точек: {item['invalid_points']}")
    # Шага ряда в улике нет, поэтому сравниваем с фактическим средним расстоянием
    # между точками: провал вдвое шире среднего — это пропуск, а не округление.
    gap, start, end, count = (item.get("max_gap"), item.get("start"),
                              item.get("end"), item.get("count"))
    if all(isinstance(v, (int, float)) for v in (gap, start, end, count)) and count > 1:
        if gap > 2 * (end - start) / (count - 1):
            notes.append(f"пропуск до {_num(gap)} с")
    return "; ".join(notes)


def _evidence(state: dict, focus: set) -> list[str]:
    """
    Улики — таблица наблюдений, а не дамп состояния.

    Строку сохраняет всё, на что ссылается гипотеза, и всё по сервисам в фокусе:
    по evidence_id из раздела Root cause analysis наблюдение обязано находиться.
    Остальное сворачивается счётчиком — это сервисы, где ничего не сработало.
    """
    evidence = state.get("evidence", {}) or {}
    cited = set()
    for hypothesis in state.get("root_cause_hypotheses", []) or []:
        cited.update(hypothesis.get("evidence_ids") or [])
        cited.update(hypothesis.get("counter_evidence_ids") or [])
    kept, hidden = {}, 0
    for key, item in evidence.items():
        service = item.get("service") if isinstance(item, dict) else None
        if key in cited or not service or service in focus:
            kept[key] = item
        else:
            hidden += 1
    metrics = [[key, item.get("service"), item.get("metric"), item.get("unit"),
                _num(item.get("median")), _num(item.get("p95")), _num(item.get("max")),
                item.get("count"), item.get("source"), _quality(item)]
               for key, item in kept.items()
               if key.startswith("metric:") and isinstance(item, dict)]
    # Столбец качества нужен там, где ряд неполон. Когда он пуст у всех рядов,
    # это шестьдесят прочерков, за которыми теряются те, где пропуск был.
    metric_headers = ["evidence_id", "сервис", "метрика", "единица", "медиана", "p95",
                      "максимум", "точек", "источник", "качество"]
    if not any(row[-1] for row in metrics):
        metric_headers = metric_headers[:-1]
        metrics = [row[:-1] for row in metrics]
    findings = [[key, item.get("service"), item.get("metric"), _num(item.get("limit")),
                 _num(item.get("peak")), item.get("count"), _time(item.get("first_at")),
                 _time(item.get("last_at")), item.get("source")]
                for key, item in kept.items()
                if key.startswith("finding:") and isinstance(item, dict)]
    rest = {key: _shortened(item) for key, item in kept.items()
            if not key.startswith(("metric:", "finding:"))}
    out = [f"**Ряды метрик ({len(metrics)})**",
           _table(metric_headers, metrics),
           f"**Срабатывания порогов ({len(findings)})**",
           _table(["evidence_id", "сервис", "метрика", "предел", "пик", "срабатываний",
                   "первое", "последнее", "источник"], findings),
           f"**Улики инструментов ({len(rest)})**", _json(rest)]
    if hidden:
        out.append(f"Свёрнуто улик: {hidden}. Это сервисы вне фокуса анализа, на которые не "
                   "ссылается ни одна гипотеза; наблюдения целиком остаются в состоянии прогона.")
    return out


def _verdict_provenance(state: dict) -> str:
    """
    Чем измерен вердикт: сервис, период, источники, метрики и причины.

    Голый вердикт непроверяем: по слову «PASSED» нельзя сказать, за какой
    промежуток и по каким рядам оно получено, а по «INCONCLUSIVE» — чего
    именно не хватило.
    """
    verdict = state.get("sla_verdict") or {}
    if not verdict:
        return "Происхождение вердикта не сохранено."
    period = verdict.get("period") or {}
    rows = [
        ["сервис", verdict.get("service") or "не указан"],
        ["окружение", " / ".join(x for x in (verdict.get("environment"), verdict.get("namespace")) if x) or "не указано"],
        ["период", f"{period.get('from') or '—'} — {period.get('to') or '—'}"
                   + (f" ({period['seconds']} с)" if period.get("seconds") is not None else "")],
        ["источники", ", ".join(verdict.get("sources") or []) or "нет"],
        ["проверено метрик", ", ".join(verdict.get("metrics") or []) or "нет"],
        ["чем измерен", "максимумы рядов за период целиком, не плато"],
    ]
    if verdict.get("simulated_sources"):
        rows.append(["симулированные источники", ", ".join(verdict["simulated_sources"])])
    capacity = verdict.get("capacity") or {}
    rows.append(["устойчивая RPS", str(capacity.get("maximum_stable_rps"))
                 + f" ({capacity.get('status', 'INCONCLUSIVE')})"])
    out = [_table(["поле", "значение"], rows)]
    if verdict.get("reasons"):
        out.append("Причины:")
        out.extend(f"- {reason}" for reason in verdict["reasons"])
    return "\n\n".join(out)


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
        "## Execution", state.get("execution_status", "UNKNOWN"),
        "Выполнение теста и вердикт SLA — разные вещи: прерванный тест не получает PASSED, "
        "а завершившийся не обязан в SLA укладываться.",
        "## Result", state.get("analysis_result", "INCONCLUSIVE"),
        _verdict_provenance(state),
        "Вердикт вычислен кодом по доступным SLA и не меняется формулировками модели. "
        "Проверяются максимумы временных рядов за заданный период; p95/p99 ряда не "
        "являются перцентилями всех запросов теста и не описывают устойчивое плато.",
        "## Diagnostic completeness", state.get("diagnostic_status", "NOT_RUN"),
        _json(state.get("diagnostic_gaps", [])),
        "Полнота диагностики оценивается отдельно: отсутствие baseline или данных зависимости "
        "не отменяет проверку полных SLA-рядов целевого сервиса.",
        "## Precheck", _json(state.get("precheck_result", {})), "## SLA"])
    focus = {key.split(":")[1] for key in state.get("evidence", {}) if key.startswith("metric:")}
    if state.get("target_service"):
        focus.add(state["target_service"])

    def limited(mapping, what):
        """Разбор по всему namespace тонет в дампах сервисов, где ничего не было."""
        mapping = mapping or {}
        shown = {service: value for service, value in mapping.items() if service in focus}
        # Сравнение всегда про одно и то же: baseline, текущее, разница. Таблицей
        # это читают глазами, дампом — нет. Незнакомую форму не ломаем.
        comparable = all(isinstance(changes, dict) and all(isinstance(c, dict) for c in changes.values())
                         for changes in shown.values())
        body = _table(["сервис", "метрика", "baseline", "текущее", "разница", "%"],
                      [[service, metric, _num(change.get("baseline")), _num(change.get("current")),
                        _num(change.get("absolute")), _num(change.get("percent"))]
                       for service, changes in shown.items()
                       for metric, change in changes.items()]) if comparable else _json(shown)
        note = (f"Показаны {len(shown)} из {len(mapping)} {what}: остальные вне фокуса "
                f"анализа. Агрегаты доступны в состоянии прогона; исходные точки перечитываются из источников.")
        return [body, note] if len(shown) < len(mapping) else [body]

    metrics = state.get("current_metrics", {}).get(state.get("target_service"), {})
    from agent.nt.thresholds import SLA_FIELDS
    for field, metric in SLA_FIELDS.items():
        if state.get(field) is not None:
            lines.append(f"- {metric}: peak={metrics.get(metric, {}).get('max', 'нет данных')}; "
                         f"limit={(state.get('sla_comparators') or {}).get(metric, '<=')} {state[field]}; "
                         f"unit={metrics.get(metric, {}).get('unit', 'нет данных')}")
    lines.append(_json(state.get("threshold_violations", [])))
    stable = state.get("maximum_stable_rps")
    lines.extend(["## Maximum stable load", f"{stable} RPS" if stable is not None else "Не установлена.",
        "Наблюдаемая устойчивая нагрузка: минимум RPS на плато после периода установления, "
        "вся измеренная часть которого соблюдала SLA. Это нижняя оценка по наблюдениям, "
        "а не доказанный предел мощности.",
        _json({k: v for k, v in state.get("capacity_assessment", {}).items() if k != "phases"}),
        "## Load phases", _json(state.get("load_phases", [])),
        "## Main anomalies", f"Сервисов с данными: {len(state.get('current_metrics', {}))}."])
    ranked = [r for r in state.get("ranked_services", []) if r["score"] > 0]
    lines.append(_table(["сервис", "score", "важность", "метрики со срабатываниями"],
        [[item.get("service"), _num(item.get("score")), item.get("severity"),
          ", ".join(item.get("metrics") or [])] for item in ranked[:20]]))
    if len(ranked) > 20:
        lines.append(f"Показаны 20 сервисов с наибольшим score из {len(ranked)} со срабатываниями.")
    timeline = [e for e in state.get("timeline", []) if not e.get("service") or e["service"] in focus]
    lines.extend(["## Timeline", _table(["время", "событие", "сервис", "метрика"],
        [[_time(event.get("at")), event.get("event"), event.get("service"), event.get("metric")]
         for event in timeline])])
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
        lines.append(f"  Механизм: {hypothesis.get('mechanism', 'unknown')}. "
                     "Причинная связь не установлена.")
        # Блок кода, приклеенный к строке, не начинается с начала строки и блоком
        # не становится: его закрывающий забор открывал следующий, чётность сбивалась
        # на каждой гипотезе, и в storage-разметке Confluence весь дальнейший отчёт
        # уезжал внутрь кода. Наблюдения — такая же таблица, как остальные улики.
        lines.append("  Проверенные наблюдения:")
        lines.append(_table(["evidence_id", "метрика", "максимум", "пик", "предел", "первое",
                             "вид", "%"],
            [[item.get("evidence_id"), item.get("metric"), _num(item.get("max")),
              _num(item.get("peak")), _num(item.get("limit")), _time(item.get("first_at")),
              item.get("kind"), _num(item.get("percent"))]
             for item in hypothesis.get("observations", [])]))
        lines.append("  Противоречащие данные: " + ", ".join(hypothesis.get("counter_evidence_ids", [])))
        lines.append("  Следующая проверка: " + hypothesis.get("next_check", "не указана"))
    lines.append(_json(state.get("hypothesis_assessment", {})))
    # Запросы, которых нет в серверной карте: их составила модель, и оператор
    # разрешил выполнение. В отчёте они приводятся дословно — иначе цифру из
    # них не повторить и не оспорить.
    composed = [item for item in state.get("evidence", {}).values()
                if isinstance(item, dict) and item.get("origin") == "model_query"]
    if composed:
        lines.extend(["## Composed queries",
                      "Запросы составлены моделью и выполнены по политике подтверждения сервера. Единицы не "
                      "проверены, в вердикт по SLA эти ряды не входят."])
        for item in composed:
            lines.append("\n".join([
                f"- {item.get('purpose') or 'без пояснения'}",
                f"  `{item.get('query', '')}`",
                f"  Evidence: {item.get('evidence_id', '')}",
            ]))
    lines.extend(["## Evidence", *_evidence(state, focus), "## Baseline comparison",
                  *limited(state.get("baseline_comparison"), "сервисов")])
    previous = dict(state.get("previous_comparison") or {})
    lines.append("## Previous test comparison")
    if previous:
        lines.extend([_json({k: v for k, v in previous.items() if k not in {"metrics", "descriptive_metrics"}}),
                      "regressions измерены на сопоставимых ступенях: это наблюдения одинаковой "
                      "нагрузки в двух прогонах, а не установленная причина. Различия по всему "
                      "периоду ниже описательные: прогоны держали там разную нагрузку.",
                      *limited(previous.get("descriptive_metrics"), "сервисов прошлого прогона")])
    else:
        lines.append(_json({}))
    lines.append("## Recommendations")
    lines.extend(f"- Предложение LLM, требует проверки: {r}" for r in state.get("recommendations", []))
    if not state.get("recommendations"):
        lines.append("Дополнительные рекомендации не сформированы.")
    lines.extend(["## Unverified assumptions", _json(state.get("missing_parameters", [])),
        _json({"stop_reason": state.get("stop_reason", "")}),
        _json(state.get("source_errors", [])),
        "Precheck относится к данным завершённого теста. Текущие DNS/HTTP/pods не подтверждают "
        "их состояние в прошлом. Запуск и остановка НТ этим графом не выполнялись.",
        "## Analysis policy", _json(state.get("analysis_policy", {})),
        "## Sources", _json([{k: v for k, v in s.items() if k != "text"}
                               for s in state.get("context_sources", [])]),
        _json({"baseline_start": state.get("baseline_start"), "baseline_end": state.get("baseline_end"),
               "metric_sources": sorted({m["source"] for metrics in state.get("current_metrics", {}).values()
                                         for m in metrics.values()})})])
    return confluence.mask_text("\n\n".join(lines))
