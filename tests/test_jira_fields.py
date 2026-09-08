import json
from urllib.parse import parse_qs, urlsplit

import pytest
import responses

from agent import jira, jira_fields, jira_forms, jira_plan, jira_writer

SCREEN = {
    "components": {
        "name": "Компоненты",
        "schema": {"type": "array"},
        "allowedValues": [{"id": "42", "name": "checkout"}],
        "required": True,
    },
    "customfield_10": {"name": "Story Points", "schema": {"type": "number"}},
    "customfield_11": {
        "name": "Acceptance Criteria",
        "schema": {
            "type": "string",
            "custom": "com.atlassian.jira.plugin.system.customfieldtypes:textarea",
        },
    },
}
ITEM = jira_plan.Item(
    local="TASK-1",
    type="Task",
    summary="Checkout",
    description="Implement checkout",
    component="checkout",
    estimate="3 SP",
    acceptance=("Idempotent",),
)


@pytest.mark.parametrize("api", ["/rest/api/2", "/rest/api/3"])
@responses.activate
def test_native_fields_are_sent_to_rest_and_removed_from_description(api):
    s = jira.Settings(base_url="https://jira.example.com", token="test", api_path=api)
    base = s.base_url + api
    responses.get(
        base + "/issue/createmeta/ORB/issuetypes", json={"values": [{"name": "Task", "id": "5"}]}
    )
    responses.get(base + "/issue/createmeta/ORB/issuetypes/5", json={"fields": SCREEN})
    responses.get(base + "/field", json=[])
    responses.post(base + "/issue", json={"key": "ORB-1"})
    result = jira_writer.create_issues(jira_plan.Plan(items=[ITEM]), "ORB", settings=s)
    payload = json.loads(
        next(call.request.body for call in responses.calls if call.request.method == "POST")
    )["fields"]
    assert payload["components"] == [{"id": "42"}]
    assert payload["customfield_10"] == 3
    if api.endswith("3"):
        assert payload["customfield_11"]["type"] == "doc"
    else:
        assert payload["customfield_11"] == "Idempotent"
    assert "Область" not in str(payload["description"])
    assert "Критерии" not in str(payload["description"])
    assert not result["warnings"]


@responses.activate
def test_form_uses_option_ids_and_keeps_full_copyable_document():
    s = jira.Settings(base_url="https://jira.example.com", token="test")
    base = s.base_url + s.api_path
    responses.get(base + "/serverInfo", json={"deploymentType": "Data Center"})
    responses.get(
        base + "/project/ORB", json={"id": "100", "issueTypes": [{"name": "Task", "id": "5"}]}
    )
    responses.get(base + "/myself", json={"name": "author"})
    responses.get(base + "/issue/createmeta/ORB/issuetypes/5", json={"fields": SCREEN})
    result = jira_forms.prepare(jira_plan.Plan(items=[ITEM]), "ORB", settings=s)
    card = result["drafts"][0]
    params = parse_qs(urlsplit(card["url"]).query)
    assert params["components"] == ["42"]
    assert params["customfield_10"] == ["3.0"]
    assert params["customfield_11"] == ["Idempotent"]
    assert params["description"] == [ITEM.description]
    assert card["document"] == ITEM.body()
    assert all(call.request.method == "GET" for call in responses.calls)


def test_ambiguous_estimates_and_unknown_components_stay_in_description():
    item = jira_plan.Item(
        local="T", type="Task", summary="Task", component="unknown", estimate="3-5 SP"
    )
    fields, mapped, warnings = jira_fields.map_item(item, SCREEN)
    assert fields == {}
    assert len(warnings) == 2
    assert "3-5 SP" in item.body(mapped=mapped)
    assert "unknown" in item.body(mapped=mapped)


@responses.activate
def test_create_metadata_reads_all_pages():
    s = jira.Settings(base_url="https://jira.example.com", token="test")
    path = s.base_url + s.api_path + "/issue/createmeta/ORB/issuetypes/5"
    responses.get(path, json={"values": [{"fieldId": "one", "name": "One"}], "total": 2})
    responses.get(path, json={"values": [{"fieldId": "two", "name": "Two"}], "total": 2})
    assert set(jira_fields.read("ORB", "5", s)) == {"one", "two"}
    assert parse_qs(urlsplit(responses.calls[1].request.url).query)["startAt"] == ["1"]


@responses.activate
def test_create_metadata_legacy_fallback():
    s = jira.Settings(base_url="https://jira.example.com", token="test")
    base = s.base_url + s.api_path + "/issue/createmeta"
    responses.get(base + "/ORB/issuetypes/5", status=404)
    responses.get(base, json={"projects": [{"issuetypes": [{"id": "5", "fields": SCREEN}]}]})
    assert jira_fields.read("ORB", "5", s) == SCREEN
