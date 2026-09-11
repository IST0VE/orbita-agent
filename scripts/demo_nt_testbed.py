"""Прогон графа nt против локального стенда nt-testbed.

Читает .env ради провайдера LLM и учёта стоимости, затем кладёт поверх
координаты стенда из .env.nt-testbed. Боевые Jira и Confluence из .env при
этом не задействованы: переменные перекрыты до импорта агента.

    python scripts/demo_nt_testbed.py
    python scripts/demo_nt_testbed.py --test-id nt-run-2187 --no-previous
"""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
# Консоль Windows по умолчанию cp1251: отчёт и гипотезы на русском её ломают.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.nt-testbed", override=True)

# Стенд целиком на localhost, а системный HTTP-прокси перехватывает и его:
# запросы к мокам уходят в прокси и отваливаются по таймауту, precheck остаётся
# без параметров теста, и прогон уходит сразу в отчёт «анализ не выполнен».
sys.path.insert(0, str(Path(__file__).resolve().parent))
from serve_nt_testbed import no_proxy  # noqa: E402

os.environ.update(no_proxy({}))

from langchain_core.messages import HumanMessage  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from agent.nt_graph import build_graph  # noqa: E402


def line(title, value):
    print(f"{title:<26} {value}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jira-key", default="NT-123")
    parser.add_argument("--test-id", default="nt-run-2291")
    parser.add_argument("--previous-test-id", default="nt-run-2187")
    parser.add_argument("--no-previous", action="store_true",
                        help="не сравнивать с прошлым прогоном")
    parser.add_argument("--out", default=str(ROOT / ".tmp" / "nt-report.md"))
    args = parser.parse_args()

    task = f"Проведи анализ НТ по {args.jira_key}.\ntest_id={args.test_id}"
    if not args.no_previous:
        task += f"\nprevious_test_id={args.previous_test_id}"
    print("== Запрос ==")
    print(task, end="\n\n")

    # Составленный моделью запрос ждёт решения оператора. В портале решает
    # человек; здесь прогон непрерывный, поэтому запрос печатается и
    # подтверждается автоматически — иначе терминальный демо-прогон встанет.
    app = build_graph().compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": f"demo-{args.test_id}", "publish": False}}
    state = app.invoke({"messages": [HumanMessage(task)]}, config)
    while state.get("__interrupt__"):
        payload = state["__interrupt__"][0].value
        if payload.get("action") != "query":
            break
        for item in payload.get("queries", []):
            print(f"== Запрос модели ({item.get('tool')}) ==")
            print(item.get("purpose") or "без пояснения")
            print(item.get("query") or item.get("arguments"), end="\n\n")
        state = app.invoke(Command(resume={"decision": "approved"}), config)

    print("== Что получилось ==")
    line("Вердикт", state.get("analysis_result"))
    line("Тест", f'{state.get("test_id")} / {state.get("test_status")}')
    line("Сервис", f'{state.get("target_service")} в {state.get("namespace")}')
    line("SLA", json.dumps({k: state.get(k) for k in
        ("sla_p95_ms", "sla_p99_ms", "sla_error_rate", "sla_max_cpu") if state.get(k) is not None}))
    line("Устойчивая нагрузка", f'{state.get("maximum_stable_rps")} RPS')
    line("Сервисов с метриками", len(state.get("current_metrics", {})))
    line("Нарушений SLA", len(state.get("threshold_violations", [])))
    line("Аномалий", len(state.get("anomalies", [])))
    line("Улик", len(state.get("evidence", {})))
    line("Вызовов модели", state.get("iteration", 0))
    line("Гипотез", len(state.get("root_cause_hypotheses", [])))
    line("Стоимость", state.get("cost"))

    ranked = [r for r in state.get("ranked_services", []) if r["score"] > 0][:5]
    if ranked:
        print("\n== Топ подозреваемых ==")
        for row in ranked:
            print(f'  {row["score"]:.3f} {row["severity"]:<8} {row["service"]:<22} '
                  f'{", ".join(row["metrics"])}')

    previous = state.get("previous_comparison") or {}
    if previous.get("stable_rps"):
        print("\n== Регрессия к прошлому прогону ==")
        print(f'  {previous["test_id"]}: {json.dumps(previous["stable_rps"], ensure_ascii=False)}')

    for hypothesis in state.get("root_cause_hypotheses", []):
        print(f'\n  Гипотеза ({hypothesis["confidence"]}) {hypothesis["service"]}: '
              f'{hypothesis["description"]}')

    for item in state.get("missing_parameters", []):
        print(f"  ПРОБЕЛ: {item}")
    for error in state.get("source_errors", []):
        print(f'  ОШИБКА ИСТОЧНИКА: {error.get("error_type")} {error.get("message")}')

    report = state.get("artifacts", {}).get("report", "")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8", newline="")
    print(f"\nОтчёт ({len(report)} символов): {out}")


if __name__ == "__main__":
    main()
