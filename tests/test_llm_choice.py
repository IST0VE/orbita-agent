"""
Своя модель у каждого пользователя: подключения, выбор, применение без перезапуска.

База подменена строками в памяти (`FakeRows`), шлюз — подменённым `httpx.get`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest
from langchain_core.runnables import RunnableLambda
from starlette.testclient import TestClient

from agent import api, llm_choice, providers, security, settings_io
from agent import config as cfg
from agent.security import Principal

ADMIN = "test-only-security-token-with-32-characters"
ANNA = Principal("sub-anna", "anna")
TIM = Principal("sub-tim", "tim")
GATEWAY = "https://gateway.example.test/v1"
SPARE = "https://spare.example.test/v1"


class FakeRows:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.reads = 0

    def load(self, subject):
        self.reads += 1
        return self.values.get(subject)

    def write(self, subject, value):
        if value is None:
            self.values.pop(subject, None)
        else:
            self.values[subject] = value


@pytest.fixture
def rows(monkeypatch):
    fake = FakeRows()
    monkeypatch.setattr(llm_choice, "rows", fake)
    monkeypatch.setattr(llm_choice, "_cache", {})
    monkeypatch.setattr(llm_choice, "_models_cache", {})
    monkeypatch.setenv("POSTGRES_URI", "postgresql://u:p@db.invalid/orbita")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_BASE", GATEWAY)
    monkeypatch.setenv("LLM_API_KEY", "main-key")
    monkeypatch.setenv("LLM_MODEL", "qwen-main")
    monkeypatch.setenv("LLM_MODELS", "qwen-main, qwen-big")
    monkeypatch.setenv("LLM_ALT1_TITLE", "Запасной шлюз")
    monkeypatch.setenv("LLM_ALT1_API_BASE", SPARE)
    monkeypatch.setenv("LLM_ALT1_API_KEY", "spare-key")
    monkeypatch.setenv("LLM_ALT1_EXTRA_BODY", '{"timeout": 420}')
    return fake


def as_user(principal, fn, *args):
    token = security._CURRENT.set(principal)
    try:
        return fn(*args)
    finally:
        security._CURRENT.reset(token)


@pytest.fixture
def gateway(monkeypatch):
    """`GET …/models` шлюза: по адресу — список, ключ записывается."""
    calls = []
    lists = {SPARE + "/models": ["spare-chat", "bge-m3", "whisper-large-v3", "spare-reasoner"]}

    def get(url, headers=None, **kwargs):
        calls.append((url, dict(headers or {})))
        if url not in lists:
            return httpx.Response(503, request=httpx.Request("GET", url))
        body = {"data": [{"id": name} for name in lists[url]]}
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(llm_choice.httpx, "get", get)
    return calls


# --------------------------------------------------------------------------
# Подключения
# --------------------------------------------------------------------------
def test_without_a_choice_everyone_works_on_the_server_model(rows):
    choice = as_user(ANNA, cfg.llm_choice)

    assert (choice.endpoint.id, choice.model, choice.chosen) == ("main", "qwen-main", False)
    assert cfg.default_model_name() == "qwen-main"


def test_a_spare_slot_never_gets_the_main_key(rows, monkeypatch):
    """Без своего ключа запасной слот идёт без ключа, а не с «родным» ключом основного."""
    monkeypatch.delenv("LLM_ALT1_API_KEY")
    monkeypatch.setenv("OPENAI_API_KEY", "vendor-main-key")

    kwargs = cfg.llm_kwargs(cfg.llm_endpoint("alt1"))

    assert kwargs["api_key"] == cfg.KEYLESS
    assert kwargs["base_url"] == SPARE
    assert kwargs["extra_body"] == {"timeout": 420}


def test_a_spare_slot_with_an_insecure_address_is_refused(rows, monkeypatch):
    monkeypatch.setenv("LLM_ALT1_API_BASE", "http://spare.example.test/v1")

    with pytest.raises(cfg.ConfigError, match="LLM_ALT1_API_BASE"):
        cfg.llm_endpoint("alt1")


def test_an_empty_slot_does_not_exist(rows):
    assert [endpoint.id for endpoint in cfg.llm_endpoints()] == ["main", "alt1"]
    with pytest.raises(cfg.ConfigError, match="больше не настроено"):
        cfg.llm_endpoint("alt2")


# --------------------------------------------------------------------------
# Выбор
# --------------------------------------------------------------------------
def test_the_choice_is_personal_and_applies_without_restart(rows, gateway):
    llm_choice.save(ANNA.subject, "alt1", "spare-chat")

    anna = as_user(ANNA, cfg.llm_choice)
    tim = as_user(TIM, cfg.llm_choice)

    assert (anna.endpoint.id, anna.model, anna.chosen) == ("alt1", "spare-chat", True)
    assert (tim.endpoint.id, tim.model) == ("main", "qwen-main")
    assert as_user(ANNA, cfg.llm_kwargs)["api_key"] == "spare-key"
    assert as_user(TIM, cfg.llm_kwargs)["api_key"] == "main-key"


def test_inside_a_run_the_run_owner_model_wins_over_the_request(rows, gateway):
    llm_choice.save(ANNA.subject, "alt1", "spare-chat")
    node = RunnableLambda(lambda _: cfg.model_name())
    config = {"configurable": {"langgraph_auth_user_id": ANNA.subject, "thread_id": "t"}}

    assert as_user(TIM, node.invoke, None, config) == "spare-chat"


def test_a_saved_choice_is_seen_at_once_and_then_cached(rows, gateway):
    as_user(ANNA, cfg.model_name)
    llm_choice.save(ANNA.subject, "main", "qwen-big")

    assert as_user(ANNA, cfg.model_name) == "qwen-big"
    reads = rows.reads
    for _ in range(5):
        as_user(ANNA, cfg.model_name)
    assert rows.reads == reads


def test_going_back_to_the_server_model(rows, gateway):
    llm_choice.save(ANNA.subject, "main", "qwen-big")
    llm_choice.save(ANNA.subject, None, None)

    assert as_user(ANNA, cfg.model_name) == "qwen-main"
    assert ANNA.subject not in rows.values


@pytest.mark.parametrize(
    ("endpoint", "model", "message"),
    [
        ("main", "gpt-unknown", "нет в списке"),
        ("alt1", "bge-m3", "нет в списке"),
        ("alt7", "x", "больше не настроено"),
        ("main", "two words", "без пробелов"),
        ("main", "", "не выбрана модель"),
    ],
)
def test_a_wrong_choice_is_refused(rows, gateway, endpoint, model, message):
    with pytest.raises(llm_choice.ChoiceError, match=message):
        llm_choice.save(ANNA.subject, endpoint, model)


def test_a_slot_removed_by_the_admin_stops_the_run_instead_of_switching_silently(rows, gateway, monkeypatch):
    llm_choice.save(ANNA.subject, "alt1", "spare-chat")
    monkeypatch.delenv("LLM_ALT1_API_BASE")
    monkeypatch.delenv("LLM_ALT1_API_KEY")

    with pytest.raises(cfg.ConfigError, match="больше не настроено"):
        as_user(ANNA, cfg.model_name)


def test_an_unavailable_database_is_named_not_replaced_by_the_server_model(rows, monkeypatch):
    def down(subject):
        raise llm_choice.db.DatabaseUnavailable("база Orbita недоступна (POSTGRES_URI): refused")

    monkeypatch.setattr(rows, "load", down)
    with pytest.raises(cfg.ConfigError, match="база Orbita недоступна"):
        as_user(ANNA, cfg.model_name)


def test_without_a_database_there_is_nothing_to_choose(rows, monkeypatch):
    monkeypatch.delenv("POSTGRES_URI")

    assert as_user(ANNA, cfg.model_name) == "qwen-main"
    with pytest.raises(llm_choice.ChoiceUnavailable, match="POSTGRES_URI"):
        llm_choice.save(ANNA.subject, "main", "qwen-big")


def test_env_prices_belong_to_the_server_model_only(rows, gateway, monkeypatch):
    monkeypatch.setenv("PRICE_CACHE_MISS_PER_MTOK", "1")
    monkeypatch.setenv("PRICE_OUTPUT_PER_MTOK", "2")
    monkeypatch.setenv("PRICE_CACHE_HIT_PER_MTOK", "0.1")

    assert as_user(ANNA, cfg.price_info).known is True
    llm_choice.save(ANNA.subject, "alt1", "spare-chat")
    with pytest.warns(UserWarning):
        assert as_user(ANNA, cfg.price_info).known is False


# --------------------------------------------------------------------------
# Клиент модели
# --------------------------------------------------------------------------
def test_a_new_choice_or_a_new_key_builds_a_new_client(rows, gateway, monkeypatch):
    built = []

    def factory(model, kwargs):
        built.append((model, kwargs["api_key"], kwargs.get("base_url")))
        return object()

    monkeypatch.setattr(providers, "_FACTORIES", {"openai": factory, "deepseek": factory, "anthropic": factory})
    monkeypatch.setattr(providers, "_CLIENTS", {})

    first = as_user(ANNA, providers.build_llm)
    assert as_user(ANNA, providers.build_llm) is first
    llm_choice.save(ANNA.subject, "alt1", "spare-chat")
    as_user(ANNA, providers.build_llm)
    monkeypatch.setenv("LLM_ALT1_API_KEY", "rotated-key")
    as_user(ANNA, providers.build_llm)

    assert built == [
        ("qwen-main", "main-key", GATEWAY),
        ("spare-chat", "spare-key", SPARE),
        ("spare-chat", "rotated-key", SPARE),
    ]


def test_the_connection_key_does_not_contain_the_api_key(rows):
    assert "main-key" not in providers.connection_key()


# --------------------------------------------------------------------------
# Список моделей
# --------------------------------------------------------------------------
def test_the_gateway_list_drops_models_one_cannot_chat_with(rows, gateway):
    listed, source, error = llm_choice.models(cfg.llm_endpoint("alt1"))

    assert (listed, source, error) == (("spare-chat", "spare-reasoner"), "gateway", None)
    assert gateway[0] == (SPARE + "/models", {"authorization": "Bearer spare-key"})
    # Второй раз — из кеша: ключ сервера не ходит на шлюз за каждым открытием страницы.
    llm_choice.models(cfg.llm_endpoint("alt1"))
    assert len(gateway) == 1


def test_a_declared_list_wins_and_a_silent_gateway_leaves_the_default(rows, gateway, monkeypatch):
    assert llm_choice.models(cfg.llm_endpoint("main"))[:2] == (("qwen-main", "qwen-big"), "list")

    monkeypatch.delenv("LLM_MODELS")
    listed, source, error = llm_choice.models(cfg.llm_endpoint("main"))
    assert (listed, source) == (("qwen-main",), "default")
    assert error == "шлюз ответил HTTP 503"
    # Без списка имя принимается как есть — проверить его можно «Проверить».
    llm_choice.save(ANNA.subject, "main", "qwen-anything")


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
@pytest.fixture
def client(rows, gateway, monkeypatch):
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


def test_the_page_shows_titles_and_models_but_no_addresses_or_keys(client):
    doc = client.get("/api/me/model", headers=bearer("anna"))

    assert doc.status_code == 200, doc.text
    assert "gateway.example" not in doc.text and "spare.example" not in doc.text
    assert "main-key" not in doc.text and "spare-key" not in doc.text
    body = doc.json()
    assert [row["title"] for row in body["endpoints"]] == ["Основное подключение", "Запасной шлюз"]
    assert body["current"] == {"endpoint": "main", "model": "qwen-main"}
    assert body["choice"] is None


def test_choosing_over_http(client):
    saved = client.put("/api/me/model", json={"endpoint": "alt1", "model": "spare-chat"}, headers=bearer("anna"))

    assert saved.status_code == 200, saved.text
    assert saved.json()["current"] == {"endpoint": "alt1", "model": "spare-chat"}
    assert client.get("/api/me/model", headers=bearer("tim")).json()["choice"] is None
    refused = client.put("/api/me/model", json={"endpoint": "alt1", "model": "nope"}, headers=bearer("anna"))
    assert refused.status_code == 400
    assert refused.json()["error_code"] == "model_invalid"
    reset = client.put("/api/me/model", json={"endpoint": None}, headers=bearer("anna"))
    assert reset.json()["choice"] is None


def test_a_check_reports_the_gateway_refusal_without_its_address(client, monkeypatch):
    class Refusing:
        def invoke(self, messages):
            raise RuntimeError(f"Connection error to {SPARE}/chat/completions")

    monkeypatch.setattr(providers, "build_probe", lambda endpoint, model, timeout: Refusing())

    checked = client.post("/api/me/model/check", json={"endpoint": "alt1", "model": "spare-chat"},
                          headers=bearer("anna"))

    assert checked.status_code == 200
    result = checked.json()["check"]
    assert result["ok"] is False
    assert "spare.example" not in result["detail"]


def test_the_page_needs_a_login(client):
    assert client.get("/api/me/model").status_code == 401


# --------------------------------------------------------------------------
# Настройки сервера: модель и подключения действуют без перезапуска
# --------------------------------------------------------------------------
@pytest.fixture
def settings_root(tmp_path: Path, monkeypatch):
    (tmp_path / ".env.example").write_text(
        "LLM_MODEL=\nLLM_API_BASE=\nLLM_API_KEY=\nLLM_ALT1_API_BASE=\nLLM_ALT1_API_KEY=\nAGENT_NAME=\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("LLM_MODEL=old-model\nAGENT_NAME=Orbita\n", encoding="utf-8")
    monkeypatch.setattr(settings_io, "_root", lambda: tmp_path)
    monkeypatch.setenv("LLM_MODEL", "old-model")
    monkeypatch.setenv("AGENT_NAME", "Orbita")
    return tmp_path


def test_model_settings_apply_at_once_and_the_rest_waits_for_a_restart(settings_root, monkeypatch):
    result = settings_io.save({"LLM_MODEL": "new-model", "AGENT_NAME": "Орбита"})

    assert result["applied"] == ["LLM_MODEL"]
    assert cfg.default_model_name() == "new-model"
    assert cfg.agent_name() == "Orbita"
    applied = {row["name"]: row for row in settings_io.applied()["applied"]}
    assert applied["LLM_MODEL"]["restart_required"] is False
    assert applied["AGENT_NAME"]["restart_required"] is True


def test_a_value_set_by_the_environment_is_not_overwritten(settings_root, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "pinned-by-systemd")

    result = settings_io.save({"LLM_MODEL": "new-model"})

    assert result["applied"] == []
    assert cfg.default_model_name() == "pinned-by-systemd"


def test_a_spare_address_cannot_be_changed_without_its_key(settings_root, monkeypatch):
    (settings_root / ".env").write_text(
        f"LLM_ALT1_API_BASE={SPARE}\nLLM_ALT1_API_KEY=spare-key\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="LLM_ALT1_API_KEY"):
        settings_io.save({"LLM_ALT1_API_BASE": "https://elsewhere.example.test/v1"})


@pytest.mark.parametrize("prefix", ["LLM_", "LLM_ALT1_"])
@pytest.mark.parametrize("pinned", ["API_KEY", "API_BASE", None])
def test_an_address_and_key_apply_together(settings_root, monkeypatch, prefix, pinned):
    base, key = prefix + "API_BASE", prefix + "API_KEY"
    before = {base: "https://old.example.test/v1", key: "file-key"}
    (settings_root / ".env").write_text(
        "".join(f"{name}={value}\n" for name, value in before.items()), encoding="utf-8"
    )
    for name, value in before.items():
        monkeypatch.setenv(name, value)
    if pinned:
        monkeypatch.setenv(prefix + pinned, "https://pinned.example.test/v1" if pinned == "API_BASE" else "environment-key")
    process_before = {name: os.environ[name] for name in before}
    updates = {base: "https://new.example.test/v1", key: "replacement-key"}

    result = settings_io.save(updates)

    assert result["applied"] == ([] if pinned else sorted(updates))
    assert {name: os.environ[name] for name in before} == (process_before if pinned else updates)
    assert settings_io._read_env_file() == updates


def test_put_settings_reports_only_what_still_needs_a_restart(settings_root, monkeypatch):
    monkeypatch.setenv("API_ADMIN_TOKEN", ADMIN)
    client = TestClient(api.app)

    response = client.put(
        "/api/settings",
        json={"values": {"LLM_MODEL": "new-model", "AGENT_NAME": "Орбита"}},
        headers=bearer(ADMIN),
    )

    assert response.status_code == 200, response.text
    assert response.json()["restart_required"] == ["AGENT_NAME"]
    assert json.loads(json.dumps(response.json()["applied"])) == ["LLM_MODEL"]
