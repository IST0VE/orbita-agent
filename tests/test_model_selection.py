"""
Модель выбирается из серверной LLM_MODEL в момент вызова ноды.

Клиент по-прежнему создаётся один раз на комбинацию (провайдер, модель,
temperature) — иначе на каждом ходе собирался бы новый HTTP-клиент. Но сама
модель из старой конфигурации треда не должна перекрывать серверную.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent import config as cfg
from agent import providers
from agent.graph import model_for, options


@pytest.fixture(autouse=True)
def fake_factories(monkeypatch: pytest.MonkeyPatch):
    """
    Подменяем фабрики классов LangChain: здесь проверяется выбор и кеширование,
    а не то, как ChatDeepSeek разбирает свои аргументы. Заодно ни один тест
    не пытается собрать настоящего клиента без ключа.
    """
    built = []

    class FakeModel:
        def __init__(self, provider, model, kwargs):
            self.provider = provider
            self.model = model
            self.kwargs = kwargs

        bound_tools = None

        def bind_tools(self, tools):
            self.bound_tools = tools
            return self

    def factory(provider):
        def build(model, kwargs):
            built.append((provider, model, kwargs["temperature"]))
            return FakeModel(provider, model, kwargs)

        return build

    monkeypatch.setattr(
        providers,
        "_FACTORIES",
        {name: factory(name) for name in ("deepseek", "openai", "anthropic")},
    )
    monkeypatch.setattr(providers, "_CLIENTS", {})
    monkeypatch.setattr("agent.nodes._BOUND", {})
    return built


# --------------------------------------------------------------------------
# Привязка инструментов
# --------------------------------------------------------------------------
def test_an_empty_tool_set_means_no_binding_at_all():
    """
    Пустой набор — это «не привязывать», а не «привязать пустой список».
    Разница не косметическая: набор уезжает в тело запроса, и эндпоинт без
    поддержки tool calling отказывает и на пустом.
    """
    assert model_for(None, ()).bound_tools is None
    assert model_for(None).bound_tools is not None


def test_a_pipeline_without_readers_asks_for_a_model_without_tools(monkeypatch):
    """
    Инструменты привязываются всем ролям конвейера ради общего префикса. Но
    там, где в цикл с инструментами не уходит ни одна роль, спрашивать некому,
    а схемы всё равно уезжают в каждый запрос. Тот же довод, по которому
    сборщик не заводит таким конвейерам узел `tools`.
    """
    from langchain_core.messages import AIMessage, HumanMessage

    from agent import audit_roles, nodes, roles

    asked = []

    class Chat:
        def invoke(self, messages):
            return AIMessage(content="документ этапа")

    def fake(config=None, tools=None):
        asked.append(tools)
        return Chat()

    monkeypatch.setattr(nodes, "model_for", fake)
    state = {"messages": [HumanMessage("задача")], "task": "задача"}

    nodes.make_role_node(audit_roles.FIRST, pipeline=audit_roles.PIPELINE)(state, {})
    nodes.make_role_node(roles.FIRST, pipeline=roles.PIPELINE)(state, {})

    assert asked[0] == ()
    assert [tool.name for tool in asked[1]] == [
        "list_task_files", "read_task_file", "jira_issue", "jira_search",
        "confluence_search", "confluence_page",
    ]


# --------------------------------------------------------------------------
# Ключ
# --------------------------------------------------------------------------
def test_key_falls_back_to_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-mini")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.7")

    assert providers.resolve_key() == ("openai", "gpt-mini", 0.7)


def test_one_field_can_be_overridden_alone(monkeypatch: pytest.MonkeyPatch):
    """Переопределение модели не должно требовать передавать остальные два поля."""
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")

    assert providers.resolve_key(model="deepseek-v4-pro") == (
        "deepseek",
        "deepseek-v4-pro",
        0.0,
    )


def test_unknown_provider_is_rejected():
    with pytest.raises(cfg.ConfigError, match="mistral"):
        providers.resolve_key(provider="mistral")


# --------------------------------------------------------------------------
# Кеш клиентов
# --------------------------------------------------------------------------
def test_same_key_reuses_the_client(fake_factories):
    first = providers.build_llm(model="m-1")
    second = providers.build_llm(model="m-1")

    assert first is second
    assert len(fake_factories) == 1


def test_different_model_gets_its_own_client(fake_factories):
    first = providers.build_llm(model="m-1")
    second = providers.build_llm(model="m-2")

    assert first is not second
    assert len(fake_factories) == 2


def test_temperature_is_part_of_the_key(fake_factories):
    providers.build_llm(model="m-1", temperature=0.0)
    providers.build_llm(model="m-1", temperature=0.9)

    assert [t for _, _, t in fake_factories] == [0.0, 0.9]


# --------------------------------------------------------------------------
# Серверная модель имеет приоритет над сохранёнными настройками треда
# --------------------------------------------------------------------------
@pytest.mark.parametrize("source", ["configurable", "context", "both"])
def test_stale_model_is_ignored(fake_factories, monkeypatch, source):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "qwen3.8-flash-next")
    stale = {"model": "qwen3.6-35b-a3b-fp8"}
    context = stale if source in ("context", "both") else {}
    config = {"configurable": stale} if source in ("configurable", "both") else {}
    monkeypatch.setattr("langgraph.runtime.get_runtime", lambda: SimpleNamespace(context=context))

    model = model_for(config)

    assert model.model == "qwen3.8-flash-next"
    assert fake_factories == [("openai", "qwen3.8-flash-next", 0.0)]
    assert stale == {"model": "qwen3.6-35b-a3b-fp8"}


def test_provider_and_temperature_come_from_configurable(fake_factories):
    model = model_for(
        {"configurable": {"provider": "openai", "model": "gpt-mini", "temperature": 0.3}}
    )

    assert (model.provider, model.model) == ("openai", cfg.DEFAULT_MODEL)
    assert fake_factories == [("openai", cfg.DEFAULT_MODEL, 0.3)]


def test_without_overrides_environment_wins(
    fake_factories, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("LLM_MODEL", "из-окружения")

    assert model_for(None).model == "из-окружения"
    assert model_for({}).model == "из-окружения"


def test_two_stale_threads_share_the_server_model(fake_factories, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "qwen3.8-flash-next")
    first = model_for({"configurable": {"thread_id": "a", "model": "m-a"}})
    second = model_for({"configurable": {"thread_id": "b", "model": "m-b"}})

    assert first.model == second.model == "qwen3.8-flash-next"
    assert first is second
    assert len(fake_factories) == 1


def test_server_model_change_does_not_reuse_stale_client(fake_factories, monkeypatch):
    config = {"configurable": {"model": "qwen3.6-35b-a3b-fp8"}}
    monkeypatch.setenv("LLM_MODEL", "qwen3.6-35b-a3b-fp8")
    first = model_for(config)
    monkeypatch.setenv("LLM_MODEL", "qwen3.8-flash-next")
    second = model_for(config)

    assert first.model == "qwen3.6-35b-a3b-fp8"
    assert second.model == "qwen3.8-flash-next"
    assert first is not second
    assert len(fake_factories) == 2


def test_document_header_uses_server_model(monkeypatch):
    from agent.documents import document_header

    monkeypatch.setenv("LLM_MODEL", "qwen3.8-flash-next")
    header = document_header({"configurable": {"model": "qwen3.6-35b-a3b-fp8"}})
    assert "qwen3.8-flash-next" in header
    assert "qwen3.6-35b-a3b-fp8" not in header


def test_studio_schema_does_not_offer_model_override():
    from agent.graph import graph

    assert "model" not in graph.get_context_jsonschema()["properties"]


def test_bound_model_is_cached_too(fake_factories):
    assert model_for({"configurable": {"model": "m-1"}}) is model_for(
        {"configurable": {"model": "m-1"}}
    )


# --------------------------------------------------------------------------
# Источники переопределений
# --------------------------------------------------------------------------
def test_options_reads_configurable():
    assert options({"configurable": {"model": "m-1", "input_dir": "task"}}) == {
        "input_dir": "task"
    }


def test_options_preserves_other_context_and_configurable_fields(monkeypatch):
    context = {"model": "old-context", "input_dir": "task", "temperature": 0.4}
    monkeypatch.setattr("langgraph.runtime.get_runtime", lambda: SimpleNamespace(context=context))
    assert options({"configurable": {"model": "old-config", "temperature": 0.2}}) == {
        "input_dir": "task", "temperature": 0.2,
    }


def test_options_survives_absent_config():
    assert options(None) == {}
    assert options({}) == {}
