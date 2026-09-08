"""Regression tests for access, secret persistence and network boundaries."""

import asyncio
import os
import socket

import pytest
import responses
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from agent import api, config, confluence, inputs, jira, settings_io
from agent.security import ApiSecurityMiddleware

TOKEN = "test-only-security-token-with-32-characters"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.mark.parametrize("token", [None, "", "short", "x" * 257, "я" * 32])
def test_unconfigured_or_invalid_token_closes_every_route(monkeypatch, token):
    if token is None:
        monkeypatch.delenv("API_ADMIN_TOKEN", raising=False)
    else:
        monkeypatch.setenv("API_ADMIN_TOKEN", token)
    client = TestClient(api.app)
    for path in ("/api/settings", "/threads", "/runs", "/info", "/docs", "/noauth/threads"):
        response = client.get(path, headers=AUTH)
        assert response.status_code == 503
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/api/settings", "/threads", "/runs", "/store", "/info"])
def test_middleware_protects_routes_added_by_the_host_server(monkeypatch, path):
    monkeypatch.setenv("API_ADMIN_TOKEN", TOKEN)

    async def endpoint(request):
        return JSONResponse({"ok": True})

    # LangGraph installs the custom app's middleware around its own routes.
    app = Starlette(routes=[Route(path, endpoint)], middleware=api.app.user_middleware)
    client = TestClient(app)
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get(path, headers=AUTH).status_code == 200


def test_duplicate_and_non_ascii_credentials_are_rejected(monkeypatch):
    monkeypatch.setenv("API_ADMIN_TOKEN", TOKEN)
    client = TestClient(api.app)
    assert client.get("/api/settings", headers=[
        ("authorization", f"Bearer {TOKEN}"), ("authorization", "Bearer wrong"),
    ]).status_code == 401
    assert client.get("/api/settings", headers={b"authorization": b"Bearer \xff"}).status_code == 401


def test_forwarded_headers_and_preflight_do_not_bypass_auth(monkeypatch):
    monkeypatch.setenv("API_ADMIN_TOKEN", TOKEN)
    client = TestClient(api.app)
    for method in ("GET", "POST", "OPTIONS"):
        response = client.request(method, "/threads", headers={
            "X-Forwarded-For": "127.0.0.1", "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "POST",
        })
        assert response.status_code == 401


def test_chunked_oversize_body_never_reaches_handler(monkeypatch):
    monkeypatch.setenv("API_ADMIN_TOKEN", TOKEN)
    monkeypatch.setenv("API_MAX_REQUEST_BYTES", "1024")

    async def run():
        called = False
        sent = []
        parts = iter([
            {"type": "http.request", "body": b"x" * 700, "more_body": True},
            {"type": "http.request", "body": b"x" * 700, "more_body": False},
        ])

        async def endpoint(scope, receive, send):
            nonlocal called
            called = True

        async def receive():
            return next(parts)

        async def send(message):
            sent.append(message)

        await ApiSecurityMiddleware(endpoint)(
            {"type": "http", "headers": [(b"authorization", f"Bearer {TOKEN}".encode())]},
            receive, send,
        )
        assert not called
        assert sent[0]["status"] == 413

    asyncio.run(run())


@pytest.mark.parametrize("name", ["API_ADMIN_TOKEN", "ALL_PROXY", "LANGGRAPH_HTTP", "NEW_SETTING"])
def test_settings_cannot_change_security_or_unknown_environment(monkeypatch, name):
    monkeypatch.setenv("API_ADMIN_TOKEN", TOKEN)
    client = TestClient(api.app, headers=AUTH)
    assert client.put("/api/settings", json={"values": {name: "payload"}}).status_code == 400
    assert client.put("/api/settings", json={"comments": {name: "payload"}}).status_code == 400
    with pytest.raises(ValueError):
        settings_io.save({name: "payload"})


def test_settings_reject_secret_expansion_without_touching_file(monkeypatch, tmp_path):
    monkeypatch.setattr(settings_io, "env_path", lambda: tmp_path / ".env")
    with pytest.raises(ValueError):
        settings_io.save({"AGENT_NAME": "${API_ADMIN_TOKEN}"})
    assert not (tmp_path / ".env").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions")
def test_saved_secrets_are_owner_only(monkeypatch, tmp_path):
    monkeypatch.setattr(settings_io, "env_path", lambda: tmp_path / ".env")
    settings_io.save({"LLM_API_KEY": "test-value"})
    assert (tmp_path / ".env").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("name", ["local.env", ".env.local", "private.pem", "private.key"])
def test_input_secrets_cannot_be_read(name):
    root = inputs.root()
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text("test-secret", encoding="utf-8")
    with pytest.raises(inputs.InputError):
        inputs.read(".", name)
    with pytest.raises(inputs.InputError):
        inputs.preview(".", name)


@pytest.mark.parametrize("variable", ["LLM_API_BASE", "OPENAI_BASE_URL", "DEEPSEEK_API_BASE", "ANTHROPIC_BASE_URL"])
@pytest.mark.parametrize("url", ["http://remote.example", "https://user:secret@example.org", "https://example.org?token=secret", "file:///tmp/model"])
def test_llm_endpoint_rejects_cleartext_and_embedded_secrets(monkeypatch, variable, url):
    monkeypatch.setenv(variable, url)
    with pytest.raises(config.ConfigError) as error:
        config.llm_kwargs()
    assert url not in str(error.value)


@pytest.mark.parametrize("system", ["jira", "confluence"])
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@responses.activate
def test_external_redirect_never_forwards_document_or_token(monkeypatch, system, status):
    module = jira if system == "jira" else confluence
    prefix = system.upper()
    monkeypatch.setenv(f"{prefix}_BASE_URL", "https://source.example")
    monkeypatch.setenv(f"{prefix}_TOKEN", "test-secret")
    monkeypatch.setenv("CONFLUENCE_SPACE_KEY", "TEST")
    responses.add(responses.POST, "https://source.example/rest/test", status=status,
                  headers={"Location": "https://other.example/collect"})
    responses.add(responses.POST, "https://other.example/collect", json={})
    call = jira.call if system == "jira" else confluence._call
    error = jira.JiraError if system == "jira" else confluence.ConfluenceError
    with pytest.raises(error, match="redirect"):
        call("POST", "/rest/test", module.load_settings(), json={"document": "private"})
    assert len(responses.calls) == 1


def test_test_suite_blocks_real_network():
    with socket.socket() as sock, pytest.raises(AssertionError, match="network"):
        sock.connect(("127.0.0.1", 9))
