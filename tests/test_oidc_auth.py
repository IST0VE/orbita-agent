"""Вход пользователей по OIDC: какие access-токены Keycloak API принимает.

Ключи подписи настоящие (RSA), сеть подменена: JWKS отдаёт заглушка, а
недоступный Keycloak изображает исключение клиента JWKS.
"""

from __future__ import annotations

import time
from urllib.parse import parse_qsl

import jwt
import pytest
import requests
import responses
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from agent import api, security

ADMIN = "test-only-security-token-with-32-characters"
ISSUER = "http://localhost:8180/realms/orbita"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJwks:
    def __init__(self, error: Exception | None = None):
        self.error = error

    def get_signing_key_from_jwt(self, token):
        if self.error:
            raise self.error
        return jwt.PyJWK.from_dict(
            {**jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True), "kid": "k1", "alg": "RS256"}
        )


def token(key=KEY, algorithm="RS256", **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": "user-1",
        "azp": "orbita-web",
        "typ": "Bearer",
        "iat": now,
        "exp": now + 300,
        "preferred_username": "analyst",
        "realm_access": {"roles": ["orbita-user"]},
    }
    claims.update(overrides)
    claims = {name: value for name, value in claims.items() if value is not None}
    return jwt.encode(claims, key, algorithm=algorithm, headers={"kid": "k1"})


@pytest.fixture
def oidc(monkeypatch):
    monkeypatch.setenv("API_ADMIN_TOKEN", ADMIN)
    monkeypatch.setenv("OIDC_ISSUER", ISSUER + "/")
    monkeypatch.setattr(security, "_jwks", lambda url: FakeJwks())
    return TestClient(api.app)


def get(client, value: str, path: str = "/api/me"):
    return client.get(path, headers={"Authorization": f"Bearer {value}"})


def test_the_login_settings_are_public_and_off_by_default(monkeypatch):
    monkeypatch.setenv("API_ADMIN_TOKEN", ADMIN)
    client = TestClient(api.app)

    assert client.get("/api/auth/config").json() == {"enabled": False}
    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    described = client.get("/api/auth/config").json()
    assert described["client_id"] == "orbita-web"
    assert described["authorization_endpoint"] == ISSUER + "/protocol/openid-connect/auth"
    # Токены браузер берёт у сервера: адрес token endpoint ему не нужен.
    assert "token_endpoint" not in described
    # Открыт ровно этот путь: соседний без токена закрыт.
    assert client.get("/api/auth/configx").status_code == 401


def test_a_user_token_from_the_realm_opens_the_api(oidc):
    assert get(oidc, token()).status_code == 200
    # Админ-токен остаётся: им живут healthcheck и скрипты.
    assert get(oidc, ADMIN).status_code == 200


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "http://evil.test/realms/orbita"},
        {"azp": "some-other-client"},
        {"typ": "ID"},
        {"exp": int(time.time()) - 120},
        {"sub": None},
    ],
)
def test_foreign_or_stale_tokens_are_refused(oidc, claims):
    response = get(oidc, token(**claims))

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_a_token_signed_by_another_key_is_refused(oidc):
    assert get(oidc, token(key=OTHER_KEY)).status_code == 401


def test_a_symmetric_token_keyed_with_the_public_key_is_refused(oidc):
    """Подмена алгоритма: HS256 с публичным ключом realm в роли секрета."""
    forged = jwt.api_jws.PyJWS().encode(
        b'{"iss":"%s","sub":"x","azp":"orbita-web","iat":1,"exp":9999999999}' % ISSUER.encode(),
        b"public-key-of-the-realm-as-hmac-secret",
        algorithm="HS256",
        headers={"kid": "k1"},
    )
    assert get(oidc, forged).status_code == 401


def test_the_required_role_is_checked(oidc, monkeypatch):
    monkeypatch.setenv("OIDC_REQUIRED_ROLE", "orbita-user")

    assert get(oidc, token()).status_code == 200
    assert get(oidc, token(realm_access={"roles": []})).status_code == 403
    client_role = {"orbita-web": {"roles": ["orbita-user"]}}
    assert get(oidc, token(realm_access=None, resource_access=client_role)).status_code == 200


def test_an_unreachable_keycloak_is_not_a_login_problem(oidc, monkeypatch):
    """401 увёл бы интерфейс на вход по кругу; ключей нет — это 503."""
    monkeypatch.setattr(
        security, "_jwks", lambda url: FakeJwks(jwt.PyJWKClientConnectionError("down"))
    )

    assert get(oidc, token()).status_code == 503


# --------------------------------------------------------------------------
# Обмен кода на токены — через сервер
#
# `connect-src` документа называет только API, а Keycloak живёт на своём
# домене: прямой fetch браузера к token endpoint политика заблокировала бы.
# --------------------------------------------------------------------------
TOKEN_URL = ISSUER + "/protocol/openid-connect/token"
ISSUED = {"access_token": "a.b.c", "refresh_token": "r", "expires_in": 300, "token_type": "Bearer"}


def exchange(client, **form):
    return client.post("/api/auth/token", data=form)


@responses.activate
def test_the_code_is_exchanged_by_the_server_without_a_token(oidc, monkeypatch):
    monkeypatch.delenv("OIDC_JWKS_URL", raising=False)
    responses.add(responses.POST, TOKEN_URL, json=ISSUED)

    answer = exchange(
        oidc,
        grant_type="authorization_code",
        code="the-code",
        redirect_uri="http://localhost:8080/",
        code_verifier="the-verifier",
        # Клиента выбирает сервер: чужой client_id и секрет не уходят.
        client_id="some-other-client",
        client_secret="guess",
    )

    assert answer.status_code == 200
    assert answer.json() == ISSUED
    assert answer.headers["cache-control"] == "no-store"
    sent = dict(parse_qsl(responses.calls[0].request.body))
    assert sent == {
        "grant_type": "authorization_code",
        "client_id": "orbita-web",
        "code": "the-code",
        "redirect_uri": "http://localhost:8080/",
        "code_verifier": "the-verifier",
    }


@responses.activate
def test_a_refresh_goes_the_same_way_and_a_refusal_comes_back_as_is(oidc, monkeypatch):
    monkeypatch.delenv("OIDC_JWKS_URL", raising=False)
    refused = {"error": "invalid_grant", "error_description": "Token is not active"}
    responses.add(responses.POST, TOKEN_URL, json=refused, status=400)

    answer = exchange(oidc, grant_type="refresh_token", refresh_token="stale")

    assert (answer.status_code, answer.json()) == (400, refused)
    assert dict(parse_qsl(responses.calls[0].request.body)) == {
        "grant_type": "refresh_token",
        "client_id": "orbita-web",
        "refresh_token": "stale",
    }


@responses.activate
def test_the_server_reaches_keycloak_where_it_reaches_the_keys(oidc, monkeypatch):
    """Keycloak из Compose: браузеру он localhost:8180, агенту — keycloak:8080."""
    inner = "http://keycloak:8080/realms/orbita/protocol/openid-connect"
    monkeypatch.setenv("OIDC_JWKS_URL", inner + "/certs")
    responses.add(responses.POST, inner + "/token", json=ISSUED)

    assert exchange(oidc, grant_type="refresh_token", refresh_token="r").status_code == 200


@responses.activate
@pytest.mark.parametrize(
    "form",
    [
        {"grant_type": "password", "username": "analyst", "password": "analyst"},
        {"grant_type": "client_credentials"},
        {"grant_type": "authorization_code", "code": "c", "redirect_uri": "http://x/"},
        {"grant_type": ["refresh_token", "password"], "refresh_token": "r"},
        {"grant_type": "refresh_token", "refresh_token": ["r1", "r2"]},
    ],
)
def test_only_a_code_with_pkce_or_a_refresh_reaches_keycloak(oidc, form):
    assert exchange(oidc, **form).status_code == 400
    assert not responses.calls


@responses.activate
def test_a_broken_keycloak_is_not_a_login_refusal(oidc, monkeypatch):
    monkeypatch.delenv("OIDC_JWKS_URL", raising=False)
    responses.add(responses.POST, TOKEN_URL, body="<html>Bad gateway</html>", status=502)

    assert exchange(oidc, grant_type="refresh_token", refresh_token="r").status_code == 502
    responses.replace(responses.POST, TOKEN_URL, body=requests.ConnectionError("down"))
    assert exchange(oidc, grant_type="refresh_token", refresh_token="r").status_code == 503


def test_the_exchange_is_bounded_and_needs_oidc(oidc, monkeypatch):
    too_long = exchange(oidc, grant_type="refresh_token", refresh_token="r" * 20_000)
    assert too_long.status_code == 413
    as_json = oidc.post("/api/auth/token", json={"grant_type": "refresh_token", "refresh_token": "r"})
    assert as_json.status_code == 415
    # Соседний путь открытым не стал, а GET на этот — тоже не обмен.
    assert oidc.post("/api/auth/tokens", data={}).status_code == 401
    assert oidc.get("/api/auth/token").status_code == 401

    monkeypatch.delenv("OIDC_ISSUER")
    assert exchange(oidc, grant_type="refresh_token", refresh_token="r").status_code == 404


def test_without_oidc_a_jwt_is_just_a_wrong_token(monkeypatch):
    monkeypatch.setenv("API_ADMIN_TOKEN", ADMIN)
    monkeypatch.setattr(security, "_jwks", lambda url: pytest.fail("JWKS requested without OIDC"))

    assert get(TestClient(api.app), token()).status_code == 401


def test_oidc_settings_cannot_be_changed_from_the_interface(oidc):
    response = oidc.put(
        "/api/settings",
        json={"values": {"OIDC_ISSUER": "http://evil.test/realms/x"}},
        headers={"Authorization": f"Bearer {ADMIN}"},
    )

    assert response.status_code == 400


def test_the_interface_learns_who_is_signed_in(oidc):
    me = get(oidc, token()).json()
    assert me == {"subject": "user-1", "name": "analyst", "admin": False, "service": False}

    service = get(oidc, ADMIN).json()
    assert service["admin"] and service["service"]


def test_server_settings_and_the_server_log_belong_to_the_admin_role(oidc):
    """Адрес Jira из настроек — это то, куда уйдёт токен: любой вошедший его не правит."""
    for path in ("/api/settings", "/api/logs"):
        refused = get(oidc, token(), path)
        assert refused.status_code == 403, path
        assert refused.json()["error_code"] == "admin_required"
        assert "orbita-admin" in refused.json()["error"]

        admin = token(realm_access={"roles": ["orbita-user", "orbita-admin"]})
        assert get(oidc, admin, path).status_code == 200, path
        assert get(oidc, ADMIN, path).status_code == 200, path

    written = oidc.put(
        "/api/settings",
        json={"values": {"JIRA_BASE_URL": "https://evil.test"}},
        headers={"Authorization": f"Bearer {token()}"},
    )
    assert written.status_code == 403


def test_the_admin_role_name_is_configurable(oidc, monkeypatch):
    monkeypatch.setenv("OIDC_ADMIN_ROLE", "orbita-ops")
    ops = token(realm_access={"roles": ["orbita-ops"]})

    assert get(oidc, ops).json()["admin"] is True
    assert get(oidc, token(realm_access={"roles": ["orbita-admin"]})).json()["admin"] is False


@pytest.mark.parametrize(
    "name", ["NT_RUNNER_TOKEN", "POSTGRES_URI", "USER_SECRETS_KEY", "KEYCLOAK_ADMIN_PASSWORD"]
)
def test_service_secrets_cannot_be_changed_from_the_interface(oidc, name):
    response = oidc.put(
        "/api/settings",
        json={"values": {name: "x" * 40}},
        headers={"Authorization": f"Bearer {ADMIN}"},
    )

    assert response.status_code == 400
