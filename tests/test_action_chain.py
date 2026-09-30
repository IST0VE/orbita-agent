"""
Порядок действий на деле: предложение → правила → согласие → выполнение → сверка.

Jira здесь — трекер в памяти за настоящим REST (`responses`): он заводит,
отдаёт и правит задачи так же, как их прислали, а тест меняет их «руками на
доске». Проверяется то, ради чего порядок заводился: второй ход того же чата
не заводит карточки заново, исправленная карточка правится на месте, чужая
правка на доске не затирается, ответ по старой версии предложения — не
согласие, а нормализация трекера не выглядит чужой правкой.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlparse

import pytest
import responses
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from test_jira_graph import CARDS, fenced, state_with_cards
from test_update_graph import run as run_update
from test_update_graph import setup  # noqa: F401 - фикстура графа обновления

from agent import (
    actions,
    jira_graph,
    jira_journal,
    jira_writer,
    proposals,
    publish_nodes,
    publishers,
    roles,
)
from agent import config as cfg
from agent.graph import build_graph

BASE = "https://jira.example.com"
API = "/rest/api/3"
THREAD = {"configurable": {"thread_id": "chain-1"}}


class Tracker:
    """Jira Cloud в памяти: заводит, отдаёт и правит задачи по REST."""

    def __init__(self, *, rewrite=None) -> None:
        self.issues: dict[str, dict] = {}
        self.puts: list[str] = []
        # Как трекер переписывает присланное: так Cloud режет ADF, а Data
        # Center меняет переводы строк.
        self.rewrite = rewrite or (lambda fields: fields)
        responses.add(
            responses.GET,
            f"{BASE}{API}/issue/createmeta/ORB/issuetypes",
            json={"values": [{"name": "Epic"}, {"name": "Task"}]},
        )
        responses.add_callback(responses.POST, f"{BASE}{API}/issue", callback=self.create)
        pattern = re.compile(re.escape(f"{BASE}{API}/issue/") + r"ORB-\d+")
        responses.add_callback(responses.GET, pattern, callback=self.read)
        responses.add_callback(responses.PUT, pattern, callback=self.update)
        # Поиск по метке операции: сверка отправки, ответ на которую потерян.
        responses.add_callback(responses.GET, f"{BASE}{API}/search/jql", callback=self.search)

    def search(self, request):
        label = request.url.split("labels")[-1]
        found = [{"key": key, "fields": {"summary": fields["summary"]}}
                 for key, fields in self.issues.items()
                 if any(mark in label for mark in fields.get("labels") or [])]
        return 200, {}, json.dumps({"issues": found})

    @staticmethod
    def _key(request) -> str:
        return urlparse(request.url).path.rsplit("/", 1)[-1]

    def create(self, request):
        key = f"ORB-{len(self.issues) + 1}"
        self.issues[key] = self.rewrite(json.loads(request.body)["fields"])
        return 201, {}, json.dumps({"key": key})

    def read(self, request):
        key = self._key(request)
        return 200, {}, json.dumps({"key": key, "fields": self.issues[key]})

    def update(self, request):
        key = self._key(request)
        self.puts.append(key)
        self.issues[key] = self.rewrite({**self.issues[key], **json.loads(request.body)["fields"]})
        return 204, {}, ""

    @property
    def posts(self) -> int:
        return sum(call.request.method == "POST" for call in responses.calls)


@pytest.fixture
def jira_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JIRA_BASE_URL", BASE)
    monkeypatch.setenv("JIRA_TOKEN", "tok")
    monkeypatch.setenv("JIRA_PROJECT_KEY", "ORB")
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")


class Operator:
    """Отвечает на остановки по очереди и запоминает, что ему показали."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.shown: list[dict] = []

    def __call__(self, payload: dict):
        self.shown.append(payload)
        answer = self.answers.pop(0)
        return answer(payload) if callable(answer) else answer


def agree(payload: dict) -> dict:
    """Согласие на показанное: с отпечатком, как отвечает интерфейс."""
    return {"decision": "approved", "digest": payload["digest"]}


def edited(description: str = "Ключ живёт сутки, повтор отвечает тем же платежом.") -> dict:
    cards = copy.deepcopy(CARDS)
    cards["issues"][1]["description"] = description
    return cards


def first_turn(monkeypatch) -> dict:
    monkeypatch.setattr(jira_graph, "interrupt", Operator(agree))
    update = jira_graph.create_node(state_with_cards(), THREAD)
    assert update["issues"]["status"] == "created", update
    return update


# --------------------------------------------------------------------------
# Jira: второй ход и правка на месте
# --------------------------------------------------------------------------
@responses.activate
def test_the_first_turn_creates_and_reads_back_every_issue(jira_env, monkeypatch):
    tracker = Tracker()

    update = first_turn(monkeypatch)

    assert tracker.posts == 2
    check = update["issues"]["verification"]
    assert [item["key"] for item in check["verified"]] == ["ORB-1", "ORB-2"]
    assert "Сверка после записи: совпало 2" in update["messages"][0].content


@responses.activate
def test_a_second_turn_with_the_same_cards_asks_nothing_and_sends_nothing(jira_env, monkeypatch):
    """Прежде правка в треде заводила заново все карточки, включая неизменённые."""
    tracker = Tracker()
    first_turn(monkeypatch)

    def asked(payload):
        raise AssertionError("спрашивать не о чем: отправлять нечего")

    monkeypatch.setattr(jira_graph, "interrupt", asked)
    update = jira_graph.create_node(state_with_cards(), THREAD)

    assert update["issues"]["status"] == "unchanged"
    assert "ORB-1" in update["issues"]["reason"] and "ORB-2" in update["issues"]["reason"]
    assert tracker.posts == 2 and tracker.puts == []


@responses.activate
def test_an_edited_card_is_updated_in_place_and_the_rest_left_alone(jira_env, monkeypatch):
    tracker = Tracker()
    first_turn(monkeypatch)
    operator = Operator(agree)
    monkeypatch.setattr(jira_graph, "interrupt", operator)

    update = jira_graph.create_node(state_with_cards(fenced(edited())), THREAD)

    (shown,) = operator.shown
    assert shown["title"] == "Jira: правок 1, без изменений 1"
    assert [(d["id"], d["action"]) for d in shown["drafts"]] == [("TASK-1", "update")]
    assert shown["drafts"][0]["url"].endswith("/browse/ORB-2")
    assert shown["drafts"][0]["diff"]["added"] >= 1
    assert "EPIC-1 → ORB-1" in shown["warnings"][0]
    assert tracker.posts == 2 and tracker.puts == ["ORB-2"]
    assert "повтор отвечает тем же платежом" in json.dumps(tracker.issues["ORB-2"], ensure_ascii=False)
    assert update["issues"]["status"] == "created"
    assert "обновлено 1" in update["messages"][0].content
    assert [i["key"] for i in update["issues"]["verification"]["verified"]] == ["ORB-2"]

    # И следующий ход с той же правкой уже ничего не отправляет.
    again = jira_graph.create_node(state_with_cards(fenced(edited())), THREAD)
    assert again["issues"]["status"] == "unchanged"


@responses.activate
def test_an_issue_edited_on_the_board_is_not_overwritten(jira_env, monkeypatch):
    tracker = Tracker()
    first_turn(monkeypatch)
    tracker.issues["ORB-2"]["summary"] = "Идемпотентность (уточнено командой)"
    monkeypatch.setattr(jira_graph, "interrupt", Operator(agree))

    update = jira_graph.create_node(state_with_cards(fenced(edited())), THREAD)

    assert tracker.puts == []
    assert tracker.issues["ORB-2"]["summary"] == "Идемпотентность (уточнено командой)"
    (conflict,) = update["issues"]["failed"]
    assert conflict["conflict"] is True and "изменена в Jira" in conflict["reason"]
    assert "не обновлена" in update["messages"][0].content


@responses.activate
def test_the_trackers_own_rewriting_is_not_taken_for_someone_elses_edit(jira_env, monkeypatch):
    """Трекер переписал тему при записи: сверка это видит, но следующая правка проходит."""

    def trimmed(fields: dict) -> dict:
        return {**fields, "summary": fields["summary"] + " [импорт]"}

    tracker = Tracker(rewrite=trimmed)
    update = first_turn(monkeypatch)
    assert [i["key"] for i in update["issues"]["verification"]["differs"]] == ["ORB-1", "ORB-2"]
    assert "сверка: ORB-1" in update["messages"][0].content
    monkeypatch.setattr(jira_graph, "interrupt", Operator(agree))

    jira_graph.create_node(state_with_cards(fenced(edited())), THREAD)

    assert tracker.puts == ["ORB-2"]


# --------------------------------------------------------------------------
# Jira: согласие привязано к содержимому
# --------------------------------------------------------------------------
@responses.activate
def test_an_answer_to_an_old_version_is_asked_again(jira_env, monkeypatch):
    tracker = Tracker()
    operator = Operator({"decision": "approved", "digest": "0" * 64}, agree)
    monkeypatch.setattr(jira_graph, "interrupt", operator)

    update = jira_graph.create_node(state_with_cards(), THREAD)

    assert len(operator.shown) == 2
    assert "другой версии" in operator.shown[1]["warnings"][0]
    assert update["issues"]["status"] == "created" and tracker.posts == 2


@responses.activate
def test_a_restart_after_a_crash_continues_the_approved_batch_without_asking(jira_env, monkeypatch):
    """
    Процесс упал после первой задачи. Узел повторяется с тем же ответом, а
    журнал уже другой: первая заведена, судьба второй неизвестна. Согласие на
    пачку покрывает её продолжение — и первая не заводится второй раз.
    """
    tracker = Tracker()
    replay: list[dict] = []

    def approve_once(payload):
        replay.append({"decision": "approved", "digest": payload["digest"]})
        return replay[0]

    monkeypatch.setattr(jira_graph, "interrupt", Operator(approve_once))
    original = jira_writer.create_issue
    calls = []

    def crashing(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("процесс упал посреди пачки")
        return original(*args, **kwargs)

    monkeypatch.setattr(jira_writer, "create_issue", crashing)
    with pytest.raises(RuntimeError):
        jira_graph.create_node(state_with_cards(), THREAD)
    monkeypatch.setattr(jira_writer, "create_issue", original)
    # LangGraph повторяет узел и отдаёт остановке прежний ответ.
    operator = Operator(replay[0])
    monkeypatch.setattr(jira_graph, "interrupt", operator)

    update = jira_graph.create_node(state_with_cards(), THREAD)

    assert len(operator.shown) == 1
    assert tracker.posts == 1
    assert [i["key"] for i in update["issues"]["created"]] == ["ORB-1"]
    assert [i["local"] for i in update["issues"]["unresolved"]] == ["TASK-1"]


@responses.activate
def test_naming_a_project_where_cards_exist_shows_the_real_plan_first(jira_env, monkeypatch):
    """Без проекта показаны «новые»; в названном проекте часть уже заведена — спросить заново."""
    tracker = Tracker()
    first_turn(monkeypatch)
    monkeypatch.delenv("JIRA_PROJECT_KEY")
    operator = Operator({"decision": "approved", "project": "orb"}, agree)
    monkeypatch.setattr(jira_graph, "interrupt", operator)

    jira_graph.create_node(state_with_cards(fenced(edited())), THREAD)

    first, second = operator.shown
    assert {d["action"] for d in first["drafts"]} == {"create"}
    assert "уже заведена" in second["warnings"][0]
    assert [(d["id"], d["action"]) for d in second["drafts"]] == [("TASK-1", "update")]
    assert tracker.posts == 2 and tracker.puts == ["ORB-2"]


@responses.activate
def test_a_refusal_and_the_proposal_it_refused_are_in_the_journal(jira_env, monkeypatch):
    Tracker()
    monkeypatch.setattr(jira_graph, "interrupt", Operator({"decision": "rejected", "reason": "рано"}))

    update = jira_graph.create_node(state_with_cards(), THREAD)

    assert update["issues"]["status"] == "rejected"
    (action,) = actions.store().list("service", thread="chain-1")
    assert action["status"] == "rejected" and action["kind"] == "jira"
    found = actions.store().get("service", action["id"])
    assert [(a["decision"], a["reason"]) for a in found["approvals"]] == [("rejected", "рано")]


@responses.activate
def test_the_journal_of_a_created_batch_names_each_operation(jira_env, monkeypatch):
    Tracker()
    first_turn(monkeypatch)

    (action,) = actions.store().list("service", thread="chain-1")
    found = actions.store().get("service", action["id"])

    assert found["status"] == "created"
    assert found["result"]["verification"]["verified"]
    issues = [op for op in found["journal"] if op["op"] == "issue"]
    assert [(op["key"], op["remote"], op["verified"]) for op in issues] == [
        ("EPIC-1", "ORB-1", actions.VERIFIED), ("TASK-1", "ORB-2", actions.VERIFIED)]
    assert all(op["label"].startswith(jira_journal.LABEL_PREFIX) for op in issues)


# --------------------------------------------------------------------------
# Jira: предложение вложенного прогона
# --------------------------------------------------------------------------
@responses.activate
def test_a_nested_proposal_does_not_rewrite_issues_of_the_callers_thread(jira_env, monkeypatch):
    tracker = Tracker()
    nested = {"configurable": {"thread_id": "inner", "nested": True}}

    def asked(payload):
        raise AssertionError("вложенный прогон не спрашивает")

    monkeypatch.setattr(jira_graph, "interrupt", asked)
    (offer,) = jira_graph.create_node(state_with_cards(), nested)["proposals"]
    created = proposals.apply(offer, {"decision": "approved", "id": offer["id"]}, thread="parent")
    assert created["status"] == "created" and tracker.posts == 2

    (again,) = jira_graph.create_node(state_with_cards(fenced(edited())), nested)["proposals"]
    result = proposals.apply(again, {"decision": "approved", "id": again["id"]}, thread="parent")

    assert result["status"] == "stale" and "TASK-1" in result["reason"]
    assert tracker.puts == [] and tracker.posts == 2


# --------------------------------------------------------------------------
# Публикация
# --------------------------------------------------------------------------
ANSWER = AIMessage(
    content="Экспорт больше 100000 строк приходит на почту.",
    response_metadata={"token_usage": {"prompt_cache_hit_tokens": 1, "prompt_cache_miss_tokens": 1,
                                       "completion_tokens": 1}},
)


class Once:
    def invoke(self, messages: list) -> AIMessage:
        return ANSWER


def published() -> list[Path]:
    directory = Path(cfg.publish_dir())
    return sorted(directory.glob("*.md")) if directory.exists() else []


def test_published_pages_are_read_back_and_journaled(monkeypatch):
    monkeypatch.setenv("MEMORY_ENABLED", "0")
    app = build_graph(llm=Once()).compile(checkpointer=InMemorySaver())

    result = app.invoke({"messages": [HumanMessage("Что с экспортом?")]}, config=THREAD)

    pages = result["publication"]["pages"]
    assert {page["verified"] for page in pages} == {actions.VERIFIED}
    assert len(result["publication"]["verification"]["verified"]) == len(roles.KEYS)
    (action,) = actions.store().list("service", thread="chain-1")
    found = actions.store().get("service", action["id"])
    assert found["kind"] == "publish" and found["status"] == "created"
    assert [op["verified"] for op in found["journal"]] == [actions.VERIFIED] * len(roles.KEYS)


def test_an_approval_of_another_plan_version_publishes_nothing(monkeypatch):
    monkeypatch.setenv("PUBLISH_REQUIRE_APPROVAL", "1")
    monkeypatch.setenv("MEMORY_ENABLED", "0")
    app = build_graph(llm=Once()).compile(checkpointer=InMemorySaver())
    stopped = app.invoke({"messages": [HumanMessage("Что с экспортом?")]}, config=THREAD)
    shown = stopped["__interrupt__"][0].value
    assert len(shown["digest"]) == 64 and shown["action_id"].startswith("ACT-")

    result = app.invoke(Command(resume={"decision": "approved", "digest": "0" * 64}), THREAD)

    assert result["publication"]["status"] == "stale"
    assert "другой версии" in result["publication"]["reason"]
    assert published() == []


def test_an_approval_with_the_shown_digest_publishes(monkeypatch):
    monkeypatch.setenv("PUBLISH_REQUIRE_APPROVAL", "1")
    monkeypatch.setenv("MEMORY_ENABLED", "0")
    app = build_graph(llm=Once()).compile(checkpointer=InMemorySaver())
    stopped = app.invoke({"messages": [HumanMessage("Что с экспортом?")]}, config=THREAD)
    digest = stopped["__interrupt__"][0].value["digest"]

    result = app.invoke(Command(resume={"decision": "approved", "digest": digest}), THREAD)

    assert result["publication"]["status"] == "created"
    assert len(published()) == len(roles.KEYS)
    (action,) = actions.store().list("service", thread="chain-1")
    assert actions.store().get("service", action["id"])["approvals"][0]["digest"] == digest


def test_a_file_changed_after_writing_is_reported(tmp_path):
    publisher = publishers.FilePublisher()
    result = publisher.publish("Экспорт", "Большой экспорт — на почту.")
    Path(result["path"]).write_text("# Экспорт\n\nподмена\n", encoding="utf-8")

    check = publishers.verify(publisher, result, "Экспорт", "Большой экспорт — на почту.")

    assert check["verified"] == actions.DIFFERS


# --------------------------------------------------------------------------
# Обновление документа
# --------------------------------------------------------------------------
def test_saving_a_new_version_needs_an_approval_of_that_version(setup):  # noqa: F811
    graph, state, _ = run_update(setup)
    shown = state["__interrupt__"][0].value
    assert len(shown["digest"]) == 64

    stale = graph.invoke(Command(resume={"decision": "approved", "digest": "0" * 64}), setup)

    assert stale["publication"]["status"] == "stale"
    assert publishers.documents() == []


def test_a_new_version_is_read_back_after_saving(setup):  # noqa: F811
    graph, state, _ = run_update(setup)
    digest = state["__interrupt__"][0].value["digest"]

    saved = graph.invoke(Command(resume={"decision": "approved", "digest": digest}), setup)

    assert saved["publication"]["status"] == "created"
    assert saved["publication"]["verified"] == actions.VERIFIED


def test_the_writer_and_the_verifier_agree_on_what_a_description_is():
    """ADF и строка Data Center с одним текстом — один отпечаток."""
    text = "Абзац один.\n\n- пункт\n- второй пункт"
    cloud = {"summary": "Тема", "description": jira_writer.text_to_adf(text)}
    server = {"summary": "Тема ", "description": text.replace("\n", "\r\n")}

    assert jira_writer.remote_hash(cloud) == jira_writer.remote_hash(
        {"summary": "Тема", "description": jira_writer.text_to_adf(text)})
    assert jira_writer.plain(server["description"]) == "Абзац один. - пункт - второй пункт"
    assert jira_writer.text_of(cloud["description"]).splitlines() == [
        "Абзац один.", "- пункт", "- второй пункт"]


def test_a_landed_older_edit_does_not_mark_the_new_edit_done(monkeypatch):
    previous = {"summary": "Прежняя", "description": "Текст прошлого плана"}
    wanted = {"summary": "Новая", "description": "Текст нового плана"}
    book = Mock()
    book.record.return_value = {"state": actions.UNKNOWN, "content_hash": "old-plan",
                                "remote_hash": jira_writer.remote_hash(previous)}
    monkeypatch.setattr(jira_writer, "_current", lambda *_: {**previous, "updated": "v2"})
    sent = []
    monkeypatch.setattr(jira_writer, "_get", lambda method, *a, **k: sent.append(method))

    outcome = jira_writer._revise(
        SimpleNamespace(local="R-1", summary="Новая"),
        {"key": "ORB-1", "url": "u"}, {"remote_hash": "before-old-put"},
        SimpleNamespace(api_path="/rest/api/3"),
        run="scope", book=book, fields=wanted, wanted="new-plan",
    )

    assert outcome == ("updated", "")
    assert sent == ["PUT"]
    assert book.rebase.call_args_list[-1].kwargs["content_hash"] == "new-plan"


def test_an_external_custom_field_edit_blocks_a_put(monkeypatch):
    fields = {"summary": "Тема", "description": "Текст", "customfield_10010": "новое"}
    book = Mock()
    book.record.return_value = {}
    monkeypatch.setattr(jira_writer, "_current", lambda *_: {
        "summary": "Тема", "description": "Текст", "updated": "v2",
    })
    sent = []
    monkeypatch.setattr(jira_writer, "_get", lambda method, *a, **k: sent.append(method))

    outcome = jira_writer._revise(
        SimpleNamespace(local="R-1", summary="Тема"),
        {"key": "ORB-1", "url": "u"},
        {"remote_hash": jira_writer.remote_hash(fields), "remote_version": "v1"}, None,
        run="scope", book=book, fields=fields, wanted="new-plan",
    )

    assert outcome[0] == "conflict"
    assert sent == []

    monkeypatch.setattr(jira_writer, "_current", lambda *_: {
        "summary": "Тема", "description": "Текст", "updated": "v1",
    })
    accepted = jira_writer._revise(
        SimpleNamespace(local="R-1", summary="Тема"),
        {"key": "ORB-1", "url": "u"},
        {"remote_hash": jira_writer.remote_hash(fields), "remote_version": "v1"},
        SimpleNamespace(api_path="/rest/api/3"), run="scope", book=book,
        fields=fields, wanted="new-plan",
    )
    assert accepted[0] == "updated"
    assert sent == ["PUT"]


def test_nested_publication_resumes_only_the_unwritten_page(monkeypatch, tmp_path):
    monkeypatch.setenv("PUBLISH_TARGET", "file")
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "1")
    monkeypatch.setenv("PUBLISH_DIR", str(tmp_path / "published"))
    monkeypatch.setenv("JIRA_JOURNAL_PATH", str(tmp_path / "actions.sqlite3"))
    publisher = publishers.FilePublisher()
    pages = [{"role": str(n), "title": f"Страница {n}", "document": f"Текст {n}",
              "digest": hashlib.sha256(f"Текст {n}".encode()).hexdigest()}
             for n in (1, 2)]
    plan = {"publisher": publisher, "pages": pages, "digest": "whole"}
    approved = publish_nodes.publish_commitment(plan)
    effect = publish_nodes.proposed_effect(plan, approved)
    recorder = actions.Recorder(publish_nodes.publish_action(approved, graph="prep", thread="parent"))
    recorder.decide({"decision": "approved", "digest": recorder.proposal["digest"]})
    publish_nodes.write_pages(publisher, pages[:1], recorder=recorder)

    result = publish_nodes.publish_proposed(
        effect, approval={"decision": "approved"}, thread="parent", graph="prep"
    )

    assert result["status"] != "stale"
    assert result["pages"][0]["recovered"] is True
    assert result["pages"][1]["status"] == "created"
    Path(result["pages"][0]["path"]).write_text("чужая правка", encoding="utf-8")
    retry = publish_nodes.publish_proposed(
        effect, approval={"decision": "approved"}, thread="parent", graph="prep"
    )
    assert retry["status"] == "stale"
