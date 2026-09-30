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
import os
import re
import sys
import time
from contextlib import contextmanager
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

    def __init__(self, answers: list[str | dict]) -> None:
        self.answers = list(answers) or [""]
        self.calls = 0

    def invoke(self, messages: list, **_: Any) -> AIMessage:
        answer = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        # Ответ может быть и вызовом инструментов: `{"tool_calls": [{"name",
        # "args"}]}`. Так сценарий повторяет роль поиска, которая читает
        # страницу сама, — и её чтение проходит через настоящий инструмент.
        calls = []
        if isinstance(answer, dict):
            calls = [
                {"name": call["name"], "args": call.get("args") or {}, "id": f"call-{self.calls}-{n}"}
                for n, call in enumerate(answer.get("tool_calls") or [])
            ]
            answer = str(answer.get("content") or "")
        # Счётчики расхода настоящие по форме: их разбирает тот же `costmeter`,
        # что и ответы провайдера, и стоимость считается тем же кодом.
        return AIMessage(
            content=answer,
            tool_calls=calls,
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
#: POSTGRES_ — тоже: иначе случай конвейера подготовки записал бы изменение
#: в базу разработчика (`changes.py`).
PREFIXES = ("LLM_", "PRICE_", "CONFLUENCE_", "AGENT_", "BUDGET_", "KNOWLEDGE_", "MEMORY_",
            "PUBLISH_", "CHECKPOINT_", "JIRA_", "ATLASSIAN_", "NT_", "DIAGRAM_",
            "POSTGRES_", "PREP_", "TOOL_")


def _isolate(case: dict, workdir: Path, *, live: bool = False) -> None:
    """Окружение прогона: только то, что задал набор и сам случай."""
    # Tracing is another external write and must not inherit personal settings.
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    for name in list(os.environ):
        if live and name.startswith(("LLM_", "PRICE_")):
            continue
        if name.startswith(PREFIXES):
            os.environ.pop(name, None)
    if not live:
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
        if live and name.startswith(("LLM_", "PRICE_", "OPENAI_", "ANTHROPIC_", "DEEPSEEK_")):
            continue
        os.environ[name] = value


@contextmanager
def isolated(case: dict, workdir: Path, *, live: bool):
    # Import configuration before the snapshot: its one-time .env loading must
    # not undo isolation on the first case or remove settings on later cases.
    from agent import config  # noqa: F401

    previous = dict(os.environ)
    try:
        _isolate(case, workdir, live=live)
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def run_case(case: dict, workdir: Path, *, live: bool = False) -> dict:
    """Один случай: прогон, показатели и то, что от него ожидалось."""
    with isolated(case, workdir, live=live):
        if case.get("graph") == "prep":
            with sources_of(case):
                return _run_prep(case, workdir, live=live)
        return _run_case(case, workdir, live=live)


# --------------------------------------------------------------------------
# Конвейер подготовки задачи
#
# Он читает Jira и Confluence, поэтому случай несёт их содержимое с собой:
# задачи по ключу, страницы по id и что находит поиск. Сеть не участвует —
# чтение подменено на время случая. Остальное — настоящее: узел чтения задачи,
# инструменты, запись Evidence, проверка ссылок и узел изменения.
# --------------------------------------------------------------------------
_EV = re.compile(r"\{ev:(jira|confluence|file):([^}]+)\}")


@contextmanager
def sources_of(case: dict):
    from agent import confluence, jira

    issues = case.get("jira") or {}
    pages = case.get("confluence") or {}
    found = case.get("search") or {}

    def fetch_issue(key, *_, **__):
        key = str(key).strip().upper()
        if key not in issues:
            raise jira.JiraError(f"задача {key} не найдена (404)")
        return {"key": key, "url": f"https://jira.example.com/browse/{key}", **issues[key]}

    def fetch_page(page_id, *_, **__):
        page_id = str(page_id).strip()
        if page_id not in pages:
            raise confluence.ConfluenceError(f"страница {page_id} не найдена (404)")
        return {"id": page_id, "url": f"https://wiki.example.com/pages/{page_id}",
                "truncated": False, **pages[page_id]}

    saved = (jira.fetch_issue, jira.search, confluence.fetch_page, confluence.search)
    jira.fetch_issue = fetch_issue
    jira.search = lambda *_, **__: list(found.get("jira") or [])
    confluence.fetch_page = fetch_page
    confluence.search = lambda *_, **__: list(found.get("confluence") or [])
    try:
        yield
    finally:
        jira.fetch_issue, jira.search, confluence.fetch_page, confluence.search = saved


def evidence_ids(case: dict) -> dict[tuple[str, str], str]:
    """
    Id источников случая — тем же кодом, каким их даёт конвейер.

    Сценарий ссылается на источник заглушкой `{ev:confluence:700001}`: id
    зависит от версии источника, и переписывать его руками в каждом ответе
    значило бы ломать случай любой правкой разметки.
    """
    from agent import evidence

    found = {}
    for key, issue in (case.get("jira") or {}).items():
        found[("jira", key)] = evidence.make_id("jira", key, str(issue.get("updated") or ""))
    for page_id, page in (case.get("confluence") or {}).items():
        found[("confluence", page_id)] = evidence.make_id(
            "confluence", page_id, str(page.get("version"))
        )
    for name, text in (case.get("materials") or {}).items():
        found[("file", name)] = evidence.make_id("file", name, evidence.digest(text)[:12])
    return found


def _with_ids(answer, ids: dict) -> Any:
    def swap(text: str) -> str:
        return _EV.sub(lambda m: ids[(m.group(1), m.group(2))], text)

    if isinstance(answer, dict):
        return {**answer, "content": swap(str(answer.get("content") or ""))}
    return swap(answer)


def critic_metrics(citations: dict, planted: list[dict] | None, *, live: bool) -> dict:
    """
    Что нашла проверка ссылок и совпало ли это с заложенными провалами.

    planted — провалы, которые сценарий повторяет дословно: этап, вид
    замечания и кусок строки. Найденное сверх них — ложные замечания: критик,
    который шумит, перестают читать так же быстро, как критик, который молчит.
    У настоящей модели (`--live`) заложенного нет: там счёт замечаний — это
    сколько раз модель нарушила правила.
    """
    from agent import critic

    found = critic.findings(citations)
    counted = critic.summary(citations)
    result = {
        "evidence_refs": counted["refs"],
        "quotes_verified": counted["verified"],
        "critic_findings": len(found),
        "critic_kinds": counted["kinds"],
    }
    if live or planted is None:
        return {**result, "critic_recall": None, "critic_false_flags": None}
    matched: set[int] = set()
    caught = 0
    for plant in planted:
        hits = [
            index for index, item in enumerate(found)
            if item["stage"] == plant["stage"] and item["kind"] == plant["kind"]
            and plant["contains"] in item["text"]
        ]
        if hits:
            caught += 1
            matched.update(hits)
    return {
        **result,
        "critic_recall": round(caught / len(planted), 3) if planted else 1.0,
        "critic_false_flags": len(found) - len(matched),
    }


def _run_prep(case: dict, workdir: Path, *, live: bool) -> dict:
    from agent import prep_graph, prep_roles, publishers

    folder = _materials(case, workdir / "input")
    ids = evidence_ids(case)
    answers = [_with_ids(answer, ids) for answer in case.get("answers") or []]
    model = None if live else ScriptedModel(answers)
    app = prep_graph.build_graph(llm=model).compile()
    config = {"configurable": {"thread_id": f"eval-{case['id']}", "input_dir": folder,
                               "input_file": list(case.get("picked") or [])}}

    started = time.time()
    failure = ""
    try:
        state = app.invoke({"messages": [HumanMessage(case["task"])]}, config=config)
    except Exception as exc:  # noqa: BLE001 - отказ прогона это тоже результат
        state, failure = {}, f"{type(exc).__name__}: {exc}"
    seconds = round(time.time() - started, 3)

    pipeline = prep_roles.PIPELINE
    documents = {
        key: text for key, text in (state.get("artifacts") or {}).items() if key in pipeline.keys
    }
    measured = checks.measure(documents, materials=case.get("materials"),
                              versions=case.get("material_versions"),
                              facts=case.get("facts"), contradictions=case.get("contradictions"))
    publication = state.get("publication") or {}
    cost = state.get("cost") or {}
    published = publishers.documents() if not failure else []
    return {
        "id": case["id"],
        "kind": case["kind"],
        "mode": "live" if live else "scripted",
        "material_versions": checks.material_versions(case.get("materials") or {}),
        "failed_to_run": failure,
        "stages_done": len(documents),
        "publication_status": publication.get("status", ""),
        "publication_reason": publication.get("reason", ""),
        "published_files": len(published),
        "usd": round(float(cost.get("usd", 0.0)), 6),
        "seconds": seconds,
        "calls": int(cost.get("calls", 0)),
        **measured,
        **critic_metrics(state.get("citations") or {}, case.get("planted"), live=live),
    }


def _run_case(case: dict, workdir: Path, *, live: bool) -> dict:
    from agent import publishers
    from agent.builder import build_graph
    from agent.pipeline import Pipeline

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

    # Документы ролей, и только они: рядом в `artifacts` код кладёт прочитанные
    # материалы и реестр источников. Мерить их как документы значило бы
    # находить факты в самом первоисточнике и считать его строки утверждениями.
    documents = {
        key: text for key, text in (state.get("artifacts") or {}).items() if key in pipeline.keys
    }
    measured = checks.measure(documents, materials=case.get("materials"),
                              versions=case.get("material_versions"),
                              facts=case.get("facts"), contradictions=case.get("contradictions"))
    publication = state.get("publication") or {}
    cost = state.get("cost") or {}
    published = publishers.documents() if not failure else []

    return {
        "id": case["id"],
        "kind": case["kind"],
        "mode": "live" if live else "scripted",
        "material_versions": checks.material_versions(case.get("materials") or {}),
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
