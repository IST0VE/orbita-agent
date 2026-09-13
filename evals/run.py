"""
Прогнать набор качества и сравнить его с базовой версией.

    python evals/run.py                 прогнать и сравнить с baseline.json
    python evals/run.py --update        записать текущий результат как базовый
    python evals/run.py --case contradiction
    python evals/run.py --live          настоящий провайдер; стоит денег

Отдельная команда, а не тест, и это не небрежность. Unit-тесты отвечают на
вопрос «код делает то, что написано»; набор — на вопрос «получившийся документ
годится для работы». Их легко совместить неправильно: конвейер проходит все
проверки и выпускает документ, в котором пропали ссылки на источники. Поэтому
ухудшение качества видно отдельной работой с отдельным отчётом.

Пороги регрессии живут в `baseline.json` рядом с базовыми показателями: в нём
записано, что получилось на базовом прогоне, а в самих случаях — чего от них
ждут по существу (`expect`). Первое ловит сползание, второе — поломку.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from evals import harness  # noqa: E402

BASELINE = HERE / "baseline.json"

#: Насколько результат может отличаться от базового, не считаясь ухудшением.
#: Показатели, которые должны расти, и те, которые должны падать, перечислены
#: отдельно: «отклонение на 10%» без направления разрешило бы вдвое меньше
#: найденных противоречий.
HIGHER_IS_BETTER = ("source_accuracy", "contradictions_mentioned", "gaps_marked", "stages_done")
LOWER_IS_BETTER = ("unsupported_claims",)
#: Ресурсы: рост дороже и дольше — тоже ухудшение, но с запасом на дрожание.
BUDGETS = {"usd": 1.5, "seconds": 3.0}
TOLERANCE = 0.05


def expectations(case: dict, result: dict) -> list[str]:
    """Проверки по существу случая: что он обязан показать в любом прогоне."""
    problems = []
    expect = case.get("expect") or {}
    if result["failed_to_run"]:
        return [f"{result['id']}: прогон не состоялся ({result['failed_to_run']})"]
    checks = {
        "min_source_accuracy": ("source_accuracy", lambda a, b: a >= b, "ниже"),
        "min_gaps_marked": ("gaps_marked", lambda a, b: a >= b, "меньше"),
        "min_contradictions_mentioned": ("contradictions_mentioned", lambda a, b: a >= b, "меньше"),
        "max_unsupported_claims": ("unsupported_claims", lambda a, b: a <= b, "больше"),
        "max_published_files": ("published_files", lambda a, b: a <= b, "больше"),
    }
    for name, limit in expect.items():
        if name in checks:
            field, ok, word = checks[name]
            if not ok(result[field], limit):
                problems.append(f"{result['id']}: {field}={result[field]} {word} ожидаемого {limit}")
        elif name == "publication_status" and result["publication_status"] != limit:
            problems.append(
                f"{result['id']}: публикация {result['publication_status']!r}, ожидалось {limit!r}"
            )
        elif name == "publication_mentions" and limit not in (result["publication_reason"] or ""):
            problems.append(f"{result['id']}: в причине публикации нет {limit!r}")
    return problems


def regressions(base: dict, now: dict) -> list[str]:
    """Чем текущий прогон хуже базового."""
    problems = []
    for case_id, before in (base.get("cases") or {}).items():
        after = (now.get("cases") or {}).get(case_id)
        if after is None:
            problems.append(f"{case_id}: случай исчез из набора")
            continue
        for field in HIGHER_IS_BETTER:
            if after[field] < before[field] * (1 - TOLERANCE):
                problems.append(f"{case_id}: {field} {before[field]} -> {after[field]}")
        for field in LOWER_IS_BETTER:
            if after[field] > before[field] + max(1, before[field] * TOLERANCE):
                problems.append(f"{case_id}: {field} {before[field]} -> {after[field]}")
        for field, factor in BUDGETS.items():
            allowed = before[field] * factor + (0.5 if field == "seconds" else 0.000001)
            if after[field] > allowed:
                problems.append(
                    f"{case_id}: {field} {before[field]} -> {after[field]} (порог {round(allowed, 6)})"
                )
    return problems


def report(now: dict) -> str:
    rows = ["| случай | этапов | ссылки | без опоры | противоречия | пробелы | $ | с |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for case_id, item in now["cases"].items():
        rows.append(
            f"| {case_id} | {item['stages_done']} | {item['source_accuracy']} | "
            f"{item['unsupported_claims']} | {item['contradictions_mentioned']} | "
            f"{item['gaps_marked']} | {item['usd']} | {item['seconds']} |"
        )
    return "\n".join(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", help="прогнать только этот случай")
    parser.add_argument("--live", action="store_true", help="настоящий провайдер; стоит денег")
    parser.add_argument("--update", action="store_true", help="записать результат как базовый")
    parser.add_argument("--json", action="store_true", help="вывести результат как JSON")
    args = parser.parse_args()

    now = harness.run(args.case, live=args.live)
    if args.json:
        print(json.dumps(now, ensure_ascii=False, indent=2))
    else:
        print(report(now))

    cases = {case["id"]: case for case in harness.load_cases(args.case)}
    problems = []
    for case_id, result in now["cases"].items():
        problems += expectations(cases[case_id], result)

    if args.update:
        BASELINE.write_text(json.dumps(now, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\nбазовый прогон записан: {BASELINE.name}")
        if problems:
            print("но ожидания случаев не выполнены:")
            print("\n".join("  " + item for item in problems))
            return 1
        return 0

    if BASELINE.is_file() and not args.case:
        problems += regressions(json.loads(BASELINE.read_text(encoding="utf-8")), now)
    elif not BASELINE.is_file():
        print("\nбазового прогона нет: запустите с --update")

    if problems:
        print("\nухудшение качества:")
        print("\n".join("  " + item for item in problems))
        return 1
    print("\nкачество не ухудшилось")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
