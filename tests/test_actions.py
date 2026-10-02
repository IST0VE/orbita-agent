"""
Единый порядок внешних действий: отпечаток, согласие и журнал.

Проверяется то, на чём держится весь порядок. Согласие привязано к
содержимому: ответ по другой версии предложения — не согласие, а решение без
отпечатка перед записью не проходит. Журнал один на все виды действий и
отвечает одинаково в файле и в Postgres. Чужие действия не видны.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pytest
from langchain_core.messages import AIMessage
from starlette.testclient import TestClient

from agent import actions, api, db, jira_graph, jira_plan, security
from agent.security import Principal

OPERATIONS = [{"op": "issue", "key": "TASK-1", "action": "create", "hash": "a1", "title": "Ключ"}]


def proposal(thread: str = "t-1", operations=None, owner: str = "sub-anna") -> dict:
    return actions.seal(
        "jira",
        graph="jira",
        target={"system": "jira", "project": "ORB"},
        operations=OPERATIONS if operations is None else operations,
        thread=thread,
        owner=owner,
        scope=actions.run_key(thread, "ORB"),
    )


# --------------------------------------------------------------------------
# Отпечаток и согласие
# --------------------------------------------------------------------------
def test_the_digest_follows_the_content_and_the_id_follows_the_thread():
    first, second = proposal("t-1"), proposal("t-2")
    edited = proposal("t-1", [{**OPERATIONS[0], "hash": "b2"}])

    assert first["digest"] == second["digest"]
    assert first["id"] != second["id"]
    assert edited["digest"] != first["digest"]
    assert first["id"].startswith("ACT-")


def test_the_stop_carries_the_digest_back_and_forth():
    shown = actions.shown({"action": "jira"}, proposal())

    assert shown["digest"] == proposal()["digest"]
    assert shown["action_id"] == proposal()["id"]


def test_an_answer_to_another_version_is_not_an_approval():
    current = proposal()["digest"]

    decision = actions.bind({"decision": "approved", "digest": "0" * 64}, current)

    assert decision["decision"] == actions.STALE
    assert actions.stale(decision, current)


def test_a_refusal_stays_a_refusal_whatever_it_was_given_to():
    decision = actions.bind({"decision": "rejected", "digest": "0" * 64}, proposal()["digest"])

    assert decision["decision"] == "rejected"
    assert actions.stale(decision, proposal()["digest"]) == ""


def test_an_answer_without_a_digest_belongs_to_what_this_stop_showed():
    """Studio и старый интерфейс отвечают `true`: согласие — на показанное здесь."""
    current = proposal()["digest"]

    for answer in (True, "да", {"decision": "approved"}):
        decision = actions.bind(answer, current)
        assert decision == {"decision": "approved", "reason": "", "digest": current}
        assert actions.stale(decision, current) == ""


def test_the_drafts_decision_survives_binding():
    decision = actions.bind({"decision": "drafts"}, proposal()["digest"])

    assert decision["decision"] == "drafts"


def test_an_approval_is_void_for_other_content_or_without_a_digest():
    current = proposal()["digest"]

    assert "изменилось" in actions.stale({"decision": "approved", "digest": "другой"}, current)
    assert "не привязано" in actions.stale({"decision": "approved"}, current)


def test_only_what_will_be_sent_is_pending():
    ops = [{**OPERATIONS[0], "action": "unchanged"}, {**OPERATIONS[0], "key": "TASK-2"}]

    assert [op["key"] for op in actions.pending(proposal(operations=ops))] == ["TASK-2"]


def test_the_result_is_kept_without_texts():
    kept = actions.brief({
        "status": "created",
        "document": "весь документ",
        "created": [{"local": "T-1", "key": "ORB-1", "url": "u", "summary": "тема", "remote_hash": "x"}],
    })

    assert kept == {"status": "created", "created": [{"local": "T-1", "key": "ORB-1", "url": "u"}]}


# --------------------------------------------------------------------------
# Хранилище в файле
# --------------------------------------------------------------------------
@pytest.fixture
def journal(tmp_path) -> actions.SqliteActions:
    return actions.SqliteActions(tmp_path / "actions.sqlite3")


def test_an_action_with_its_approval_and_operations_is_one_record(journal):
    item = proposal()
    journal.save_action(item, actions.PROPOSED)
    journal.add_approval(item["id"], {"decision": "approved", "digest": item["digest"]}, "sub-anna")
    book = actions.Bound(journal, item["id"])
    book.begin(item["scope"], "issue", "TASK-1", content_hash="a1", title="Ключ")
    book.finish(item["scope"], "issue", "TASK-1", remote="ORB-1", url="u", remote_hash="r1")
    journal.set_status(item["id"], "created", {"status": "created", "document": "текст"})

    found = journal.get("sub-anna", item["id"])

    assert found["status"] == "created"
    assert found["result"] == {"status": "created"}
    assert found["operations"] == OPERATIONS
    assert [a["decision"] for a in found["approvals"]] == ["approved"]
    assert found["approvals"][0]["digest"] == item["digest"]
    (operation,) = found["journal"]
    assert (operation["remote"], operation["content_hash"], operation["remote_hash"]) == (
        "ORB-1", "a1", "r1")
    assert operation["action_id"] == item["id"]


def test_the_same_proposal_again_changes_the_status_not_the_record(journal):
    item = proposal()
    journal.save_action(item, actions.PROPOSED)
    journal.save_action(item, "created")

    listed = journal.list("sub-anna")

    assert [(a["id"], a["status"]) for a in listed] == [(item["id"], "created")]
    assert listed[0]["operations"] == 1


def test_someone_elses_action_is_not_there(journal):
    item = proposal()
    journal.save_action(item, actions.PROPOSED)

    assert journal.list("sub-tim") == []
    assert journal.get("sub-tim", item["id"]) is None


def test_a_listing_can_be_narrowed_to_one_thread(journal):
    journal.save_action(proposal("t-1"), actions.PROPOSED)
    journal.save_action(proposal("t-2"), actions.PROPOSED)

    assert [a["thread_id"] for a in journal.list("sub-anna", thread="t-2")] == ["t-2"]


def test_finishing_keeps_the_fingerprints_written_at_the_start(journal):
    """Задача, найденная сверкой по метке, не теряет содержимое, с которым уходила."""
    journal.begin("run", "issue", "T-1", content_hash="a1")
    journal.finish("run", "issue", "T-1", remote="ORB-7", url="u")

    assert journal.record("run", "issue", "T-1")["content_hash"] == "a1"


def test_a_check_after_writing_becomes_the_new_baseline(journal):
    journal.begin("run", "issue", "T-1", content_hash="a1")
    journal.finish("run", "issue", "T-1", remote="ORB-7", remote_hash="sent")
    journal.checked("run", "issue", "T-1", actions.DIFFERS, detail="тема другая",
                    remote_hash="stored", remote_version="v7")

    record = journal.record("run", "issue", "T-1")

    assert (record["verified"], record["remote_hash"], record["remote_version"]) == (
        actions.DIFFERS, "stored", "v7")


def test_a_new_start_forgets_the_last_check(journal):
    journal.begin("run", "update", "T-1", content_hash="a1")
    journal.checked("run", "update", "T-1", actions.VERIFIED)
    journal.begin("run", "update", "T-1", content_hash="b2")

    record = journal.record("run", "update", "T-1")

    assert (record["state"], record["verified"], record["content_hash"]) == (
        actions.PENDING, "", "b2")


def test_an_unwritable_journal_is_named_not_crashed(tmp_path):
    blocker = tmp_path / "файл"
    blocker.write_text("не папка", encoding="utf-8")

    with pytest.raises(actions.JournalUnavailable):
        actions.SqliteActions(blocker / "actions.sqlite3")


def test_without_a_database_the_journal_is_the_file(monkeypatch, tmp_path):
    monkeypatch.setenv("JIRA_JOURNAL_PATH", str(tmp_path / "j.sqlite3"))

    found = actions.store()

    assert isinstance(found, actions.SqliteActions)
    assert found.path == tmp_path / "j.sqlite3"


def test_with_a_database_the_journal_is_postgres(monkeypatch):
    monkeypatch.setenv("POSTGRES_URI", "postgresql://u:p@db:5432/orbita")

    assert isinstance(actions.store(), actions.PostgresActions)


def test_the_old_jira_journal_prevents_a_duplicate_after_upgrade(monkeypatch, tmp_path):
    path = tmp_path / "old.sqlite3"
    monkeypatch.setenv("JIRA_JOURNAL_PATH", str(path))
    plan = jira_plan.parse('```json\n{"issues": [{"id": "TASK-1", "type": "Task", '
                           '"summary": "Уже заведена"}]}\n```')
    old_run = actions.run_key("thread", "ORB", plan.fingerprint())
    label = actions.label_for(old_run, "issue", "TASK-1")
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE operations (run TEXT, kind TEXT, local TEXT, state TEXT, "
                     "label TEXT, remote TEXT, url TEXT, detail TEXT, updated REAL)")
        conn.execute("INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (old_run, "issue", "TASK-1", "completed", label, "ORB-7", "u", "", 1.0))
    journal = actions.SqliteActions(path)
    monkeypatch.setattr(jira_graph, "_base_url", lambda: "")

    offered = jira_graph._proposal(plan, "ORB", "thread", "", journal)
    record = journal.record(actions.run_key("thread", "ORB"), "issue", "TASK-1")

    assert offered["operations"][0]["action"] == "unchanged"
    assert record["remote"] == "ORB-7"
    assert record["label"] == label


def test_a_changed_plan_recovers_the_previous_plan_from_chat_history(monkeypatch, tmp_path):
    path = tmp_path / "old.sqlite3"
    monkeypatch.setenv("JIRA_JOURNAL_PATH", str(path))
    old_text = '```json\n{"issues": [{"id": "TASK-1", "type": "Task", "summary": "Старая"}]}\n```'
    new_text = '```json\n{"issues": [{"id": "TASK-1", "type": "Task", "summary": "Новая"}]}\n```'
    old = jira_plan.parse(old_text)
    old_run = actions.run_key("thread", "ORB", old.fingerprint())
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE operations (run TEXT, kind TEXT, local TEXT, state TEXT, "
                     "label TEXT, remote TEXT, url TEXT, detail TEXT, updated REAL)")
        conn.execute("INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (old_run, "issue", "TASK-1", "completed",
                      actions.label_for(old_run, "issue", "TASK-1"), "ORB-7", "u", "", 1.0))
    journal = actions.SqliteActions(path)
    monkeypatch.setattr(jira_graph, "_base_url", lambda: "")

    jira_graph._migrate_legacy_history([AIMessage(content=old_text)], "ORB", "thread", "",
                                       journal)
    offered = jira_graph._proposal(jira_plan.parse(new_text), "ORB", "thread", "", journal)

    assert offered["operations"][0]["action"] == "update"
    assert offered["operations"][0]["remote"] == "ORB-7"


# --------------------------------------------------------------------------
# Запись действия: терпимая и строгая
# --------------------------------------------------------------------------
class Broken:
    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise actions.JournalUnavailable("база лежит")

        return fail


def test_a_tolerant_recorder_keeps_the_reason_and_goes_on():
    recorder = actions.Recorder(proposal(), journal=Broken())

    recorder.propose()
    recorder.decide({"decision": "approved"})
    recorder.done("created", {})

    assert recorder.error == "база лежит"


def test_a_strict_recorder_stops_the_write():
    recorder = actions.Recorder(proposal(), strict=True, journal=Broken())

    with pytest.raises(actions.JournalUnavailable):
        recorder.propose()


# --------------------------------------------------------------------------
# Тот же SQL в Postgres
# --------------------------------------------------------------------------
class Cursor:
    description = None

    def fetchall(self):
        return []


class Connection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params: tuple = ()):
        self.statements.append((sql, params))
        return Cursor()

    @contextmanager
    def transaction(self):
        yield


def test_every_statement_has_as_many_values_as_placeholders(monkeypatch):
    conn = Connection()

    @contextmanager
    def connection():
        yield conn

    monkeypatch.setattr(db, "connection", connection)
    store = actions.PostgresActions()
    item = proposal()
    store.save_action(item, actions.PROPOSED)
    store.set_status(item["id"], "created", {"status": "created"})
    store.set_status(item["id"], "stale")
    store.add_approval(item["id"], {"decision": "approved", "digest": item["digest"]}, "anna")
    store.begin("run", "issue", "T-1", content_hash="a1", action=item["id"])
    store.finish("run", "issue", "T-1", remote="ORB-1", remote_hash="r")
    store.fail("run", "issue", "T-1", "400")
    store.unresolved("run", "issue", "T-1", "таймаут")
    store.checked("run", "issue", "T-1", actions.VERIFIED, remote_hash="r")
    store.rebase("run", "issue", "T-1", content_hash="b", remote_hash="s")
    store.record("run", "issue", "T-1")
    store.list("anna", thread="t-1")
    store.get("anna", item["id"])

    assert len(conn.statements) == 13
    for sql, params in conn.statements:
        assert "?" not in sql
        assert sql.count("%s") == len(params), sql


def test_a_database_failure_becomes_a_journal_failure(monkeypatch):
    @contextmanager
    def connection():
        raise db.DatabaseUnavailable("база Orbita недоступна")
        yield

    monkeypatch.setattr(db, "connection", connection)

    with pytest.raises(actions.JournalUnavailable, match="недоступна"):
        actions.PostgresActions().record("run", "issue", "T-1")


# --------------------------------------------------------------------------
# API: только свои
# --------------------------------------------------------------------------
@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("API_ADMIN_TOKEN", "test-only-admin-token-with-32-characters")
    users = {"anna": Principal("sub-anna", "anna"), "tim": Principal("sub-tim", "tim")}
    checked = security.authenticate
    monkeypatch.setattr(
        security,
        "authenticate",
        lambda headers: users.get(headers.get("authorization", "").removeprefix("Bearer "))
        or checked(headers),
    )
    actions.store().save_action(proposal(), actions.PROPOSED)
    return TestClient(api.app)


def test_the_owner_sees_the_action_with_its_journal(client):
    listed = client.get("/api/actions", headers={"Authorization": "Bearer anna"})
    one = client.get(f"/api/actions/{proposal()['id']}", headers={"Authorization": "Bearer anna"})

    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["actions"]] == [proposal()["id"]]
    assert one.json()["digest"] == proposal()["digest"]
    assert one.json()["approvals"] == [] and one.json()["journal"] == []


def test_an_action_of_another_person_is_not_found(client):
    listed = client.get("/api/actions", headers={"Authorization": "Bearer tim"})
    one = client.get(f"/api/actions/{proposal()['id']}", headers={"Authorization": "Bearer tim"})

    assert listed.json()["actions"] == []
    assert one.status_code == 404


def test_actions_need_a_login(client):
    assert client.get("/api/actions").status_code == 401
