from __future__ import annotations

import json
from copy import deepcopy

import pytest
import responses
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent import inputs, publishers, update_graph, update_plan
from agent.ui_engine.registry import registry

ORIGINAL = "# API\n\nТаймаут: 10 секунд.\n\n## Авторизация\nТокен обязателен.\n"
NEW = "На встрече решили: таймаут 30 секунд."
CHANGE = {
    "old": "Таймаут: 10 секунд.",
    "new": "Таймаут: 30 секунд.",
    "source": "meeting.txt",
    "quote": "таймаут 30 секунд",
    "reason": "Решение встречи",
}


def plan(changes=None, questions=None):
    return json.dumps(
        {"changes": [CHANGE] if changes is None else changes, "questions": questions or []},
        ensure_ascii=False,
    )


class Model:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        return AIMessage(content=self.response)


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv("PUBLISH_TARGET", "file")
    monkeypatch.setenv("MEMORY_ENABLED", "0")
    # Deliberately disabled globally: this graph still requires explicit save approval.
    monkeypatch.setenv("PUBLISH_REQUIRE_APPROVAL", "0")
    base = inputs.ensure_root() / "main"
    extra = inputs.ensure_root() / "new"
    base.mkdir()
    extra.mkdir()
    (base / "api.md").write_text(ORIGINAL, encoding="utf-8")
    (extra / "meeting.txt").write_text(NEW, encoding="utf-8")
    return {
        "configurable": {
            "thread_id": "revision-test",
            "base_dir": "main",
            "base_file": "api.md",
            "input_dir": "new",
            "input_file": ["meeting.txt"],
        }
    }


def run(config, response=None):
    model = Model(plan() if response is None else response)
    graph = update_graph.build_graph(llm=model).compile(checkpointer=InMemorySaver())
    state = graph.invoke({"messages": [HumanMessage("Обнови API по решению встречи.")]}, config)
    return graph, state, model


def test_only_explicit_spans_change_and_source_is_required():
    result = update_plan.apply_plan(ORIGINAL, {"meeting.txt": NEW}, plan())
    assert result["document"] == ORIGINAL.replace("10 секунд", "30 секунд")
    assert "## Авторизация\nТокен обязателен.\n" in result["document"]
    assert "-Таймаут: 10 секунд." in result["diff"]
    assert "+Таймаут: 30 секунд." in result["diff"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("old", "не существующий текст"),
        ("old", ""),
        ("source", "not-selected.txt"),
        ("quote", "выдуманное решение"),
        ("quote", ""),
        ("new", None),
        ("reason", ""),
    ],
)
def test_invalid_edits_are_rejected(field, value):
    change = {**CHANGE, field: value}
    with pytest.raises(update_plan.UpdateError):
        update_plan.apply_plan(ORIGINAL, {"meeting.txt": NEW}, plan([change]))


def test_ambiguous_and_overlapping_edits_are_rejected():
    with pytest.raises(update_plan.UpdateError, match="однозначно"):
        update_plan.apply_plan(ORIGINAL * 2, {"meeting.txt": NEW}, plan())
    with pytest.raises(update_plan.UpdateError, match="пересекаются"):
        update_plan.apply_plan(
            ORIGINAL,
            {"meeting.txt": NEW},
            plan(
                [
                    CHANGE,
                    {**CHANGE, "old": "10 секунд", "new": "30 секунд"},
                ]
            ),
        )


def test_all_edits_refer_to_original_not_previous_replacements():
    changes = [{**CHANGE, "old": "alpha", "new": "beta"}, {**CHANGE, "old": "beta", "new": "gamma"}]
    result = update_plan.apply_plan("alpha beta", {"meeting.txt": NEW}, plan(changes))
    assert result["document"] == "beta gamma"


def test_run_reviews_exact_new_version_and_saves_only_after_approval(setup):
    graph, state, model = run(setup)
    assert len(model.calls) == 1
    payload = json.loads(model.calls[0][-1].content)
    assert payload["original"] == ORIGINAL
    assert payload["materials"] == {"meeting.txt": NEW}
    assert publishers.documents() == []
    approval = state["__interrupt__"][0].value
    assert approval["drafts"][0]["document"] == state["document"]
    assert "таймаут 30 секунд" in approval["document"]
    state = graph.invoke(Command(resume={"decision": "approved"}), setup)
    assert state["publication"]["status"] == "created"
    docs = publishers.documents()
    assert len(docs) == 1
    assert state["document"] in publishers.read_document(docs[0]["name"])
    assert inputs.read("main", "api.md") == ORIGINAL
    assert len(model.calls) == 1  # Resume does not invoke the model again.


def test_rejection_keeps_draft_and_writes_nothing(setup):
    graph, _, _ = run(setup)
    state = graph.invoke(Command(resume={"decision": "rejected"}), setup)
    assert state["publication"]["status"] == "rejected"
    assert "30 секунд" in state["document"]
    assert publishers.documents() == []


@pytest.mark.parametrize(
    "change",
    [
        {"base_file": ""},
        {"input_file": []},
        {"input_file": ["missing.txt"]},
        {"base_file": "../../outside.md"},
        {"input_dir": "main", "input_file": ["api.md"]},
    ],
)
def test_bad_input_stops_before_model(setup, change):
    config = deepcopy(setup)
    config["configurable"].update(change)
    _, state, model = run(config)
    assert state["error"]
    assert model.calls == []
    assert publishers.documents() == []


def test_oversized_package_stops_before_model(setup, monkeypatch):
    monkeypatch.setenv("AGENT_INPUT_MAX_CHARS", str(len(ORIGINAL) + 3))
    _, state, model = run(setup)
    assert "превышают" in state["error"]
    assert not model.calls


def test_generated_invalid_plan_is_not_published(setup):
    _, state, _ = run(setup, "Всё исправлено!")
    assert state["publication"]["status"] == "failed"
    assert "__interrupt__" not in state
    assert publishers.documents() == []


def test_unresolved_questions_are_visible_without_spurious_changes(setup):
    _, state, _ = run(setup, plan([], ["Какой таймаут утвердили?"]))
    assert state["document"] == ORIGINAL
    assert "Какой таймаут" in state["artifacts"]["changes"]
    assert state["publication"]["status"] == "unchanged"
    assert "__interrupt__" not in state


def test_disabled_publication_still_produces_reviewable_document(setup, monkeypatch):
    monkeypatch.setenv("PUBLISH_TARGET", "none")
    _, state, _ = run(setup)
    assert "30 секунд" in state["document"]
    assert state["publication"]["status"] == "disabled"
    assert "__interrupt__" not in state


def test_changed_destination_after_review_is_not_used(setup, monkeypatch):
    graph, _, _ = run(setup)
    monkeypatch.setenv("PUBLISH_TARGET", "confluence")
    state = graph.invoke(Command(resume={"decision": "approved"}), setup)
    assert state["publication"]["status"] == "failed"
    assert publishers.documents() == []


def test_changed_output_directory_is_not_used(setup, monkeypatch, tmp_path):
    graph, _, _ = run(setup)
    monkeypatch.setenv("PUBLISH_DIR", str(tmp_path / "other"))
    state = graph.invoke(Command(resume={"decision": "approved"}), setup)
    assert state["publication"]["status"] == "failed"
    assert not (tmp_path / "other").exists()


def test_original_can_be_a_previously_published_document(setup):
    publishers.FilePublisher().publish("Previous", ORIGINAL)
    previous = publishers.documents()[0]["name"]
    setup["configurable"].update(base_dir="@published", base_file=previous)
    _, state, _ = run(setup)
    assert "30 секунд" in state["document"]
    assert "10 секунд" in publishers.read_document(previous)


def test_open_questions_are_in_save_confirmation(setup):
    _, state, _ = run(setup, plan(questions=["Уточнить повторные попытки?"]))
    assert "Уточнить повторные попытки?" in state["__interrupt__"][0].value["warnings"]


@responses.activate
def test_confluence_receives_only_approved_document_not_change_report(setup, monkeypatch):
    monkeypatch.setenv("PUBLISH_TARGET", "confluence")
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://wiki.example.com")
    monkeypatch.setenv("CONFLUENCE_TOKEN", "test-token")
    monkeypatch.setenv("CONFLUENCE_SPACE_KEY", "DOCS")
    endpoint = "https://wiki.example.com/rest/api/content"
    responses.get(endpoint, json={"results": []})
    responses.post(endpoint, json={"id": "123", "version": {"number": 1}}, status=200)
    graph, state, _ = run(setup)
    approved = state["__interrupt__"][0].value["drafts"][0]["document"]
    assert all(call.request.method == "GET" for call in responses.calls)
    state = graph.invoke(Command(resume={"decision": "approved"}), setup)
    assert state["publication"]["status"] == "created"
    writes = [call for call in responses.calls if call.request.method == "POST"]
    assert len(writes) == 1
    body = json.loads(writes[0].request.body)["body"]["storage"]["value"]
    assert body == approved
    assert "30 секунд" in body
    assert "Решение встречи" not in body


def test_manifest_has_independent_original_and_material_selectors():
    manifest = registry.resolve("update").value
    fields = {item["id"]: item for item in manifest["input"]}
    assert fields["base_task"]["options"]["document_input"] == "base_document"
    assert fields["task"]["options"]["document_input"] == "document"
    assert fields["base_document"]["target"] == "configurable.base_file"
    assert not fields["base_document"]["options"]["multiple"]
    assert fields["document"]["options"]["multiple"]
