"""Native review handoff must never publish or create Jira issues implicitly."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest
import responses

from agent import confluence, jira, jira_forms, jira_plan, nodes, publishers, render, roles


@pytest.mark.parametrize("version", ["v1", "v2"])
@responses.activate
def test_confluence_draft_contains_full_body_and_reuses_operator_edits(version):
    s = confluence.Settings(
        base_url="https://wiki.example.com/wiki",
        token="test",
        space_key="DOC",
        space_id="55",
        parent_id="99",
        api_path="/rest/api/content" if version == "v1" else "/api/v2",
        api_version=version,
    )
    url = s.base_url + s.api_path + ("/pages" if version == "v2" else "")
    body = "<p>Полный текст архитектуры</p>" * 3000
    responses.get(url, json={"results": []})
    responses.post(url, json={"id": "12345", "status": "draft"})
    prop_url = url + "/12345" + ("/properties" if version == "v2" else "/property/share-id")
    responses.get(
        prop_url,
        json={"results": [{"key": "share-id", "value": "shared"}]}
        if version == "v2"
        else {"value": "shared"},
    )
    first = confluence.create_draft("Архитектура", body, s)
    sent = json.loads(responses.calls[1].request.body)
    assert sent["status"] == "draft"
    assert (sent["body"]["value"] if version == "v2" else sent["body"]["storage"]["value"]) == body
    assert (sent["parentId"] if version == "v2" else sent["ancestors"][0]["id"]) == "99"
    assert parse_qs(urlsplit(first["url"]).query) == {
        "draftId": ["12345"],
        "draftShareId": ["shared"],
    }
    responses.replace(
        responses.GET,
        url,
        json={
            "results": [
                {"id": "12345", "status": "draft", "title": sent["title"], "body": "operator edits"}
            ]
        },
    )
    second = confluence.create_draft("Архитектура", body, s)
    assert second["url"] == first["url"]
    assert sum(call.request.method == "POST" for call in responses.calls) == 1
    assert not any(call.request.method == "PUT" for call in responses.calls)


@responses.activate
def test_draft_refusal_never_falls_back_to_publication():
    s = confluence.Settings(base_url="https://wiki.example.com", token="test", space_key="DOC")
    url = s.base_url + s.api_path
    responses.get(url, json={"results": []})
    responses.post(url, status=400, json={"message": "draft unsupported"})
    with pytest.raises(confluence.ConfluenceError, match="draft unsupported"):
        confluence.create_draft("Title", "<p>Body</p>", s)
    assert len(responses.calls) == 2


def test_publish_node_hands_off_drafts_and_keeps_partial_results(monkeypatch):
    monkeypatch.setenv("PUBLISH_REQUIRE_APPROVAL", "1")
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "1")
    monkeypatch.setenv("CONFLUENCE_PUBLISH_MODE", "each")

    class Publisher:
        name = "confluence"
        renderer = render.STORAGE

        def missing(self):
            return []

        def publish(self, *_):
            pytest.fail("draft handoff must not publish")

    monkeypatch.setattr(publishers, "current", Publisher)
    calls = []

    def create(title, body, *, draft_key):
        calls.append(body)
        if len(calls) == 2:
            raise confluence.ConfluenceError("wiki unavailable")
        return {"status": "draft", "title": title, "url": "https://wiki.example.com/draft"}

    monkeypatch.setattr(confluence, "create_draft", create)
    result = nodes.publish_node(
        {
            "messages": [],
            "approval": {"decision": "drafts"},
            "artifacts": {key: f"Full content {key}" for key in roles.KEYS},
        },
        {},
    )
    assert result["publication"]["status"] == "partial"
    assert len(calls) == len(roles.KEYS)
    assert result["publication"]["pages"][1]["reason"] == "wiki unavailable"
    assert "document_hash" not in result


@pytest.mark.parametrize(
    "deployment,major",
    [("Cloud", 1000), ("Data Center", 8), ("Data Center", 9), ("Server", 10)],
)
@responses.activate
def test_jira_forms_only_read_metadata_and_preserve_long_content(deployment, major):
    s = jira.Settings(base_url="https://jira.example.com", token="test")
    base = s.base_url + s.api_path
    responses.get(
        base + "/serverInfo", json={"deploymentType": deployment, "versionNumbers": [major]}
    )
    responses.get(
        base + "/project/ORB", json={"id": "100", "issueTypes": [{"id": "5", "name": "Task"}]}
    )
    plan = jira_plan.Plan(
        items=[
            jira_plan.Item(
                local="TASK-1",
                type="Task",
                summary="Проверка & правка",
                description="Полное описание " * 600,
            )
        ]
    )
    result = jira_forms.prepare(plan, "ORB", settings=s)
    card = result["drafts"][0]
    assert card["document"] == plan.items[0].body()
    assert len(card["url"]) <= 4000
    assert result["created"] == []
    assert all(call.request.method == "GET" for call in responses.calls)
    # Форму заполняет любой Server / Data Center, а не только до девятой версии:
    # экрана создания там один, и отказ от ссылки по номеру версии оставлял
    # оператора со ссылкой на проект вместо готовой формы.
    assert parse_qs(urlsplit(card["url"]).query)["summary"] == [plan.items[0].summary]
    assert "слишком длинное" in card["note"]


@responses.activate
def test_jira_unreachable_names_the_failure_and_keeps_copyable_fields():
    """Отказ трекера нельзя выдавать за особенность версии: это разные починки."""
    s = jira.Settings(base_url="https://jira.example.com", token="test")
    responses.get(s.base_url + s.api_path + "/serverInfo", status=401)
    plan = jira_plan.Plan(
        items=[jira_plan.Item(local="T", type="Task", summary="Title", description="Body")]
    )
    result = jira_forms.prepare(plan, "ORB", settings=s)
    card = result["drafts"][0]
    assert card["document"] == "Body"
    assert card["url"].endswith("/browse/ORB")
    assert "401" in card["note"]
    assert any("401" in warning for warning in result["warnings"])
    assert "не заполнены" in result["reason"]
    assert result["created"] == []


@responses.activate
def test_form_link_carries_the_reporter_on_data_center():
    """
    Пустой «Автор» — красная ошибка в форме и ещё один шаг руками.

    Проверяется вместе с тем, что Cloud за учёткой не ходит: там поле
    опознаётся по accountId, и логин в ссылке ему не подходит.
    """
    s = jira.Settings(base_url="https://jira.example.com", token="t", api_path="/rest/api/2")
    base = s.base_url + s.api_path
    responses.get(base + "/serverInfo", json={"deploymentType": "Server", "versionNumbers": [10]})
    responses.get(
        base + "/project/ORB", json={"id": "1", "issueTypes": [{"id": "2", "name": "Task"}]}
    )
    responses.get(base + "/myself", json={"name": "ivanov"})
    plan = jira_plan.Plan(items=[jira_plan.Item(local="T", type="Task", summary="Тема")])

    result = jira_forms.prepare(plan, "ORB", settings=s)

    assert parse_qs(urlsplit(result["drafts"][0]["url"]).query)["reporter"] == ["ivanov"]
    assert len([call for call in responses.calls if call.request.url.endswith("/myself")]) == 1
