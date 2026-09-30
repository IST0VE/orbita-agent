"""
Вложенный прогон: граф не спрашивает оператора и не пишет наружу, а отдаёт
предложения — и по ним пишет вызывающий, один раз.

Под оркестратором без этого режима случается ровно две беды: оператор
подтверждает одно и то же дважды (у вложенного графа и у внешнего), а
документ публикуется дважды. Поэтому проверяется три вещи. Что во вложенном
прогоне нет ни одной остановки, кроме паузы, и ни одной записи наружу. Что
предложение несёт всё, что граф показал бы на остановке, и всё, что нужно
для записи. И что запись по предложению сверяет то же, что сверяет сам граф:
чужое, изменённое или устаревшее согласие ничего не пишет.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated, TypedDict

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from test_jira_graph import state_with_cards
from test_nt_graph import INPUT as NT_INPUT
from test_nt_graph import composed_call, composed_source, setup_source
from test_nt_graph import model as nt_model
from test_nt_run import INPUT as RUN_INPUT
from test_nt_run import FakeRunner, analyzer, decision, write
from test_update_graph import NEW, ORIGINAL, Model
from test_update_graph import plan as update_plan

from agent import (
    config as cfg,
)
from agent import (
    jira_graph,
    jira_writer,
    memory,
    nt_graph,
    nt_run_graph,
    pause,
    proposals,
    publishers,
    roles,
    update_graph,
)
from agent.graph import build_graph

QUESTION = "Что отвечать клиенту про экспорт?"
ANSWER = AIMessage(
    content="Экспорт больше 100000 строк приходит на почту.",
    response_metadata={
        "token_usage": {
            "prompt_cache_hit_tokens": 100,
            "prompt_cache_miss_tokens": 10,
            "completion_tokens": 5,
        }
    },
)


class Once:
    """Одна заготовка на все роли: проверяется не текст, а что с ним делают."""

    def invoke(self, messages: list) -> AIMessage:
        return ANSWER


def nested(thread: str, **extra) -> dict:
    return {"configurable": {"thread_id": thread, "nested": True, **extra}}


def published_files() -> list[Path]:
    directory = Path(cfg.publish_dir())
    return sorted(directory.glob("*.md")) if directory.exists() else []


def approve(proposal: dict, **extra) -> dict:
    return {"decision": "approved", "id": proposal["id"], **extra}


@pytest.fixture(autouse=True)
def everything_asks(monkeypatch: pytest.MonkeyPatch):
    """Все остановки включены: во вложенном прогоне не должна случиться ни одна."""
    monkeypatch.setenv("PUBLISH_REQUIRE_APPROVAL", "1")
    monkeypatch.setenv("PIPELINE_REQUIRE_APPROVAL", "1")
    monkeypatch.setenv("MEMORY_ENABLED", "1")

    def remembered(*args, **kwargs):
        raise AssertionError("вложенный прогон не пишет в долгую память")

    monkeypatch.setattr(memory, "fact_from", remembered)


def run_pipeline(thread: str = "nested-1") -> dict:
    app = build_graph(llm=Once()).compile(checkpointer=InMemorySaver())
    return app.invoke({"messages": [HumanMessage(QUESTION)]}, config=nested(thread))


# --------------------------------------------------------------------------
# Конвейер из ролей: ворота, память и публикация
# --------------------------------------------------------------------------
def test_a_nested_pipeline_neither_stops_nor_publishes():
    result = run_pipeline()

    assert "__interrupt__" not in result
    assert published_files() == []
    assert result["publication"]["status"] == "proposed"
    assert list(result["artifacts"]).count(roles.LAST.key) == 1


def test_the_proposal_carries_what_the_operator_would_have_seen():
    (proposal,) = run_pipeline()["proposals"]

    assert proposal["kind"] == "publish"
    assert proposal["graph"] == "agent"
    assert proposal["approval_required"] is True
    assert proposal["prompt"]["action"] == "publish"
    assert [draft["id"] for draft in proposal["prompt"]["drafts"]] == list(roles.KEYS)
    assert len(proposal["effect"]["pages"]) == len(roles.ROLES)
    assert all("Экспорт больше 100000 строк" in page["document"]
               for page in proposal["effect"]["pages"])
    assert proposal["id"] == proposals.digest_of(proposal)


def test_an_approved_proposal_is_published_once():
    (proposal,) = run_pipeline()["proposals"]

    result = proposals.apply(proposal, approve(proposal))

    assert result["status"] == "created"
    assert len(published_files()) == len(roles.ROLES)


def test_without_the_policy_asking_no_decision_is_needed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PUBLISH_REQUIRE_APPROVAL", "0")
    (proposal,) = run_pipeline()["proposals"]

    assert proposal["approval_required"] is False
    assert proposals.apply(proposal)["status"] == "created"


@pytest.mark.parametrize(
    "answer, status",
    [
        (None, "rejected"),
        ({"decision": "rejected", "reason": "не сейчас"}, "rejected"),
        ({"decision": "drafts"}, "rejected"),
        ({"decision": "approved", "id": "другое"}, "stale"),
    ],
)
def test_no_approval_of_this_proposal_writes_nothing(answer, status):
    (proposal,) = run_pipeline()["proposals"]

    assert proposals.apply(proposal, answer)["status"] == status
    assert published_files() == []


def test_a_proposal_changed_after_it_was_issued_writes_nothing():
    (proposal,) = run_pipeline()["proposals"]
    pages = proposal["effect"]["pages"]
    forged = {
        **proposal,
        "effect": {**proposal["effect"], "pages": [{**pages[0], "document": "подмена"}, *pages[1:]]},
    }

    assert proposals.apply(forged, approve(proposal))["status"] == "stale"
    assert published_files() == []


def test_a_destination_changed_after_the_proposal_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Согласие давали на папку, в которую документы уже не уедут."""
    (proposal,) = run_pipeline()["proposals"]
    monkeypatch.setenv("PUBLISH_DIR", str(tmp_path / "другая"))

    result = proposals.apply(proposal, approve(proposal))

    assert result["status"] == "stale"
    assert "назначение" in result["reason"]
    assert published_files() == []


def test_the_operator_pause_is_kept_in_a_nested_run():
    """Паузу просит человек, а не граф: вложенный режим её не отменяет."""
    pause.board.request("nested-pause")
    try:
        result = run_pipeline("nested-pause")
    finally:
        pause.board.cancel("nested-pause")

    assert result["__interrupt__"][0].value["action"] == "pause"


class Parent(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    proposals: list


def parent_of(child, *, inner: bool):
    """Внешний граф с одним узлом, который зовёт конвейер ради результата."""

    def delegate(state: Parent, config) -> dict:
        configurable = {**config["configurable"], "nested": inner}
        result = child.invoke({"messages": state["messages"]}, {**config, "configurable": configurable})
        return {"proposals": result.get("proposals") or []}

    builder = StateGraph(Parent)
    builder.add_node("delegate", delegate)
    builder.add_edge(START, "delegate")
    builder.add_edge("delegate", END)
    return builder.compile(checkpointer=InMemorySaver())


def test_under_a_parent_graph_the_question_reaches_the_parent_as_a_proposal():
    child = build_graph(llm=Once()).compile()
    config = {"configurable": {"thread_id": "parent-1"}}

    # Без вложенного режима вопрос вложенного графа всплывает остановкой
    # внешнего — и внешний, задав свой, спросил бы оператора второй раз.
    asked = parent_of(child, inner=False).invoke({"messages": [HumanMessage(QUESTION)]}, config)
    assert asked["__interrupt__"][0].value["action"] == "stage"

    result = parent_of(child, inner=True).invoke(
        {"messages": [HumanMessage(QUESTION)]}, {"configurable": {"thread_id": "parent-2"}}
    )
    assert "__interrupt__" not in result
    assert [item["kind"] for item in result["proposals"]] == ["publish"]
    assert published_files() == []


def test_a_new_turn_starts_without_the_proposals_of_the_last_one(monkeypatch):
    app = build_graph(llm=Once()).compile(checkpointer=InMemorySaver())
    config = nested("nested-turns")
    app.invoke({"messages": [HumanMessage(QUESTION)]}, config)
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")

    again = app.invoke({"messages": [HumanMessage("Добавь про CSV")]}, config)

    assert again["proposals"] == []


# --------------------------------------------------------------------------
# Jira: заведение задач
# --------------------------------------------------------------------------
@pytest.fixture
def tracker(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_TOKEN", "tok")
    monkeypatch.setenv("JIRA_PROJECT_KEY", "ORB")
    calls: list[dict] = []

    def create_issues(plan, project, *, settings=None, source="", run="", journal=None):
        calls.append({"project": project, "items": [item.local for item in plan.items], "run": run})
        return {"status": "created", "project": project, "created": [], "failed": []}

    def asked(payload):
        raise AssertionError("вложенный прогон не спрашивает про заведение задач")

    monkeypatch.setattr(jira_writer, "create_issues", create_issues)
    monkeypatch.setattr(jira_writer, "projects", lambda: [{"key": "ORB", "name": "Orbita"}])
    monkeypatch.setattr(jira_writer, "issue_types", lambda project, settings=None: ["Epic", "Task"])
    monkeypatch.setattr(jira_graph, "interrupt", asked)
    return calls


def test_nested_decomposition_proposes_issues_instead_of_creating_them(tracker):
    update = jira_graph.create_node(state_with_cards(), nested("jira-nested"))

    assert tracker == []
    assert update["issues"]["status"] == "proposed"
    (proposal,) = update["proposals"]
    assert proposal["kind"] == "jira"
    assert proposal["approval_required"] is True
    assert proposal["prompt"]["action"] == "jira"
    assert [card["title"] for card in proposal["prompt"]["drafts"]]
    assert proposal["effect"]["project"] == "ORB"


def test_approved_issues_are_created_in_the_project_the_operator_chose(tracker):
    (proposal,) = jira_graph.create_node(state_with_cards(), nested("jira-nested"))["proposals"]

    result = proposals.apply(proposal, approve(proposal, project="pay"), thread="parent-thread")

    assert result["status"] == "created"
    assert [call["project"] for call in tracker] == ["PAY"]
    assert tracker[0]["items"] == ["EPIC-1", "TASK-1"]
    # Повтор применения находит свои операции в журнале по ключу прогона.
    again = proposals.apply(proposal, approve(proposal, project="pay"), thread="parent-thread")
    assert again["status"] == "created"
    assert tracker[1]["run"] == tracker[0]["run"]


def test_cards_changed_after_the_proposal_create_nothing(tracker):
    (proposal,) = jira_graph.create_node(state_with_cards(), nested("jira-nested"))["proposals"]
    effect = {**proposal["effect"], "document": proposal["effect"]["document"].replace(
        "Идемпотентность", "Другое")}
    forged = {**proposal, "effect": effect}
    forged["id"] = proposals.digest_of(forged)

    assert proposals.apply(forged, approve(forged), thread="parent-thread")["status"] == "stale"
    assert tracker == []


def test_issues_are_not_created_for_an_unknown_caller(tracker):
    """
    Без треда вызывающего у всех предложений с теми же карточками и проектом
    один ключ прогона: второе вернуло бы задачи первого вместо своих.
    """
    (proposal,) = jira_graph.create_node(state_with_cards(), nested("jira-nested"))["proposals"]

    with pytest.raises(ValueError, match="треда вызывающего"):
        proposals.apply(proposal, approve(proposal))
    with pytest.raises(ValueError, match="треда вызывающего"):
        jira_graph.create_proposed(proposal["effect"], thread="  ")
    assert tracker == []


def test_the_same_cards_in_another_caller_thread_are_another_operation(tracker):
    first = jira_graph.create_node(state_with_cards(), nested("nested-a"))["proposals"][0]
    second = jira_graph.create_node(state_with_cards(), nested("nested-b"))["proposals"][0]

    proposals.apply(first, approve(first), thread="parent-a")
    proposals.apply(second, approve(second), thread="parent-b")

    assert tracker[0]["run"] != tracker[1]["run"]


# --------------------------------------------------------------------------
# Обновление документа
# --------------------------------------------------------------------------
@pytest.fixture
def revision(monkeypatch: pytest.MonkeyPatch):
    from agent import inputs

    monkeypatch.setenv("PUBLISH_TARGET", "file")
    base = inputs.ensure_root() / "main"
    extra = inputs.ensure_root() / "new"
    base.mkdir()
    extra.mkdir()
    (base / "api.md").write_text(ORIGINAL, encoding="utf-8")
    (extra / "meeting.txt").write_text(NEW, encoding="utf-8")
    return nested(
        "revision-nested",
        base_dir="main",
        base_file="api.md",
        input_dir="new",
        input_file=["meeting.txt"],
    )


def test_a_nested_revision_proposes_the_new_version_and_saves_nothing(revision):
    app = update_graph.build_graph(llm=Model(update_plan())).compile(checkpointer=InMemorySaver())

    state = app.invoke({"messages": [HumanMessage("Обнови API по решению встречи.")]}, revision)

    assert "__interrupt__" not in state
    assert publishers.documents() == []
    assert state["publication"]["status"] == "proposed"
    (proposal,) = state["proposals"]
    assert proposal["graph"] == "update"
    assert proposal["approval_required"] is True
    assert "30 секунд" in proposal["effect"]["pages"][0]["document"]

    assert proposals.apply(proposal, approve(proposal))["status"] == "created"
    assert len(publishers.documents()) == 1


# --------------------------------------------------------------------------
# Анализ НТ: запросы модели и резервное сохранение
# --------------------------------------------------------------------------
def test_nested_analysis_refuses_composed_queries_without_asking():
    sources, prom = composed_source()
    app = nt_graph.build_graph(
        nt_model(composed_call(), AIMessage(content="{}")), sources=sources
    ).compile(checkpointer=InMemorySaver())

    state = app.invoke({**NT_INPUT, "messages": [HumanMessage("Анализ НТ")]},
                       nested("nt-nested", publish=False))

    assert "__interrupt__" not in state
    assert not [call for call in prom.calls if call[0] == "composed"]
    denied = json.loads(state["investigation_history"][2].content)
    assert denied["error_type"] == "POLICY_DENIED"
    assert "nested run" in denied["message"]


def test_nested_analysis_keeps_the_report_instead_of_saving_a_fallback_file(monkeypatch):
    """Резервный файл — тоже запись наружу: вложенный прогон её не делает."""
    monkeypatch.setenv("PUBLISH_TARGET", "confluence")
    sources, _ = setup_source()

    state = nt_graph.build_graph(nt_model(AIMessage(content="{}")), sources=sources).compile().invoke(
        {**NT_INPUT, "messages": [HumanMessage("Анализ НТ")]}, nested("nt-fallback", publish=True)
    )

    assert state["publication"]["status"] == "skipped"
    assert publishers.documents() == []
    assert "# NT Report" in state["artifacts"]["report"]


def test_nested_analysis_proposes_its_report(monkeypatch):
    monkeypatch.setenv("PUBLISH_TARGET", "file")
    sources, _ = setup_source()

    state = nt_graph.build_graph(nt_model(AIMessage(content="{}")), sources=sources).compile().invoke(
        {**NT_INPUT, "messages": [HumanMessage("Анализ НТ")]}, nested("nt-propose", publish=True)
    )

    (proposal,) = state["proposals"]
    assert proposal["graph"] == "nt"
    assert publishers.documents() == []
    assert proposals.apply(proposal, approve(proposal))["status"] == "created"
    assert len(publishers.documents()) == 1


# --------------------------------------------------------------------------
# Проведение НТ: запуск нагрузки и уточнение
# --------------------------------------------------------------------------
def campaign(runner, *answers, **kw):
    app = nt_run_graph.build_graph(
        GenericFakeChatModel(messages=iter(answers)),
        runner=runner, analyzer=analyzer, poll_seconds=0, **kw,
    ).compile(checkpointer=InMemorySaver())
    config = {**nested("run-nested", publish=False), "recursion_limit": 200}
    return asyncio.run(app.ainvoke(RUN_INPUT, config))


@pytest.mark.parametrize("automatic", [False, True])
def test_nested_campaign_proposes_the_launch_and_never_loads_the_stand(automatic):
    runner = FakeRunner()

    result = campaign(runner, write(), decision("ready"), auto_approve=automatic)

    assert "__interrupt__" not in result
    assert not runner.prepared and not runner.starts
    (proposal,) = result["proposals"]
    assert proposal["kind"] == "nt_launch"
    assert proposal["approval_required"] is (not automatic)
    assert "import http" in proposal["prompt"]["document"]
    assert proposal["effect"]["commitment"]["plan"]["target"] == "local"
    assert "передан вызывающему графу" in result["artifacts"]["report"]
    with pytest.raises(proposals.NotApplicable):
        proposals.apply(proposal, approve(proposal))


def test_nested_campaign_hands_the_question_over_and_stops_asking_the_model():
    runner = FakeRunner()

    result = campaign(runner, decision("clarify", question="Какой SLA?"))

    assert "__interrupt__" not in result
    (proposal,) = result["proposals"]
    assert proposal["kind"] == "nt_clarify"
    assert proposal["effect"] == {"question": "Какой SLA?"}
    assert "Какой SLA?" in result["artifacts"]["report"]
    assert result["usage"]["calls"] == 1
