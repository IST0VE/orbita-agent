"""
Задача 1.6: REST-клиент Confluence на библиотеке `responses`.

`responses` подменяет транспорт requests и роняет любой запрос, для которого
не зарегистрирован ответ. Поэтому тест, который случайно уйдёт в сеть, упадёт,
а не тихо сходит на чей-то боевой wiki.

Настройки передаются в `publish_page` явным объектом Settings — окружение
здесь не участвует, кроме подсказки в тексте ошибки 401.
"""

from __future__ import annotations

import json

import pytest
import requests
import responses

from agent.confluence import (
    ConfluenceError,
    Settings,
    find_page,
    load_settings,
    page_url,
    publish_page,
)

BASE = "https://wiki.example.com"
API = "/rest/api/content"
URL = BASE + API

CLOUD = Settings(
    base_url=BASE,
    token="tok-123",
    space_key="SUP",
    email="me@org.com",
    parent_id="999",
    api_path=API,
    timeout_s=5.0,
    version_message="обновлено агентом Orbita",
)
SERVER = Settings(
    base_url=BASE,
    token="pat-456",
    space_key="SUP",
    api_path=API,
    timeout_s=5.0,
)


def created_page(page_id="123", version=1):
    return {
        "id": page_id,
        "version": {"number": version},
        "_links": {"base": BASE, "webui": f"/spaces/SUP/pages/{page_id}"},
    }


def body_of(call) -> dict:
    return json.loads(call.request.body)


def _configured_env(monkeypatch: pytest.MonkeyPatch, base_url: str) -> None:
    monkeypatch.setenv("CONFLUENCE_BASE_URL", base_url)
    monkeypatch.setenv("CONFLUENCE_TOKEN", "token")
    monkeypatch.setenv("CONFLUENCE_SPACE_KEY", "SUP")
    monkeypatch.setenv("CONFLUENCE_API_VERSION", "v1")
    monkeypatch.delenv("CONFLUENCE_ALLOW_INSECURE_HTTP", raising=False)


def test_settings_reject_plain_http_for_remote_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configured_env(monkeypatch, "http://wiki.example.com")

    with pytest.raises(ConfluenceError, match="HTTPS"):
        load_settings()


def test_settings_allow_plain_http_for_local_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configured_env(monkeypatch, "http://127.0.0.1:8090")

    assert load_settings().base_url == "http://127.0.0.1:8090"


def test_settings_require_explicit_opt_in_for_remote_plain_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configured_env(monkeypatch, "http://wiki.example.com")
    monkeypatch.setenv("CONFLUENCE_ALLOW_INSECURE_HTTP", "1")

    assert load_settings().base_url == "http://wiki.example.com"


# --------------------------------------------------------------------------
# Две ветки upsert
# --------------------------------------------------------------------------
@responses.activate
def test_creates_page_when_none_exists():
    responses.get(URL, json={"results": []})
    responses.post(URL, json=created_page())

    result = publish_page("Orbita: вопрос [t-1]", "<p>тело</p>", CLOUD)

    assert result["status"] == "created"
    assert result["page_id"] == "123"
    assert result["version"] == 1
    assert result["url"] == f"{BASE}/spaces/SUP/pages/123"

    payload = body_of(responses.calls[1])
    assert payload["space"] == {"key": "SUP"}
    assert payload["ancestors"] == [{"id": "999"}]
    assert payload["body"]["storage"] == {
        "value": "<p>тело</p>",
        "representation": "storage",
    }


@responses.activate
def test_create_without_parent_sends_no_ancestors():
    responses.get(URL, json={"results": []})
    responses.post(URL, json=created_page())

    publish_page("заголовок", "<p>тело</p>", SERVER)

    assert "ancestors" not in body_of(responses.calls[1])


@responses.activate
def test_updates_existing_page_with_next_version():
    responses.get(URL, json={"results": [{"id": "555", "version": {"number": 7}}]})
    responses.put(f"{URL}/555", json=created_page("555", version=8))

    result = publish_page("заголовок", "<p>новое тело</p>", CLOUD)

    assert result["status"] == "updated"
    assert result["version"] == 8

    payload = body_of(responses.calls[1])
    assert payload["id"] == "555"
    assert payload["version"] == {"number": 8, "message": "обновлено агентом Orbita"}
    assert responses.calls[1].request.url.endswith("/555")


@responses.activate
def test_find_page_returns_none_on_empty_results():
    responses.get(URL, json={"results": []})
    assert find_page("нет такой", CLOUD) is None


@responses.activate
def test_search_asks_for_exact_title_in_space():
    responses.get(URL, json={"results": []})
    find_page("Orbita: вопрос [t-1]", CLOUD)

    query = responses.calls[0].request.params
    assert query["title"] == "Orbita: вопрос [t-1]"
    assert query["spaceKey"] == "SUP"
    assert query["type"] == "page"


# --------------------------------------------------------------------------
# Авторизация
# --------------------------------------------------------------------------
@responses.activate
def test_cloud_uses_basic_auth():
    """E-mail задан — Confluence Cloud, Basic из пары email:token."""
    responses.get(URL, json={"results": []})
    find_page("заголовок", CLOUD)

    assert responses.calls[0].request.headers["Authorization"].startswith("Basic ")


@responses.activate
def test_server_uses_bearer_token():
    """E-mail не задан — Server / Data Center, personal access token в Bearer."""
    responses.get(URL, json={"results": []})
    find_page("заголовок", SERVER)

    assert responses.calls[0].request.headers["Authorization"] == "Bearer pat-456"


# --------------------------------------------------------------------------
# Ошибки
# --------------------------------------------------------------------------
@responses.activate
def test_401_mentions_confluence_email_when_it_is_not_set():
    """
    Самая частая ошибка настройки: токен Cloud без e-mail. Сообщение обязано
    называть переменную, а не просто «401».
    """
    responses.get(URL, json={"message": "Unauthorized"}, status=401)

    with pytest.raises(ConfluenceError) as exc:
        publish_page("заголовок", "<p>тело</p>", SERVER)

    text = str(exc.value)
    assert "HTTP 401" in text
    assert "CONFLUENCE_EMAIL" in text


@responses.activate
def test_403_with_email_set_points_at_permissions(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_EMAIL", "me@org.com")
    responses.get(URL, json={"message": "Forbidden"}, status=403)

    with pytest.raises(ConfluenceError) as exc:
        publish_page("заголовок", "<p>тело</p>", CLOUD)

    assert "права токена" in str(exc.value)


@responses.activate
def test_non_json_answer_gives_a_readable_error():
    """
    Прокси или SSO отдают HTML-страницу логина с кодом 200. Голый ValueError
    из .json() здесь ничего не объясняет.
    """
    responses.get(URL, body="<html>login</html>", status=200, content_type="text/html")

    with pytest.raises(ConfluenceError, match="ответ не является JSON"):
        publish_page("заголовок", "<p>тело</p>", CLOUD)


@responses.activate
def test_gateway_answer_is_named_instead_of_the_html_body():
    """
    HTML с кодом шлюза — это ответ посредника, а не Confluence: запрос до
    сервера не дошёл, и сообщение обязано говорить об этом, а не показывать
    страницу отказа.
    """
    responses.get(
        URL,
        body="<html><head><title>   Gateway   Timeout   </title></head></html>",
        status=504,
        content_type="text/html",
    )

    with pytest.raises(ConfluenceError) as exc:
        publish_page("заголовок", "<p>тело</p>", CLOUD)

    assert "HTTP 504" in str(exc.value)
    assert "«Gateway Timeout»" in str(exc.value)
    assert "<html" not in str(exc.value)


@responses.activate
def test_html_error_body_is_trimmed_into_the_message():
    """Отказ с HTML-телом, но не шлюзовым кодом: тело нужно, свёрнутое в строку."""
    responses.get(
        URL,
        body="<html>   Bad   Request   </html>",
        status=400,
        content_type="text/html",
    )

    with pytest.raises(ConfluenceError) as exc:
        publish_page("заголовок", "<p>тело</p>", CLOUD)

    assert "HTTP 400" in str(exc.value)
    assert "<html> Bad Request </html>" in str(exc.value)


@responses.activate
def test_network_failure_becomes_confluence_error():
    responses.get(URL, body=requests.ConnectionError("no route to host"))

    with pytest.raises(ConfluenceError, match="сеть недоступна"):
        publish_page("заголовок", "<p>тело</p>", CLOUD)


# --------------------------------------------------------------------------
# Ссылка на страницу
# --------------------------------------------------------------------------
def test_page_url_falls_back_when_links_are_absent():
    assert page_url({"id": "777"}, CLOUD) == f"{BASE}/pages/viewpage.action?pageId=777"
