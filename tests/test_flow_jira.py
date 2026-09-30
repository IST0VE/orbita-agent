"""
Чтение доски для метрик потока на библиотеке `responses`.

`responses` роняет любой незарегистрированный запрос, поэтому тест, который
пошёл бы куда-то ещё, упадёт, а не сходит в чужой трекер. Проверяется то, в чём
Cloud и Data Center расходятся: постраничность поиска, история задачи сверх
потолка поиска, отказ канбан-доски на вопросе о спринтах.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
import responses

from agent import flow, flow_jira, jira

BASE = "https://jira.example.com"
SERVER = jira.Settings(base_url=BASE, token="pat", api_path="/rest/api/2", search_path="/search",
                       interval_s=0.0)
CLOUD = jira.Settings(base_url=BASE, token="tok", email="me@org.com", api_path="/rest/api/3",
                      interval_s=0.0)
FIELDS = flow.Fields(estimate="customfield_10002", sprint="customfield_10005")


def query(call) -> dict:
    return {key: values[0] for key, values in parse_qs(urlsplit(call.request.url).query).items()}


@responses.activate
def test_the_board_filter_loses_its_sort_to_be_wrapped():
    responses.get(f"{BASE}/rest/agile/1.0/board/42/configuration", json={
        "filter": {"id": "10500"},
        "estimation": {"field": {"fieldId": "customfield_10002", "displayName": "Story Points"}},
    })
    responses.get(f"{BASE}/rest/api/2/filter/10500",
                  json={"jql": "project = PAY AND type != Epic ORDER BY Rank ASC"})

    config = flow_jira.configuration(42, SERVER)

    assert config == {"filter_id": "10500", "estimate": "customfield_10002",
                      "estimate_name": "Story Points"}
    assert flow_jira.board_jql("10500", SERVER) == "project = PAY AND type != Epic"


@responses.activate
def test_the_sprint_field_is_found_by_its_type_not_by_its_name():
    responses.get(f"{BASE}/rest/api/2/field", json=[
        {"id": "customfield_10005", "name": "Спринт",
         "schema": {"custom": "com.pyxis.greenhopper.jira:gh-sprint"}},
        {"id": "customfield_10021", "name": "Flagged", "schema": {"custom": "multicheckboxes"}},
        {"id": "customfield_10002", "name": "Оценка", "schema": {"custom": "float"}},
    ])

    found = flow_jira.fields({"estimate": "customfield_10002", "estimate_name": ""}, SERVER)

    assert (found.sprint, found.sprint_name) == ("customfield_10005", "Спринт")
    assert found.flagged == "customfield_10021"
    assert found.estimate_name == "Оценка"


@responses.activate
def test_a_kanban_board_has_no_sprints_and_that_is_not_an_error():
    responses.get(f"{BASE}/rest/agile/1.0/board/7/sprint", status=400,
                  json={"errorMessages": ["The board does not support sprints"]})

    assert flow_jira.sprints(7, SERVER) == []


@responses.activate
def test_sprints_are_read_page_by_page():
    responses.get(f"{BASE}/rest/agile/1.0/board/42/sprint", json={
        "isLast": False,
        "values": [{"id": 1, "name": "S1", "state": "closed", "startDate": "2026-06-01T10:00:00.000Z",
                    "completeDate": "2026-06-14T10:00:00.000Z"}],
    })
    responses.get(f"{BASE}/rest/agile/1.0/board/42/sprint", json={
        "isLast": True, "values": [{"id": 2, "name": "S2", "state": "ACTIVE"}],
    })

    found = flow_jira.sprints(42, SERVER)

    assert [item["sprint_id"] for item in found] == [1, 2]
    assert found[1]["state"] == "active"
    assert query(responses.calls[1])["startAt"] == "1"


def page(keys: list[str], **extra) -> dict:
    return {"issues": [{"key": key, "fields": {}} for key in keys], **extra}


@responses.activate
def test_data_center_search_pages_by_start_at_and_stops_at_the_total():
    url = f"{BASE}/rest/api/2/search"
    responses.get(url, json=page(["A-1", "A-2"], total=3))
    responses.get(url, json=page(["A-3"], total=3))

    found = [item["key"] for item in flow_jira.search("project = A", FIELDS, SERVER, limit=100)]

    assert found == ["A-1", "A-2", "A-3"]
    first, second = query(responses.calls[0]), query(responses.calls[1])
    assert first["expand"] == "changelog"
    assert "customfield_10005" in first["fields"] and "assignee" not in first["fields"]
    assert (first["startAt"], second["startAt"]) == ("0", "2")


@responses.activate
def test_cloud_search_pages_by_token():
    url = f"{BASE}/rest/api/3/search/jql"
    responses.get(url, json=page(["A-1"], nextPageToken="t2"))
    responses.get(url, json=page(["A-2"], isLast=True))

    found = [item["key"] for item in flow_jira.search("project = A", FIELDS, CLOUD, limit=100)]

    assert found == ["A-1", "A-2"]
    assert "nextPageToken" not in query(responses.calls[0])
    assert query(responses.calls[1])["nextPageToken"] == "t2"


@responses.activate
def test_the_search_stops_at_its_limit():
    responses.get(f"{BASE}/rest/api/2/search", json=page(["A-1", "A-2", "A-3"], total=50))

    found = list(flow_jira.search("project = A", FIELDS, SERVER, limit=2))

    assert len(found) == 2
    assert len(responses.calls) == 1


@pytest.mark.parametrize(
    ("log", "cut"),
    [({"total": 150, "histories": [{}] * 100}, True), ({"total": 3, "histories": [{}] * 3}, False),
     ({"histories": [{}]}, False)],
)
def test_a_changelog_cut_by_the_search_is_recognised(log, cut):
    assert flow_jira.truncated({"changelog": log}) is cut


@responses.activate
def test_the_full_changelog_is_paged_on_cloud_and_expanded_on_data_center():
    responses.get(f"{BASE}/rest/api/3/issue/A-1/changelog",
                  json={"isLast": False, "values": [{"id": "1"}]})
    responses.get(f"{BASE}/rest/api/3/issue/A-1/changelog",
                  json={"isLast": True, "values": [{"id": "2"}]})
    responses.get(f"{BASE}/rest/api/2/issue/A-1",
                  json={"changelog": {"histories": [{"id": "1"}, {"id": "2"}, {"id": "3"}]}})

    assert [item["id"] for item in flow_jira.full_changelog("A-1", CLOUD)] == ["1", "2"]
    assert len(flow_jira.full_changelog("A-1", SERVER)) == 3
