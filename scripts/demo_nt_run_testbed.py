"""Прогон графа nt_run по стенду nt-testbed с полной стенограммой.

Отличие от `demo_nt_run.py`: тот проверяет границы на временных заглушках и
заранее заданных ответах модели, а этот проводит настоящее НТ — настоящий k6 по
цели стенда, настоящая модель, настоящие Jira и Confluence стенда — и пишет
разбор прогона в Markdown: задача, системный промпт, каждый ход модели с
аргументами вызовов, ответы инструментов, сгенерированные сценарий и k6-скрипт,
команды runner и k6, измерения, стоимость и итоговый отчёт.

Стенограмма нужна для документации и разбора: по журналу графа видно, что он
делал, но не видно, чем модель это обосновала. `run_history` живёт в состоянии
треда и исчезает вместе с checkpointer, поэтому выгружается сразу после прогона.

Перед запуском:

    cd ../nt-testbed && docker compose up -d
    python scripts/serve_nt_runner.py --config config/nt-runner.json

Затем:

    python scripts/demo_nt_run_testbed.py
    python scripts/demo_nt_run_testbed.py --out docs/examples/nt-run-trace.md

Прогон стоит несколько вызовов модели и создаёт реальную нагрузку на стенд.
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
BASE_ENV = ROOT / ".env"
TESTBED_ENV = ROOT / ".env.nt-testbed"
LOOPBACK = ("localhost", "127.0.0.1", "::1")

TASK = """Проведи нагрузочное тестирование стенда checkout.
Сценарий: POST /api/checkout с телом {"cart_id": "<из набора данных>", "items": 2}, ожидается HTTP 200.
Цель: убедиться, что checkout-api держит 20 запросов в секунду.
Разгон 10 секунд, плато 40 секунд, до 10 виртуальных пользователей.
SLA: p95 <= 800 мс, доля ошибок <= 1%.
Остановить прогон при p95 > 2500 мс или доле ошибок > 15%.
Авторизация не нужна, используй синтетические cart_id."""


def environment() -> dict[str, str]:
    """Координаты стенда поверх .env — как это делает scripts/serve_nt_testbed.py."""
    if not TESTBED_ENV.exists():
        sys.exit(f"нет файла {TESTBED_ENV.name}: стенд не настроен")
    env = {k: v for k, v in dotenv_values(BASE_ENV).items() if v is not None}
    env |= {k: v for k, v in dotenv_values(TESTBED_ENV).items() if v is not None}
    hosts = [h.strip() for h in (env.get("NO_PROXY") or "").split(",") if h.strip()]
    hosts += [h for h in LOOPBACK if h not in hosts]
    env |= {"NO_PROXY": ",".join(hosts), "no_proxy": ",".join(hosts)}
    return env


def fence(text: str, language: str = "") -> str:
    """Блок кода, устойчивый к обратным кавычкам внутри самого текста.

    Модель отвечает размеченным текстом со своими блоками кода и заголовками.
    Ограждение короче внутреннего закрывает блок раньше времени, и разметка
    ответа становится разметкой документа: в оглавлении появляются чужие
    заголовки. Поэтому забор всегда на одну кавычку длиннее самой длинной
    цепочки внутри.
    """
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    guard = "`" * max(3, longest + 1)
    return f"{guard}{language}\n{text}\n{guard}"


def transcript(state, prefix: str, plan_files: list[Path]) -> str:
    """История треда в читаемый разбор: кто сказал, что вызвал, что вернулось."""
    from agent import nodes

    lines = ["# Прогон nt_run по стенду nt-testbed", "",
             f"Снято: {time.strftime('%Y-%m-%d %H:%M:%S')}", "",
             "## Задача оператора", "", fence(TASK), "",
             "## Системный промпт планировщика", "",
             "Собирается в узле `plan_next` заново на каждом ходе: сам промпт, JSON Schema",
             "плана и capabilities runner. Схема и лимиты приходят от runner, не от модели.",
             "", fence(prefix), "", "## Ход рассуждения", ""]

    for index, message in enumerate(state.get("run_history", []), 1):
        kind = getattr(message, "type", "?")
        if kind == "human":
            lines += [f"### {index}. Оператор / факты прогона", "", fence(nodes.text_of(message)), ""]
        elif kind == "tool":
            lines += [f"### {index}. Ответ инструмента `{getattr(message, 'name', '?')}`", "",
                      fence(str(message.content)[:4000], "json"), ""]
        else:
            lines.append(f"### {index}. Модель")
            lines.append("")
            text = nodes.text_of(message).strip()
            if text:
                lines += [fence(text), ""]
            for call in getattr(message, "tool_calls", None) or []:
                lines += [f"Вызов `{call['name']}`:", "",
                          fence(json.dumps(call["args"], ensure_ascii=False, indent=2), "json"), ""]
            if not text and not getattr(message, "tool_calls", None):
                lines += ["_(пустой ответ)_", ""]

    lines += ["## Что ушло на стенд", "",
              "Сценарий и скрипт компилирует инструмент, а не модель: хост, редиректы,",
              "таймауты и код заданы компилятором, из плана подставляются только данные.",
              "Команда, которой runner запускает k6 (рабочий каталог — папка прогона):", "",
              fence("k6 run --config k6-config.json --quiet --out json test.js", "text"), ""]
    for path in plan_files:
        if path.is_file():
            body = path.read_text(encoding="utf-8")
            language = "json" if path.suffix == ".json" else "javascript"
            lines += [f"### `{path.parent.name}/{path.name}`", "",
                      fence(body if len(body) <= 6000 else body[:6000] + "\n…", language), ""]

    lines += ["## Журнал действий графа", "",
              fence(json.dumps(state.get("execution_log", []), ensure_ascii=False, indent=2), "json"), "",
              "## Результаты прогонов", "",
              fence(json.dumps([{"kind": r["kind"], "result": r["result"]}
                                for r in state.get("runs", [])], ensure_ascii=False, indent=2), "json"), "",
              "## Исторический анализ (вложенный граф nt)", "",
              fence(json.dumps(state.get("run_analysis", {}), ensure_ascii=False, indent=2), "json"), "",
              "## Стоимость", "",
              fence(json.dumps({"cost": state.get("cost", {}), "usage": state.get("usage", {})},
                               ensure_ascii=False, indent=2), "json"), "",
              "## Итоговый отчёт", "", state.get("artifacts", {}).get("report", "_нет_"), ""]
    return "\n".join(lines)


async def run(out: Path) -> None:
    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    from agent.nt_run.client import RunnerHTTP
    from agent.nt_run.plan import Plan, canonical
    from agent.nt_run_graph import PROMPT, build_graph

    capabilities = RunnerHTTP.from_env().capabilities()
    prefix = (PROMPT + "\nJSON Schema:\n" + canonical(Plan.model_json_schema())
              + "\nCapabilities:\n" + canonical(capabilities))

    app = build_graph(auto_approve=True).compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "trace", "publish": False}, "recursion_limit": 10000}
    began = time.time()
    payload, guard = {"messages": [HumanMessage(TASK)]}, 0
    while guard < 10:
        guard += 1
        async for chunk in app.astream(payload, config, stream_mode="updates"):
            for node, update in chunk.items():
                if node == "__interrupt__":
                    print(f"  [{time.time()-began:6.1f}s] пауза: "
                          f"{json.dumps(getattr(update[0], 'value', {}), ensure_ascii=False)[:90]}", flush=True)
                elif isinstance(update, dict):
                    print(f"  [{time.time()-began:6.1f}s] {node}", flush=True)
        if not app.get_state(config).next:
            break
        payload = Command(resume={"decision": "approved"})

    state = app.get_state(config).values
    root = Path(os.environ.get("NT_RUN_ARTIFACTS", ".nt-runs"))
    started = [e["test_id"] for e in state.get("execution_log", []) if e["event"] == "start"]
    files = [root / test / name for test in started[-1:] for name in ("scenario.json", "test.js")]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(transcript(state, prefix, files), encoding="utf-8")
    # Сырые сообщения рядом с разбором: тред живёт в памяти и исчезает вместе с
    # процессом, а перерисовать разбор другой вёрсткой после этого уже не из чего.
    raw = out.with_suffix(".json")
    raw.write_text(json.dumps({
        "task": TASK, "system_prompt": prefix,
        "messages": [{"type": getattr(message, "type", "?"),
                      "name": getattr(message, "name", None),
                      "text": getattr(message, "content", ""),
                      "tool_calls": getattr(message, "tool_calls", None) or []}
                     for message in state.get("run_history", [])],
        "execution_log": state.get("execution_log", []), "runs": state.get("runs", []),
        "run_analysis": state.get("run_analysis", {}), "cost": state.get("cost", {}),
        "usage": state.get("usage", {}),
        "report": state.get("artifacts", {}).get("report", ""),
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nстенограмма: {out}\nсырые сообщения: {raw}", flush=True)
    print(f"прогонов: {len(state.get('runs', []))}; "
          f"стоимость: {json.dumps(state.get('cost', {}), ensure_ascii=False)}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(ROOT / "docs" / "examples" / "nt-run-trace.md"))
    args = parser.parse_args()
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT / "src"))
    os.environ.update(environment())
    asyncio.run(run(Path(args.out)))


if __name__ == "__main__":
    main()
