"""
R6: повтор узла не заводит задачи второй раз.

Узел LangGraph выполняется дважды чаще, чем кажется: упал процесс между
принятым POST и сохранением результата, оборвалась сеть на середине пачки,
оператор возобновил тред из checkpoint. До журнала операций повтор просто
заводил задачи заново — тихо, с уведомлениями всей доске.

Три случая, которые проверяются здесь, и они разные по последствиям: сбой
до отправки (заводить можно), сбой после принятого POST (заводить нельзя,
надо найти заведённое) и сбой посередине пачки (доделать остаток).
"""

from __future__ import annotations

import json

import pytest
import responses

from agent import jira, jira_journal, jira_plan, jira_writer

BASE = "https://jira.example.com"
API = "/rest/api/3"
CLOUD = jira.Settings(base_url=BASE, token="tok", email="me@org.com", api_path=API, timeout_s=5.0)
RUN = "run-1"


@pytest.fixture
def journal(tmp_path) -> jira_journal.Journal:
    return jira_journal.Journal(tmp_path / "operations.sqlite3")


def plan_of(*names: str) -> jira_plan.Plan:
    issues = [{"id": name, "type": "Task", "summary": f"Задача {name}"} for name in names]
    return jira_plan.parse(json.dumps({"issues": issues}, ensure_ascii=False))


def linked_plan() -> jira_plan.Plan:
    return jira_plan.parse(json.dumps({"issues": [
        {"id": "T-1", "type": "Task", "summary": "Первая"},
        {"id": "T-2", "type": "Task", "summary": "Вторая", "depends_on": ["T-1"]},
    ]}, ensure_ascii=False))


def register_types() -> None:
    responses.add(
        responses.GET,
        f"{BASE}{API}/issue/createmeta/ORB/issuetypes",
        json={"values": [{"name": "Task"}, {"name": "Epic"}]},
        status=200,
    )


def posts() -> list:
    return [call for call in responses.calls
            if call.request.method == "POST" and call.request.url.endswith("/issue")]


def links() -> list:
    return [call for call in responses.calls if call.request.url.endswith("/issueLink")]


def sent(index: int) -> dict:
    raw = posts()[index].request.body
    return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)["fields"]


# --------------------------------------------------------------------------
# Журнал сам по себе
# --------------------------------------------------------------------------
def test_the_operation_label_is_the_same_on_a_repeat():
    first = jira_journal.label_for(RUN, "issue", "T-1")

    assert first == jira_journal.label_for(RUN, "issue", "T-1")
    assert first.startswith(jira_journal.LABEL_PREFIX)
    assert first != jira_journal.label_for(RUN, "issue", "T-2")
    assert first != jira_journal.label_for("другой-прогон", "issue", "T-1")


def test_a_journal_survives_a_new_process(tmp_path):
    path = tmp_path / "operations.sqlite3"
    jira_journal.Journal(path).begin(RUN, "issue", "T-1")

    record = jira_journal.Journal(path).record(RUN, "issue", "T-1")

    assert record["state"] == jira_journal.PENDING
    assert record["label"] == jira_journal.label_for(RUN, "issue", "T-1")


def test_the_run_key_follows_the_plan():
    same = jira_journal.run_key("t-1", "ORB", plan_of("T-1").fingerprint())
    again = jira_journal.run_key("t-1", "ORB", plan_of("T-1").fingerprint())
    other = jira_journal.run_key("t-1", "ORB", plan_of("T-1", "T-2").fingerprint())

    assert same == again != other


# --------------------------------------------------------------------------
# Сбой до отправки
# --------------------------------------------------------------------------
@responses.activate
def test_a_failure_before_the_post_lets_the_next_run_create_it(journal):
    register_types()
    responses.add(responses.POST, f"{BASE}{API}/issue", json={"key": "ORB-1"}, status=201)

    result = jira_writer.create_issues(
        plan_of("T-1"), "ORB", settings=CLOUD, run=RUN, journal=journal
    )

    assert [issue["key"] for issue in result["created"]] == ["ORB-1"]
    assert len(posts()) == 1
    assert journal.record(RUN, "issue", "T-1")["state"] == jira_journal.COMPLETED


@responses.activate
def test_the_operation_label_travels_with_the_issue(journal):
    register_types()
    responses.add(responses.POST, f"{BASE}{API}/issue", json={"key": "ORB-1"}, status=201)

    jira_writer.create_issues(plan_of("T-1"), "ORB", settings=CLOUD, run=RUN, journal=journal)

    assert jira_journal.label_for(RUN, "issue", "T-1") in sent(0)["labels"]


# --------------------------------------------------------------------------
# Сбой после принятого POST
# --------------------------------------------------------------------------
@responses.activate
def test_an_accepted_post_is_never_repeated_blindly(journal):
    """Журнал помнит отправку; трекер по метке отдаёт уже заведённую задачу."""
    register_types()
    journal.begin(RUN, "issue", "T-1")
    responses.add(
        responses.GET,
        f"{BASE}{API}/search/jql",
        json={"issues": [{"key": "ORB-7", "fields": {"summary": "Задача T-1"}}]},
        status=200,
    )

    result = jira_writer.create_issues(
        plan_of("T-1"), "ORB", settings=CLOUD, run=RUN, journal=journal
    )

    assert posts() == []
    assert [issue["key"] for issue in result["created"]] == ["ORB-7"]
    assert result["created"][0]["recovered"]
    assert journal.record(RUN, "issue", "T-1")["remote"] == "ORB-7"


@responses.activate
def test_a_lost_post_that_never_landed_is_sent_once(journal):
    """Сверка прошла и ничего не нашла: значит, POST до трекера не доехал."""
    register_types()
    journal.begin(RUN, "issue", "T-1")
    responses.add(responses.GET, f"{BASE}{API}/search/jql", json={"issues": []}, status=200)
    responses.add(responses.POST, f"{BASE}{API}/issue", json={"key": "ORB-1"}, status=201)

    result = jira_writer.create_issues(
        plan_of("T-1"), "ORB", settings=CLOUD, run=RUN, journal=journal
    )

    assert len(posts()) == 1
    assert [issue["key"] for issue in result["created"]] == ["ORB-1"]


@responses.activate
def test_an_unverifiable_result_is_shown_not_retried(journal):
    """Сверка невозможна: решение за человеком, вслепую не повторяем."""
    register_types()
    journal.begin(RUN, "issue", "T-1")
    responses.add(responses.GET, f"{BASE}{API}/search/jql", status=503, json={"message": "down"})

    result = jira_writer.create_issues(
        plan_of("T-1"), "ORB", settings=CLOUD, run=RUN, journal=journal
    )

    assert posts() == []
    assert result["created"] == []
    assert result["status"] == "unknown"
    assert result["unresolved"][0]["local"] == "T-1"
    assert any("сверка не удалась" in text for text in result["warnings"])
    assert journal.record(RUN, "issue", "T-1")["state"] == jira_journal.UNKNOWN


@responses.activate
def test_an_explicit_refusal_may_be_retried(journal):
    """Трекер ответил 400: запрос дошёл и был отклонён — повтор безопасен."""
    register_types()
    responses.add(responses.POST, f"{BASE}{API}/issue", status=400, json={"message": "нет типа"})

    first = jira_writer.create_issues(
        plan_of("T-1"), "ORB", settings=CLOUD, run=RUN, journal=journal
    )
    assert first["status"] == "failed"
    assert journal.record(RUN, "issue", "T-1")["state"] == jira_journal.FAILED

    responses.reset()
    register_types()
    responses.add(responses.POST, f"{BASE}{API}/issue", json={"key": "ORB-1"}, status=201)
    second = jira_writer.create_issues(
        plan_of("T-1"), "ORB", settings=CLOUD, run=RUN, journal=journal
    )

    assert [issue["key"] for issue in second["created"]] == ["ORB-1"]


# --------------------------------------------------------------------------
# Сбой посередине пачки
# --------------------------------------------------------------------------
@responses.activate
def test_a_repeat_finishes_the_batch_without_duplicating_its_start(journal):
    register_types()
    responses.add(responses.POST, f"{BASE}{API}/issue", json={"key": "ORB-1"}, status=201)
    responses.add(responses.POST, f"{BASE}{API}/issue", status=503, json={"message": "down"})

    plan = plan_of("T-1", "T-2")
    first = jira_writer.create_issues(plan, "ORB", settings=CLOUD, run=RUN, journal=journal)
    assert [issue["key"] for issue in first["created"]] == ["ORB-1"]
    assert first["failed"][0]["local"] == "T-2"

    responses.reset()
    register_types()
    responses.add(responses.POST, f"{BASE}{API}/issue", json={"key": "ORB-2"}, status=201)
    second = jira_writer.create_issues(plan, "ORB", settings=CLOUD, run=RUN, journal=journal)

    # Первая карточка не отправлена заново, вторая доведена до конца.
    assert len(posts()) == 1
    assert [issue["key"] for issue in second["created"]] == ["ORB-1", "ORB-2"]
    assert second["created"][0]["recovered"] and not second["created"][1].get("recovered")


@responses.activate
def test_a_parent_recovered_from_the_journal_still_parents_its_child(journal):
    """Восстанавливаются не только задачи: ребёнок уезжает с настоящим родителем."""
    register_types()
    journal.begin(RUN, "issue", "EPIC-1")
    journal.finish(RUN, "issue", "EPIC-1", remote="ORB-1", url=f"{BASE}/browse/ORB-1")
    responses.add(responses.POST, f"{BASE}{API}/issue", json={"key": "ORB-2"}, status=201)

    plan = jira_plan.parse(json.dumps({"issues": [
        {"id": "EPIC-1", "type": "Epic", "summary": "Родитель"},
        {"id": "T-1", "type": "Task", "summary": "Ребёнок", "parent": "EPIC-1"},
    ]}, ensure_ascii=False))
    result = jira_writer.create_issues(plan, "ORB", settings=CLOUD, run=RUN, journal=journal)

    assert len(posts()) == 1
    assert sent(0)["parent"] == {"key": "ORB-1"}
    assert [issue["key"] for issue in result["created"]] == ["ORB-1", "ORB-2"]


@responses.activate
def test_a_completed_link_is_not_created_twice(journal):
    register_types()
    responses.add(responses.POST, f"{BASE}{API}/issue", json={"key": "ORB-1"}, status=201)
    responses.add(responses.POST, f"{BASE}{API}/issue", json={"key": "ORB-2"}, status=201)
    responses.add(responses.POST, f"{BASE}{API}/issueLink", json={}, status=201)

    plan = linked_plan()
    jira_writer.create_issues(plan, "ORB", settings=CLOUD, run=RUN, journal=journal)
    assert len(links()) == 1
    assert journal.record(RUN, "link", "ORB-1->ORB-2")["state"] == jira_journal.COMPLETED

    responses.reset()
    register_types()
    second = jira_writer.create_issues(plan, "ORB", settings=CLOUD, run=RUN, journal=journal)

    assert links() == []
    assert second["status"] == "created"


@responses.activate
def test_a_pending_link_is_checked_before_it_is_repeated(journal):
    register_types()
    for local, key in (("T-1", "ORB-1"), ("T-2", "ORB-2")):
        journal.begin(RUN, "issue", local)
        journal.finish(RUN, "issue", local, remote=key, url=f"{BASE}/browse/{key}")
    journal.begin(RUN, "link", "ORB-1->ORB-2")
    responses.add(
        responses.GET,
        f"{BASE}{API}/issue/ORB-1",
        json={"fields": {"issuelinks": [{"outwardIssue": {"key": "ORB-2"}}]}},
        status=200,
    )

    jira_writer.create_issues(linked_plan(), "ORB", settings=CLOUD, run=RUN, journal=journal)

    assert links() == []
    assert journal.record(RUN, "link", "ORB-1->ORB-2")["state"] == jira_journal.COMPLETED


@responses.activate
def test_without_a_run_key_the_old_behaviour_stays(journal):
    """Вызов без ключа прогона ничего не восстанавливает и ничего не пишет."""
    register_types()
    responses.add(responses.POST, f"{BASE}{API}/issue", json={"key": "ORB-1"}, status=201)

    jira_writer.create_issues(plan_of("T-1"), "ORB", settings=CLOUD)

    assert len(posts()) == 1
    assert journal.records(RUN) == []
