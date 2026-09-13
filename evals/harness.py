"""
Прогон набора задач и сбор показателей качества.

Набор существует ради вопроса, на который unit-тесты не отвечают: конвейер
по-прежнему проходит все проверки — но выпускает ли он документ, с которым
можно работать? Зелёные тесты и ухудшившийся результат совмещаются легко:
достаточно чуть иначе сформулировать роль или подрезать материалы.

Модель по умолчанию подделана и отвечает по сценарию случая. Это не попытка
измерить модель — это попытка измерить код вокруг неё: замечает ли он
противоречие, не выдаёт ли недоступный источник за прочитанный, отмечает ли
пробелы, честно ли сообщает о неудавшейся публикации. Такой прогон
детерминирован, ничего не стоит и потому запускается на каждое изменение.

Живой прогон (`--live`) берёт настроенного провайдера и стоит денег.
Он отвечает на другой вопрос — как справляется модель, — и запускается руками.

Материалы случаев синтетические и обезличенные: вымышленный сервис, вымышленные
ключи задач, адреса из зарезервированных для документации доменов.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

from evals import checks  # noqa: E402

CASES = Path(__file__).resolve().parent / "cases"


class ScriptedModel:
    """
    Модель, отвечающая по сценарию случая.

    Ответы кончились — повторяется последний: число вызовов зависит от
    конвейера, а сценарий описывает содержание, а не арифметику.
    """

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers) or [""]
        self.calls = 0

    def invoke(self, messages: list, **_: Any) -> AIMessage:
        answer = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        # Счётчики расхода настоящие по форме: их разбирает тот же `costmeter`,
        # что и ответы провайдера, и стоимость считается тем же кодом.
        return AIMessage(
            content=answer,
            response_metadata={"token_usage": {
                "prompt_cache_hit_tokens": 900,
                "prompt_cache_miss_tokens": 120,
                "completion_tokens": max(1, len(answer) // 4),
            }},
        )

    def bind_tools(self, tools):  # noqa: ANN001 - совместимость с интерфейсом модели
        return self


def load_cases(only: list[str] | None = None) -> list[dict]:
    found = []
    for path in sorted(CASES.glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        case["file"] = path.name
        if not only or case["id"] in only:
            found.append(case)
    return found


def _materials(case: dict, root: Path) -> str:
    """Материалы случая на диск: конвейер читает их так же, как обычные файлы."""
    folder = root / case["id"]
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in (case.get("materials") or {}).items():
        (folder / name).write_text(text, encoding="utf-8")
    return case["id"]


#: Опорный тариф набора. Он не про реальные цены: он про то, чтобы стоимость
#: одного и того же прогона совпадала на разных машинах. Личный `.env` с
#: половиной заданных PRICE_* давал бы ноль, и показатель стоимости перестал
#: бы что-либо измерять.
REFERENCE_PRICE = {
    "PRICE_CACHE_HIT_PER_MTOK": "0.014",
    "PRICE_CACHE_MISS_PER_MTOK": "0.14",
    "PRICE_CACHE_WRITE_PER_MTOK": "0",
    "PRICE_OUTPUT_PER_MTOK": "0.28",
}

#: Префиксы переменных проекта: набор не должен зависеть от личного `.env`.
#: Иначе настроенный Confluence у разработчика меняет исход случая с публикацией.
PREFIXES = ("LLM_", "PRICE_", "CONFLUENCE_", "AGENT_", "BUDGET_", "KNOWLEDGE_", "MEMORY_",
            "PUBLISH_", "CHECKPOINT_", "JIRA_", "ATLASSIAN_", "NT_", "DIAGRAM_")


def _isolate(case: dict, workdir: Path) -> None:
    """Окружение прогона: только то, что задал набор и сам случай."""
    import os

    for name in list(os.environ):
        if name.startswith(PREFIXES):
            os.environ.pop(name, None)
    os.environ.update(REFERENCE_PRICE)
    os.environ["LLM_PROVIDER"] = "deepseek"
    os.environ["LLM_MODEL"] = "eval-reference-model"
    os.environ["AGENT_INPUT_DIR"] = str(workdir / "input")
    os.environ["PUBLISH_DIR"] = str(workdir / "published" / case["id"])
    os.environ["MEMORY_ENABLED"] = "0"
    os.environ["PUBLISH_REQUIRE_APPROVAL"] = "0"
    os.environ["PUBLISH_TARGET"] = case.get("publish_target", "file")
    os.environ["JIRA_JOURNAL_PATH"] = str(workdir / "jira.sqlite3")
    for name, value in (case.get("environment") or {}).items():
        os.environ[name] = value


def run_case(case: dict, workdir: Path, *, live: bool = False) -> dict:
    """Один случай: прогон, показатели и то, что от него ожидалось."""
    from agent import publishers
    from agent.builder import build_graph
    from agent.pipeline import Pipeline

    _isolate(case, workdir)
    folder = _materials(case, workdir / "input")
    model = None if live else ScriptedModel(case.get("answers") or [])

    from agent import roles as roles_module

    pipeline: Pipeline = roles_module.PIPELINE
    app = build_graph(llm=model, pipeline=pipeline).compile()
    config = {"configurable": {"thread_id": f"eval-{case['id']}", "input_dir": folder}}

    started = time.time()
    failure = ""
    try:
        state = app.invoke({"messages": [HumanMessage(case["task"])]}, config=config)
    except Exception as exc:  # noqa: BLE001 - отказ прогона это тоже результат
        state, failure = {}, f"{type(exc).__name__}: {exc}"
    seconds = round(time.time() - started, 3)

    documents = dict(state.get("artifacts") or {})
    measured = checks.measure(documents)
    publication = state.get("publication") or {}
    cost = state.get("cost") or {}
    published = publishers.documents() if not failure else []

    return {
        "id": case["id"],
        "kind": case["kind"],
        "failed_to_run": failure,
        "stages_done": len(documents),
        "publication_status": publication.get("status", ""),
        "publication_reason": publication.get("reason", ""),
        "published_files": len(published),
        "usd": round(float(cost.get("usd", 0.0)), 6),
        "seconds": seconds,
        "calls": int(cost.get("calls", 0)),
        **measured,
    }


def run(only: list[str] | None = None, *, live: bool = False, workdir: Path | None = None) -> dict:
    import tempfile

    cases = load_cases(only)
    if not cases:
        raise SystemExit("набор пуст: не найдено ни одного случая")
    temporary = None
    if workdir is None:
        temporary = tempfile.TemporaryDirectory()
        workdir = Path(temporary.name)
    try:
        results = [run_case(case, workdir, live=live) for case in cases]
    finally:
        if temporary is not None:
            temporary.cleanup()
    return {"cases": {item["id"]: item for item in results}}
