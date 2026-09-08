"""Границы служебного HTTP API: аутентификация, размер и allowlist настроек."""

from __future__ import annotations

from starlette.testclient import TestClient

from agent import api


def test_admin_token_protects_custom_routes(monkeypatch):
    monkeypatch.setenv("API_ADMIN_TOKEN", "test-only-correct-horse-battery-staple")
    client = TestClient(api.app)

    denied = client.get("/api/settings")
    allowed = client.get(
        "/api/settings",
        headers={"authorization": "Bearer test-only-correct-horse-battery-staple"},
    )

    assert denied.status_code == 401
    assert denied.headers["www-authenticate"] == "Bearer"
    assert allowed.status_code == 200


def test_process_environment_name_cannot_be_persisted(monkeypatch):
    monkeypatch.setenv("API_ADMIN_TOKEN", "test-only-auth-token-with-32-characters")
    client = TestClient(api.app, headers={"Authorization": "Bearer test-only-auth-token-with-32-characters"})

    value = client.put("/api/settings", json={"values": {"PYTHONPATH": "payload"}})
    note = client.put("/api/settings", json={"comments": {"LD_PRELOAD": "подпись"}})
    shape = client.put("/api/settings", json={"values": {"нет": "1"}})

    assert value.status_code == 400
    assert "правится только на сервере" in value.json()["error"]
    assert note.status_code == 400
    assert "правится только на сервере" in note.json()["error"]
    assert shape.status_code == 400
    assert "так не выглядит" in shape.json()["error"]


def test_request_body_has_a_hard_limit(monkeypatch):
    monkeypatch.setenv("API_ADMIN_TOKEN", "test-only-auth-token-with-32-characters")
    monkeypatch.setenv("API_MAX_REQUEST_BYTES", "1024")
    client = TestClient(api.app, headers={"Authorization": "Bearer test-only-auth-token-with-32-characters"})

    response = client.post("/api/inputs", json={"name": "x" * 2000})

    assert response.status_code == 413


def test_every_saved_setting_is_reported_as_requiring_restart(monkeypatch):
    monkeypatch.setenv("API_ADMIN_TOKEN", "test-only-auth-token-with-32-characters")
    monkeypatch.setattr(
        api.settings_io,
        "save",
        lambda updates, comments=None: {"saved": sorted(updates), "path": ".env"},
    )
    client = TestClient(api.app, headers={"Authorization": "Bearer test-only-auth-token-with-32-characters"})

    response = client.put("/api/settings", json={"values": {"AGENT_NAME": "New name"}})

    assert response.status_code == 200
    assert response.json()["restart_required"] == ["AGENT_NAME"]


def test_published_document_cannot_escape_its_folder(monkeypatch):
    """Имя приходит из запроса, поэтому граница проверяется на сервере, не на фронте."""
    monkeypatch.setenv("API_ADMIN_TOKEN", "test-only-auth-token-with-32-characters")
    client = TestClient(api.app, headers={"Authorization": "Bearer test-only-auth-token-with-32-characters"})

    response = client.get("/api/published/file", params={"name": "../../.env"})

    assert response.status_code == 404


def test_published_listing_is_protected_by_the_admin_token(monkeypatch):
    monkeypatch.setenv("API_ADMIN_TOKEN", "test-only-correct-horse-battery-staple")
    client = TestClient(api.app)

    assert client.get("/api/published").status_code == 401
