"""
Задача 2.7: тот же набор сценариев для REST v2.

У v1 и v2 отличается не только путь: другой формат тела, другой поиск страницы
по заголовку и другое место идентификатора пространства. Поэтому проверка
общая, а ожидания по запросам — свои для каждой версии.
"""

from __future__ import annotations

import json

import pytest
import responses

from agent import config as cfg
from agent.confluence import ConfluenceError, Settings, backend, find_page, publish_page

BASE = "https://wiki.example.com"

V1 = Settings(
    base_url=BASE,
    token="tok",
    space_key="SUP",
    parent_id="999",
    api_path="/rest/api/content",
    api_version="v1",
)
V2 = Settings(
    base_url=BASE,
    token="tok",
    space_id="98765",
    parent_id="999",
    api_path="/api/v2",
    api_version="v2",
)

SEARCH = {"v1": f"{BASE}/rest/api/content", "v2": f"{BASE}/api/v2/pages"}
CREATE = SEARCH
SETTINGS = {"v1": V1, "v2": V2}


def page(page_id="123", version=1):
    return {
        "id": page_id,
        "version": {"number": version},
        "_links": {"base": BASE, "webui": f"/spaces/SUP/pages/{page_id}"},
    }


def body_of(call) -> dict:
    return json.loads(call.request.body)


# --------------------------------------------------------------------------
# Общее поведение: один и тот же набор для обеих версий
# --------------------------------------------------------------------------
@pytest.mark.parametrize("version", ["v1", "v2"])
@responses.activate
def test_creates_page_when_none_exists(version):
    s = SETTINGS[version]
    responses.get(SEARCH[version], json={"results": []})
    responses.post(CREATE[version], json=page())

    result = publish_page("заголовок", "<p>тело</p>", s)

    assert result["status"] == "created"
    assert result["page_id"] == "123"
    assert result["url"] == f"{BASE}/spaces/SUP/pages/123"


@pytest.mark.parametrize("version", ["v1", "v2"])
@responses.activate
def test_updates_existing_page_with_next_version(version):
    s = SETTINGS[version]
    responses.get(SEARCH[version], json={"results": [{"id": "555", "version": {"number": 7}}]})
    responses.put(f"{SEARCH[version]}/555", json=page("555", version=8))

    result = publish_page("заголовок", "<p>новое</p>", s)

    assert result["status"] == "updated"
    assert result["version"] == 8
    assert body_of(responses.calls[1])["version"]["number"] == 8


@pytest.mark.parametrize("version", ["v1", "v2"])
@responses.activate
def test_find_page_returns_none_on_empty_results(version):
    responses.get(SEARCH[version], json={"results": []})
    assert find_page("нет такой", SETTINGS[version]) is None


@pytest.mark.parametrize("version", ["v1", "v2"])
@responses.activate
def test_http_error_is_wrapped(version):
    responses.get(SEARCH[version], json={"message": "Unauthorized"}, status=401)

    with pytest.raises(ConfluenceError, match="HTTP 401"):
        publish_page("заголовок", "<p>тело</p>", SETTINGS[version])


# --------------------------------------------------------------------------
# Различия диалектов
# --------------------------------------------------------------------------
@responses.activate
def test_v2_searches_by_space_id():
    """v1 ищет по spaceKey, v2 — по числовому space-id."""
    responses.get(SEARCH["v2"], json={"results": []})
    find_page("заголовок", V2)

    query = responses.calls[0].request.params
    assert query["space-id"] == "98765"
    assert "spaceKey" not in query


@responses.activate
def test_v2_create_uses_space_id_and_flat_body():
    responses.get(SEARCH["v2"], json={"results": []})
    responses.post(CREATE["v2"], json=page())

    publish_page("заголовок", "<p>тело</p>", V2)

    payload = body_of(responses.calls[1])
    assert payload["spaceId"] == "98765"
    assert payload["status"] == "current"
    assert payload["body"] == {"representation": "storage", "value": "<p>тело</p>"}
    assert payload["parentId"] == "999"
    # v1-специфичного в теле быть не должно
    assert "space" not in payload
    assert "ancestors" not in payload
    assert "type" not in payload


@responses.activate
def test_v1_create_uses_space_key_and_nested_body():
    responses.get(SEARCH["v1"], json={"results": []})
    responses.post(CREATE["v1"], json=page())

    publish_page("заголовок", "<p>тело</p>", V1)

    payload = body_of(responses.calls[1])
    assert payload["space"] == {"key": "SUP"}
    assert payload["ancestors"] == [{"id": "999"}]
    assert payload["body"]["storage"]["representation"] == "storage"
    assert "spaceId" not in payload


def test_unknown_version_is_rejected():
    with pytest.raises(ConfluenceError, match="CONFLUENCE_API_VERSION"):
        backend(Settings(base_url=BASE, token="tok", api_version="v3"))


# --------------------------------------------------------------------------
# Конфигурация
# --------------------------------------------------------------------------
def test_api_path_default_follows_the_version(monkeypatch: pytest.MonkeyPatch):
    assert cfg.confluence_api_path() == "/rest/api/content"

    monkeypatch.setenv("CONFLUENCE_API_VERSION", "v2")
    assert cfg.confluence_api_path() == "/api/v2"


def test_explicit_api_path_wins(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_API_VERSION", "v2")
    monkeypatch.setenv("CONFLUENCE_API_PATH", "/proxy/confluence/api/v2")
    assert cfg.confluence_api_path() == "/proxy/confluence/api/v2"


def test_v2_requires_space_id_instead_of_space_key(monkeypatch: pytest.MonkeyPatch):
    """
    v2 создаёт страницу по числовому spaceId — ключ пространства она не
    принимает. Значит и список обязательных переменных у версий разный.
    """
    monkeypatch.setenv("CONFLUENCE_API_VERSION", "v2")
    assert "CONFLUENCE_SPACE_ID" in cfg.confluence_required_vars()
    assert "CONFLUENCE_SPACE_KEY" not in cfg.confluence_required_vars()


def test_unknown_version_is_a_configuration_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_API_VERSION", "v9")
    with pytest.raises(cfg.ConfigError, match="CONFLUENCE_API_VERSION"):
        cfg.confluence_api_version()
