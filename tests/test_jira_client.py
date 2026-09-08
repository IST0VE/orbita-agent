"""
REST-клиент чтения Jira на библиотеке `responses`.

`responses` подменяет транспорт requests и роняет любой запрос, для которого
не зарегистрирован ответ. Поэтому тест, который случайно уйдёт в сеть, упадёт,
а не тихо сходит на чей-то боевой трекер.

Проверяется три вещи. Первая — разбор описания: Cloud отдаёт его деревом ADF,
Data Center — строкой, и роль обязана получить текст в обоих случаях. Вторая —
что в JQL не уезжает то, чего туда не клали: запрос собирает клиент, а не
модель. Третья — что отказ трекера превращается во внятную строку, а не в
исключение посреди прогона.
"""

from __future__ import annotations

import pytest
import responses

from agent import config as cfg
from agent import jira

BASE = "https://jira.example.com"
API = "/rest/api/3"

CLOUD = jira.Settings(
    base_url=BASE,
    token="tok-123",
    email="me@org.com",
    api_path=API,
    timeout_s=5.0,
    search_limit=3,
    comments_limit=5,
    max_chars=1000,
)
SERVER = jira.Settings(
    base_url=BASE,
    token="pat-456",
    api_path="/rest/api/2",
    search_path="/search",
    timeout_s=5.0,
)


def adf(*paragraphs: str) -> dict:
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": text}]}
            for text in paragraphs
        ],
    }


def issue_payload(**fields) -> dict:
    base = {
        "summary": "Экспорт отчётов в CSV",
        "description": adf("Нужен экспорт в личном кабинете."),
        "status": {"name": "In Progress"},
        "issuetype": {"name": "Story"},
        "priority": {"name": "High"},
        "labels": ["export"],
        "components": [{"name": "web-app"}],
        "assignee": {"displayName": "Мария И."},
    }
    return {"key": "ORB-123", "fields": {**base, **fields}}


def register_issue(payload: dict, comments: list | None = None) -> None:
    responses.add(responses.GET, f"{BASE}{API}/issue/ORB-123", json=payload, status=200)
    responses.add(
        responses.GET,
        f"{BASE}{API}/issue/ORB-123/comment",
        json={"comments": comments or []},
        status=200,
    )


# --------------------------------------------------------------------------
# Ключи задач в тексте
# --------------------------------------------------------------------------
def test_issue_keys_are_found_in_operator_text():
    found = jira.find_keys("надо доделать ORB-123 и заодно посмотреть PAY-7")

    assert found == ["ORB-123", "PAY-7"]


def test_keys_are_not_confused_with_ordinary_words():
    """
    Регулярка сталкивается с текстом на русском, где дефис и цифры обычны.
    `UTF-8` и `COVID-19` формально похожи на ключ; отличает их то, что
    настоящий ключ стоит отдельным словом и после него нет букв.
    """
    assert jira.find_keys("кодировка UTF-8, дата 2026-08") == ["UTF-8"]
    assert jira.find_keys("см. ORB-123abc") == []


def test_repeated_keys_are_listed_once():
    assert jira.find_keys("ORB-1 связана с ORB-1 и ORB-2") == ["ORB-1", "ORB-2"]


# --------------------------------------------------------------------------
# Разбор описания
# --------------------------------------------------------------------------
def test_adf_document_becomes_text():
    document = {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Первый абзац."}]},
            {
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": "пункт"}]}
                        ],
                    }
                ],
            },
        ],
    }

    assert jira.field_text(document) == "Первый абзац.\nпункт"


def test_mentions_survive_the_conversion():
    """
    Упоминание текста в `content` не имеет — оно в атрибутах. Потерять его
    значит потерять исполнителя, названного прямо в описании.
    """
    document = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Согласовать с "},
                    {"type": "mention", "attrs": {"text": "@Мария И."}},
                ],
            }
        ],
    }

    assert "@Мария И." in jira.field_text(document)


def test_data_center_returns_a_plain_string():
    assert jira.field_text("  Обычная строка из v2  ") == "Обычная строка из v2"


def test_empty_description_is_a_fact_not_a_failure():
    """Незаполненное описание — самая частая находка конвейера, а не сбой."""
    assert jira.field_text(None) == ""


# --------------------------------------------------------------------------
# Чтение задачи
# --------------------------------------------------------------------------
@responses.activate
def test_issue_is_read_with_fields_links_and_comments():
    register_issue(
        issue_payload(
            subtasks=[{"key": "ORB-124", "fields": {"summary": "Схема таблицы"}}],
            issuelinks=[
                {
                    "type": {"outward": "blocks"},
                    "outwardIssue": {"key": "ORB-200", "fields": {"summary": "Релиз"}},
                }
            ],
        ),
        comments=[
            {
                "author": {"displayName": "Пётр"},
                "created": "2026-08-20T10:00:00.000+0300",
                "body": adf("Согласовали формат: точка с запятой."),
            }
        ],
    )

    issue = jira.fetch_issue("ORB-123", CLOUD)

    assert issue["key"] == "ORB-123"
    assert issue["url"] == f"{BASE}/browse/ORB-123"
    assert issue["status"] == "In Progress"
    assert issue["assignee"] == "Мария И."
    assert issue["components"] == ["web-app"]
    assert issue["subtasks"] == [{"key": "ORB-124", "summary": "Схема таблицы"}]
    assert issue["links"] == [{"relation": "blocks", "key": "ORB-200", "summary": "Релиз"}]
    assert issue["comments"][0]["text"] == "Согласовали формат: точка с запятой."


@responses.activate
def test_only_the_needed_fields_are_requested():
    """
    `*all` на задаче с годовой историей — это сотни килобайт кастомных полей,
    которые оплачиваются на каждом следующем запросе этапа.
    """
    register_issue(issue_payload())

    jira.fetch_issue("ORB-123", CLOUD)

    requested = responses.calls[0].request.params["fields"]
    assert "summary" in requested and "description" in requested
    assert "*all" not in requested


@responses.activate
def test_comments_are_ordered_as_a_conversation():
    """API отдаёт новые первыми; читать их так — значит читать разговор задом наперёд."""
    register_issue(
        issue_payload(),
        comments=[
            {
                "author": {"displayName": "Б"},
                "created": "2026-08-21T10:00:00.000+0300",
                "body": "второй",
            },
            {
                "author": {"displayName": "А"},
                "created": "2026-08-20T10:00:00.000+0300",
                "body": "первый",
            },
        ],
    )

    issue = jira.fetch_issue("ORB-123", CLOUD)

    assert [item["text"] for item in issue["comments"]] == ["первый", "второй"]


@responses.activate
def test_long_description_is_capped():
    register_issue(issue_payload(description=adf("х" * 5000)))

    issue = jira.fetch_issue("ORB-123", CLOUD)

    assert len(issue["description"]) == CLOUD.max_chars


def test_a_key_is_validated_before_the_request_goes_out():
    """Проверка до сети: `сделай экспорт` не должно превращаться в GET /issue/."""
    with pytest.raises(jira.JiraError, match="не похоже на ключ"):
        jira.fetch_issue("сделай экспорт", CLOUD)


# --------------------------------------------------------------------------
# Поиск
# --------------------------------------------------------------------------
@responses.activate
def test_search_builds_its_own_jql():
    """
    JQL собирает клиент. Свободный JQL от модели — чужой язык запросов под
    нашим токеном: выгрузить им всё, до чего токен дотягивается, ничего не стоит.
    """
    responses.add(
        responses.GET,
        f"{BASE}{API}/search/jql",
        json={
            "issues": [
                {
                    "key": "ORB-9",
                    "fields": {
                        "summary": "Старый экспорт",
                        "status": {"name": "Done"},
                        "issuetype": {"name": "Task"},
                    },
                }
            ]
        },
        status=200,
    )

    found = jira.search("экспорт CSV", CLOUD)

    sent = responses.calls[0].request.params
    assert sent["jql"] == 'text ~ "экспорт CSV" ORDER BY updated DESC'
    assert sent["maxResults"] == "3"
    assert found[0]["key"] == "ORB-9"
    assert found[0]["url"] == f"{BASE}/browse/ORB-9"


@responses.activate
def test_quotes_in_the_query_cannot_break_out_of_jql():
    responses.add(responses.GET, f"{BASE}{API}/search/jql", json={"issues": []}, status=200)

    jira.search('экспорт" OR project = SECRET', CLOUD)

    assert (
        responses.calls[0].request.params["jql"]
        == 'text ~ "экспорт\\" OR project = SECRET" ORDER BY updated DESC'
    )


def test_an_empty_query_does_not_go_to_the_network():
    """Ни одного зарегистрированного ответа: любой запрос уронил бы тест."""
    assert jira.search("   ", CLOUD) == []


# --------------------------------------------------------------------------
# Отказы
# --------------------------------------------------------------------------
@responses.activate
def test_missing_issue_says_so_plainly():
    responses.add(responses.GET, f"{BASE}{API}/issue/ORB-123", json={}, status=404)

    with pytest.raises(jira.JiraError, match="не найден"):
        jira.fetch_issue("ORB-123", CLOUD)


@responses.activate
def test_error_messages_of_jira_are_unwrapped():
    responses.add(
        responses.GET,
        f"{BASE}{API}/issue/ORB-123",
        json={"errorMessages": ["Issue does not exist"], "errors": {"project": "нет прав"}},
        status=400,
    )

    with pytest.raises(jira.JiraError) as exc:
        jira.fetch_issue("ORB-123", CLOUD)

    assert "Issue does not exist" in str(exc.value)
    assert "project: нет прав" in str(exc.value)


@responses.activate
def test_401_without_email_hints_at_cloud_basic_auth():
    responses.add(responses.GET, f"{BASE}/rest/api/2/issue/ORB-123", json={}, status=401)

    with pytest.raises(jira.JiraError, match="JIRA_EMAIL"):
        jira.fetch_issue("ORB-123", SERVER)


# --------------------------------------------------------------------------
# Настройки
# --------------------------------------------------------------------------
def test_token_does_not_travel_over_plain_http(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JIRA_BASE_URL", "http://jira.example.com")
    monkeypatch.setenv("JIRA_TOKEN", "tok")

    with pytest.raises(jira.JiraError, match="JIRA_ALLOW_INSECURE_HTTP"):
        jira.load_settings()


def test_loopback_over_http_is_allowed(monkeypatch: pytest.MonkeyPatch):
    """Локальный стенд на http — обычное дело, и запрещать его не за что."""
    monkeypatch.setenv("JIRA_BASE_URL", "http://localhost:8080")
    monkeypatch.setenv("JIRA_TOKEN", "tok")

    assert jira.load_settings().base_url == "http://localhost:8080"


@responses.activate
def test_data_center_searches_at_its_own_path():
    """
    Cloud перевёл поиск на `/search/jql`, Data Center остался на `/search`.
    Один путь на весь клиент — это 404 на каждом поиске при полностью рабочем
    чтении задач по ключу, то есть отказ, который выглядит как «ничего не нашлось».
    """
    responses.add(responses.GET, f"{BASE}/rest/api/2/search", json={"issues": []}, status=200)

    assert jira.search("экспорт", SERVER) == []


def test_the_search_path_follows_the_api_version(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JIRA_API_PATH", "/rest/api/2")
    assert cfg.jira_search_path() == "/search"

    monkeypatch.setenv("JIRA_API_PATH", "/rest/api/3")
    assert cfg.jira_search_path() == "/search/jql"


def test_missing_variables_are_listed_by_name(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://jira.example.com")

    assert jira.missing_vars() == ["JIRA_TOKEN"]
    assert not jira.is_configured()
