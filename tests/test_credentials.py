"""Личные подключения Jira и Confluence: чей токен уходит в запрос и как он хранится.

База подменена строками в памяти (`FakeRows`) — SQL проверяет последний тест,
на настоящем Postgres, если ему дали адрес (ORBITA_TEST_POSTGRES_URI).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import responses
from langchain_core.runnables import RunnableLambda
from starlette.testclient import TestClient

from agent import api, confluence, credentials, jira, security
from agent.security import SERVICE, Principal

ADMIN = "test-only-security-token-with-32-characters"
KEY = "k" * 43
JIRA = "https://jira.example.test"
ANNA = Principal("sub-anna", "anna")
TIM = Principal("sub-tim", "tim")


class FakeRows:
    def __init__(self):
        self.secrets: dict[tuple[str, str], tuple] = {}
        self.checked: dict[tuple[str, str], dict] = {}

    def load(self, subject):
        from datetime import UTC, datetime

        return {
            name: (*row, datetime(2026, 9, 25, tzinfo=UTC))
            for (owner, name), row in self.secrets.items()
            if owner == subject
        }

    def write(self, subject, sealed):
        for name, row in sealed.items():
            if row is None:
                self.secrets.pop((subject, name), None)
            else:
                self.secrets[(subject, name)] = row
        for system, pair in credentials.SYSTEMS.items():
            if set(pair) & set(sealed):
                self.checked.pop((subject, system), None)

    def checks(self, subject):
        return {system: v for (owner, system), v in self.checked.items() if owner == subject}

    def record_check(self, subject, system, ok, detail):
        self.checked[(subject, system)] = {"ok": ok, "detail": detail, "checked_at": "now"}


@pytest.fixture
def rows(monkeypatch):
    fake = FakeRows()
    monkeypatch.setattr(credentials, "rows", fake)
    monkeypatch.setattr(credentials, "_cache", {})
    monkeypatch.setenv("USER_SECRETS_KEY", KEY)
    monkeypatch.setenv("JIRA_BASE_URL", JIRA)
    monkeypatch.setenv("JIRA_API_PATH", "/rest/api/2")
    monkeypatch.setenv("JIRA_TOKEN", "shared-token-from-env")
    return fake


def as_user(principal, fn, *args):
    token = security._CURRENT.set(principal)
    try:
        return fn(*args)
    finally:
        security._CURRENT.reset(token)


# --------------------------------------------------------------------------
# Шифрование
# --------------------------------------------------------------------------
def test_a_sealed_value_opens_only_for_its_owner_and_field(monkeypatch):
    monkeypatch.setenv("USER_SECRETS_KEY", KEY)
    key_id, nonce, ciphertext = credentials.seal("sub-anna", "JIRA_TOKEN", "anna-pat")

    assert b"anna-pat" not in ciphertext
    assert credentials.unseal("sub-anna", "JIRA_TOKEN", key_id, nonce, ciphertext) == "anna-pat"
    # Та же запись, переставленная другому человеку или в другое поле, не читается.
    with pytest.raises(credentials.CredentialsError):
        credentials.unseal("sub-tim", "JIRA_TOKEN", key_id, nonce, ciphertext)
    with pytest.raises(credentials.CredentialsError):
        credentials.unseal("sub-anna", "CONFLUENCE_TOKEN", key_id, nonce, ciphertext)


def test_a_rotated_key_still_reads_old_records(monkeypatch):
    monkeypatch.setenv("USER_SECRETS_KEY", KEY)
    sealed = credentials.seal("sub-anna", "JIRA_TOKEN", "anna-pat")

    monkeypatch.setenv("USER_SECRETS_KEY", "n" * 43)
    with pytest.raises(credentials.CredentialsError, match="USER_SECRETS_OLD_KEYS"):
        credentials.unseal("sub-anna", "JIRA_TOKEN", *sealed)
    monkeypatch.setenv("USER_SECRETS_OLD_KEYS", KEY)
    assert credentials.unseal("sub-anna", "JIRA_TOKEN", *sealed) == "anna-pat"
    # Новое пишется уже новым ключом.
    assert credentials.seal("sub-anna", "JIRA_TOKEN", "x")[0] != sealed[0]


@pytest.mark.parametrize("key", [None, "short"])
def test_without_a_proper_key_nothing_is_stored(monkeypatch, key):
    if key:
        monkeypatch.setenv("USER_SECRETS_KEY", key)
    with pytest.raises(credentials.CredentialsError, match="USER_SECRETS_KEY"):
        credentials.seal("sub-anna", "JIRA_TOKEN", "anna-pat")


# --------------------------------------------------------------------------
# Чей токен уходит в Jira
# --------------------------------------------------------------------------
def test_each_user_reaches_jira_with_their_own_token(rows):
    credentials.save(ANNA.subject, {"JIRA_TOKEN": "anna-pat", "JIRA_EMAIL": "anna@corp"})
    credentials.save(TIM.subject, {"JIRA_TOKEN": "tim-pat"})

    anna = as_user(ANNA, jira.load_settings)
    tim = as_user(TIM, jira.load_settings)
    assert (anna.token, anna.email) == ("anna-pat", "anna@corp")
    assert (tim.token, tim.email) == ("tim-pat", None)
    # Админ-токен и скрипты без пользователя — общий токен из .env, как раньше.
    assert as_user(SERVICE, jira.load_settings).token == "shared-token-from-env"
    assert jira.load_settings().token == "shared-token-from-env"


def test_without_a_personal_token_the_shared_one_is_not_used(rows):
    with pytest.raises(jira.JiraError) as refused:
        as_user(ANNA, jira.load_settings)

    assert "токен Jira" in str(refused.value)
    assert "Мои подключения" in str(refused.value)
    assert as_user(ANNA, jira.missing_vars) == ["JIRA_TOKEN"]


def test_inside_a_run_the_run_owner_wins_over_the_request(rows):
    """Воркер прогонов мог родиться в чужом запросе: верить надо конфигу прогона."""
    credentials.save(ANNA.subject, {"JIRA_TOKEN": "anna-pat"})
    credentials.save(TIM.subject, {"JIRA_TOKEN": "tim-pat"})
    node = RunnableLambda(lambda _: jira.load_settings().token)
    config = {"configurable": {"langgraph_auth_user_id": ANNA.subject}}

    assert as_user(TIM, node.invoke, None, config) == "anna-pat"


def test_confluence_can_be_checked_without_a_space(rows, monkeypatch):
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://wiki.example.test")
    credentials.save(ANNA.subject, {"CONFLUENCE_TOKEN": "anna-wiki"})

    with pytest.raises(confluence.ConfluenceError, match="пространство Confluence"):
        as_user(ANNA, confluence.load_settings)
    assert as_user(ANNA, lambda: confluence.load_settings(require_space=False)).token == "anna-wiki"


def test_an_unavailable_database_is_named_not_mistaken_for_a_missing_token(rows, monkeypatch):
    def down(subject):
        raise credentials.db.DatabaseUnavailable("база Orbita недоступна (POSTGRES_URI): refused")

    monkeypatch.setattr(rows, "load", down)
    with pytest.raises(jira.JiraError, match="база Orbita недоступна"):
        as_user(ANNA, jira.load_settings)


# --------------------------------------------------------------------------
# «Мои подключения» по HTTP
# --------------------------------------------------------------------------
@pytest.fixture
def client(rows, monkeypatch):
    monkeypatch.setenv("API_ADMIN_TOKEN", ADMIN)
    users = {"anna": ANNA, "tim": TIM}
    checked = security.authenticate
    monkeypatch.setattr(
        security,
        "authenticate",
        lambda headers: users.get(headers.get("authorization", "").removeprefix("Bearer "))
        or checked(headers),
    )
    return TestClient(api.app)


def bearer(name):
    return {"Authorization": f"Bearer {name}"}


def field(doc, system, name):
    found = next(item for item in doc["systems"] if item["id"] == system)
    return next(item for item in found["fields"] if item["name"] == name)


def test_the_token_never_comes_back(client):
    saved = client.put(
        "/api/me/connections",
        json={"values": {"JIRA_TOKEN": "anna-pat", "JIRA_EMAIL": "anna@corp"}},
        headers=bearer("anna"),
    )

    assert saved.status_code == 200, saved.text
    assert "anna-pat" not in saved.text
    doc = saved.json()
    assert field(doc, "jira", "JIRA_TOKEN")["filled"] is True
    assert field(doc, "jira", "JIRA_TOKEN")["value"] == ""
    assert field(doc, "jira", "JIRA_EMAIL")["value"] == "anna@corp"
    assert doc["systems"][0]["base_url"] == JIRA
    assert doc["systems"][0]["connected"] is True
    # Соседу не видно ни значения, ни самого факта.
    other = client.get("/api/me/connections", headers=bearer("tim")).json()
    assert field(other, "jira", "JIRA_TOKEN")["filled"] is False


def test_a_mask_or_a_foreign_name_is_refused(client):
    for values in ({"JIRA_TOKEN": "********"}, {"LLM_API_KEY": "x"}, {"JIRA_TOKEN": "a\nb"}):
        response = client.put("/api/me/connections", json={"values": values}, headers=bearer("anna"))
        assert response.status_code == 400, values


def test_the_admin_token_has_no_personal_connections(client):
    response = client.get("/api/me/connections", headers=bearer(ADMIN))

    assert response.status_code == 400
    assert response.json()["error_code"] == "connections_service"


@responses.activate
def test_a_check_asks_jira_who_i_am_with_my_token(client):
    responses.get(f"{JIRA}/rest/api/2/myself", json={"displayName": "Анна Аналитик"})
    client.put("/api/me/connections", json={"values": {"JIRA_TOKEN": "anna-pat"}}, headers=bearer("anna"))

    checked = client.post("/api/me/connections/jira/check", headers=bearer("anna")).json()

    assert checked["check"] == {"ok": True, "detail": "вход выполнен: Анна Аналитик"}
    assert responses.calls[0].request.headers["Authorization"] == "Bearer anna-pat"
    assert checked["systems"][0]["check"]["ok"] is True
    # Новый токен стирает проверку старого.
    again = client.put(
        "/api/me/connections", json={"values": {"JIRA_TOKEN": "anna-new"}}, headers=bearer("anna")
    ).json()
    assert again["systems"][0]["check"] is None


@responses.activate
def test_a_refused_check_is_an_answer_not_an_error(client):
    responses.get(f"{JIRA}/rest/api/2/myself", status=401, json={})
    client.put("/api/me/connections", json={"values": {"JIRA_TOKEN": "bad"}}, headers=bearer("anna"))

    checked = client.post("/api/me/connections/jira/check", headers=bearer("anna"))

    assert checked.status_code == 200
    assert checked.json()["check"]["ok"] is False
    assert "401" in checked.json()["check"]["detail"]


def test_disconnecting_forgets_token_and_email(client):
    client.put(
        "/api/me/connections",
        json={"values": {"JIRA_TOKEN": "anna-pat", "JIRA_EMAIL": "anna@corp", "JIRA_PROJECT_KEY": "ANNA"}},
        headers=bearer("anna"),
    )

    left = client.delete("/api/me/connections/jira", headers=bearer("anna")).json()

    assert field(left, "jira", "JIRA_TOKEN")["filled"] is False
    assert field(left, "jira", "JIRA_EMAIL")["value"] == ""
    # Проект — выбор места, а не пропуск: после нового токена он нужен тот же.
    assert field(left, "jira", "JIRA_PROJECT_KEY")["value"] == "ANNA"
    assert client.delete("/api/me/connections/github", headers=bearer("anna")).status_code == 404


# --------------------------------------------------------------------------
# Настоящий Postgres
# --------------------------------------------------------------------------
POSTGRES_SCRIPT = """
from agent import credentials, db

db.close()
rows = credentials.PostgresRows()
sealed = credentials.seal("sub-pg", "JIRA_TOKEN", "pg-pat")
rows.write("sub-pg", {"JIRA_TOKEN": sealed, "JIRA_EMAIL": credentials.seal("sub-pg", "JIRA_EMAIL", "a@b")})
rows.record_check("sub-pg", "jira", True, "ok")
loaded = rows.load("sub-pg")
assert credentials.unseal("sub-pg", "JIRA_TOKEN", *loaded["JIRA_TOKEN"][:3]) == "pg-pat"
assert rows.checks("sub-pg")["jira"]["ok"] is True
rows.write("sub-pg", {"JIRA_TOKEN": credentials.seal("sub-pg", "JIRA_TOKEN", "pg-new")})
assert "jira" not in rows.checks("sub-pg")
rows.write("sub-pg", {"JIRA_TOKEN": None, "JIRA_EMAIL": None})
assert rows.load("sub-pg") == {}
# Схема накатывается один раз: второе открытие пула её не трогает.
db.close()
with db.connection() as conn:
    versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY 1")]
assert versions == [m[0] for m in db.MIGRATIONS], versions
db.close()
print("ok")
"""


@pytest.mark.skipif(
    not os.environ.get("ORBITA_TEST_POSTGRES_URI"),
    reason="нужен Postgres: ORBITA_TEST_POSTGRES_URI=postgresql://… (например, сервис postgres из Compose)",
)
def test_rows_round_trip_through_real_postgres():
    env = {
        **os.environ,
        "PYTHON_DOTENV_DISABLED": "1",
        "POSTGRES_URI": os.environ["ORBITA_TEST_POSTGRES_URI"],
        "USER_SECRETS_KEY": KEY,
    }
    result = subprocess.run(
        [sys.executable, "-c", POSTGRES_SCRIPT],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, timeout=60, check=False,
    )

    assert result.returncode == 0, result.stderr[-3000:]


# --------------------------------------------------------------------------
# Проект и пространство — у каждого свои
# --------------------------------------------------------------------------
def test_each_user_has_their_own_project_and_space(rows, monkeypatch):
    monkeypatch.setenv("JIRA_PROJECT_KEY", "TEAM")
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://wiki.example.test")
    monkeypatch.setenv("CONFLUENCE_SPACE_KEY", "USPA")
    monkeypatch.setenv("CONFLUENCE_PARENT_PAGE_ID", "111")
    credentials.save(ANNA.subject, {
        "JIRA_TOKEN": "a", "JIRA_PROJECT_KEY": "anna",
        "CONFLUENCE_TOKEN": "a", "CONFLUENCE_SPACE_KEY": "~anna",
    })
    credentials.save(TIM.subject, {"JIRA_TOKEN": "t", "CONFLUENCE_TOKEN": "t"})

    assert as_user(ANNA, jira.default_project) == "ANNA"
    anna = as_user(ANNA, confluence.load_settings)
    # Своё пространство без своего родителя — корень своего пространства:
    # страница 111 лежит в USPA, и публикация под неё упала бы.
    assert (anna.space_key, anna.parent_id) == ("~anna", None)
    # Без своих значений — общие из .env, как значение по умолчанию.
    assert as_user(TIM, jira.default_project) == "TEAM"
    tim = as_user(TIM, confluence.load_settings)
    assert (tim.space_key, tim.parent_id) == ("USPA", "111")


@responses.activate
def test_an_own_space_id_is_searched_in_that_space_not_the_shared_one(rows, monkeypatch):
    """
    REST v2: страница «Мои подключения» показывает только id пространства, и
    ключ человек задать не может. Общий ключ из `.env` к его id не пара:
    публикация шла бы в 222, а поиск — в SHARED.
    """
    wiki = "https://wiki.example.test"
    monkeypatch.setenv("CONFLUENCE_BASE_URL", wiki)
    monkeypatch.setenv("CONFLUENCE_API_VERSION", "v2")
    monkeypatch.setenv("CONFLUENCE_SPACE_KEY", "SHARED")
    monkeypatch.setenv("CONFLUENCE_SPACE_ID", "111")
    monkeypatch.setattr(confluence, "_SPACE_KEYS", {})
    credentials.save(ANNA.subject, {"CONFLUENCE_TOKEN": "a", "CONFLUENCE_SPACE_ID": "222"})
    credentials.save(TIM.subject, {"CONFLUENCE_TOKEN": "t"})
    responses.add(responses.GET, wiki + "/api/v2/spaces/222", json={"id": "222", "key": "ANNA"})
    responses.add(responses.GET, wiki + "/rest/api/content/search", json={"results": []})

    assert as_user(ANNA, confluence.load_settings).space_id == "222"
    as_user(ANNA, confluence.search, "вебхук")
    # Без своих значений общие ключ и id остаются парой из `.env`.
    tim = as_user(TIM, confluence.load_settings)
    assert (tim.space_key, tim.space_id) == ("SHARED", "111")
    as_user(TIM, confluence.search, "вебхук")

    searched = [call.request.params["cql"] for call in responses.calls if "cql" in call.request.params]
    assert searched == [
        'type = "page" AND space = "ANNA" AND text ~ "вебхук"',
        'type = "page" AND space = "SHARED" AND text ~ "вебхук"',
    ]


def test_the_page_shows_the_shared_default_next_to_the_own_value(client, monkeypatch):
    monkeypatch.setenv("JIRA_PROJECT_KEY", "TEAM")

    doc = client.get("/api/me/connections", headers=bearer("anna")).json()

    project = field(doc, "jira", "JIRA_PROJECT_KEY")
    assert (project["value"], project["default"], project["secret"]) == ("", "TEAM", False)
    # REST v1 публикует по ключу пространства; числовой id ему не нужен и только путал бы.
    names = [item["name"] for item in next(s for s in doc["systems"] if s["id"] == "confluence")["fields"]]
    assert "CONFLUENCE_SPACE_KEY" in names and "CONFLUENCE_SPACE_ID" not in names


@pytest.mark.parametrize(
    "values",
    [{"JIRA_PROJECT_KEY": "ORB 1"}, {"CONFLUENCE_PARENT_PAGE_ID": "page-1"}, {"CONFLUENCE_SPACE_KEY": "a b"}],
)
def test_a_malformed_place_is_refused(client, values):
    response = client.put("/api/me/connections", json={"values": values}, headers=bearer("anna"))

    assert response.status_code == 400
