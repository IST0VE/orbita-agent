"""
Метрики для Prometheus: что считается, под какими метками и кто их может читать.

Реестр `prometheus_client` один на процесс и копится через все тесты, поэтому
проверяется прирост, а не абсолютное значение.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import warnings
from pathlib import Path
from types import SimpleNamespace
from typing import TypedDict
from uuid import uuid4

import pytest
import requests
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt
from prometheus_client import REGISTRY, generate_latest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from agent import api, llm_pacing, logbook, metrics, request_pacing, settings_io

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
ADMIN = "test-only-admin-token-with-32-characters"
SCRAPER = "test-only-metrics-token"


def value(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


# --------------------------------------------------------------------------
# Модель
# --------------------------------------------------------------------------
def answer(**token_usage: int) -> LLMResult:
    """Ответ DeepSeek: счётчики кеша лежат в token_usage."""
    message = AIMessage("готово", response_metadata={"token_usage": token_usage})
    return LLMResult(generations=[[ChatGeneration(message=message)]])


def start(handler: metrics.LlmMetrics, graph: str, node: str, model: str = "test-model"):
    run = uuid4()
    handler.on_chat_model_start(
        {}, [[]], run_id=run,
        metadata={"graph_id": graph, "langgraph_node": node},
        invocation_params={"model": model},
    )
    return run


def test_a_model_answer_is_counted_with_tokens_and_money(monkeypatch):
    monkeypatch.setenv("PRICE_CACHE_HIT_PER_MTOK", "1")
    monkeypatch.setenv("PRICE_CACHE_MISS_PER_MTOK", "10")
    monkeypatch.setenv("PRICE_OUTPUT_PER_MTOK", "100")
    labels = {"graph": "prep", "node": "analyst"}
    before = {
        "ok": value("orbita_llm_requests_total", **labels, outcome="ok"),
        "hit": value("orbita_llm_tokens_total", **labels, kind="cache_hit"),
        "miss": value("orbita_llm_tokens_total", **labels, kind="cache_miss"),
        "output": value("orbita_llm_tokens_total", **labels, kind="output"),
        "usd": value("orbita_llm_cost_usd_total", **labels),
        "naive": value("orbita_llm_cost_without_cache_usd_total", **labels),
        "answers": value("orbita_llm_request_duration_seconds_count", graph="prep", model="test-model"),
        "flying": value("orbita_llm_requests_in_flight"),
    }
    handler = metrics.LlmMetrics()

    run = start(handler, "prep", "analyst")
    assert value("orbita_llm_requests_in_flight") == before["flying"] + 1
    handler.on_llm_end(
        answer(prompt_cache_hit_tokens=800, prompt_cache_miss_tokens=200,
               prompt_tokens=1000, completion_tokens=200, total_tokens=1200),
        run_id=run,
    )

    assert value("orbita_llm_requests_total", **labels, outcome="ok") == before["ok"] + 1
    assert value("orbita_llm_tokens_total", **labels, kind="cache_hit") == before["hit"] + 800
    assert value("orbita_llm_tokens_total", **labels, kind="cache_miss") == before["miss"] + 200
    assert value("orbita_llm_tokens_total", **labels, kind="output") == before["output"] + 200
    # 800 × $1 + 200 × $10 + 200 × $100 за миллион; без кеша весь вход — по $10.
    assert value("orbita_llm_cost_usd_total", **labels) == pytest.approx(before["usd"] + 0.0228)
    assert value("orbita_llm_cost_without_cache_usd_total", **labels) == pytest.approx(
        before["naive"] + 0.03
    )
    assert value(
        "orbita_llm_request_duration_seconds_count", graph="prep", model="test-model"
    ) == before["answers"] + 1
    assert value("orbita_llm_requests_in_flight") == before["flying"]


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (type("RateLimitError", (Exception,), {"status_code": 429})(), "rate_limited"),
        (type("APITimeoutError", (Exception,), {})(), "timeout"),
        (ValueError("что-то другое"), "error"),
    ],
)
def test_a_refusal_is_counted_by_its_kind(error, outcome):
    labels = {"graph": "audit", "node": "critic", "outcome": outcome}
    before = value("orbita_llm_requests_total", **labels)
    handler = metrics.LlmMetrics()

    handler.on_llm_error(error, run_id=start(handler, "audit", "critic"))

    assert value("orbita_llm_requests_total", **labels) == before + 1


def test_an_answer_at_an_unknown_price_is_not_counted_as_free(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "model-without-a-price")
    before_unpriced = value("orbita_llm_unpriced_requests_total", graph="jira")
    before_usd = value("orbita_llm_cost_usd_total", graph="jira", node="writer")
    handler = metrics.LlmMetrics()

    with warnings.catch_warnings():
        # costmeter предупреждает о модели без тарифа — здесь это и проверяется.
        warnings.simplefilter("ignore")
        handler.on_llm_end(
            answer(prompt_tokens=100, completion_tokens=10), run_id=start(handler, "jira", "writer")
        )

    assert value("orbita_llm_unpriced_requests_total", graph="jira") == before_unpriced + 1
    assert value("orbita_llm_cost_usd_total", graph="jira", node="writer") == before_usd


def test_a_call_outside_a_graph_is_still_counted():
    before = value("orbita_llm_requests_total", graph="none", node="none", outcome="ok")
    handler = metrics.LlmMetrics()
    run = uuid4()

    handler.on_chat_model_start({}, [[]], run_id=run)
    handler.on_llm_end(answer(), run_id=run)

    assert value("orbita_llm_requests_total", graph="none", node="none", outcome="ok") == before + 1


def test_queue_wait_and_the_gateway_window_are_exported(monkeypatch):
    monkeypatch.setenv("LLM_REQUESTS_PER_MINUTE", "16")
    llm_pacing.reset(llm_pacing.MinuteBudget(clock=lambda: 100.0, sleep=lambda _: None))
    try:
        waits = value("orbita_llm_queue_wait_seconds_count")
        llm_pacing.Pacer().on_chat_model_start({}, [[]], run_id=uuid4())

        assert value("orbita_llm_queue_wait_seconds_count") == waits + 1
        assert value("orbita_llm_window_requests") == 1
        assert value("orbita_llm_window_tokens") > 0
        assert value("orbita_llm_limit_requests_per_minute") == 16
        assert value("orbita_llm_gateway_pause_seconds") == 0
    finally:
        llm_pacing.reset()


# --------------------------------------------------------------------------
# Графы
# --------------------------------------------------------------------------
class Counter(TypedDict):
    value: int


def graph(name: str):
    """ok → ask (пауза на подтверждение) или ok → boom (исключение)."""

    def ok(state: Counter):
        return {"value": state["value"] + 1}

    def ask(state: Counter):
        return {"value": interrupt("подтвердите")}

    def boom(state: Counter):
        raise RuntimeError("узел упал")

    builder = StateGraph(Counter)
    builder.add_node("ok", ok)
    builder.add_node("ask", ask)
    builder.add_node("boom", boom)
    builder.add_edge(START, "ok")
    builder.add_conditional_edges("ok", lambda state: "boom" if state["value"] > 10 else "ask")
    builder.add_edge("ask", END)
    builder.add_edge("boom", END)
    return metrics.observe(builder.compile(checkpointer=InMemorySaver()), name)


def runs(name: str, outcome: str) -> float:
    return value("orbita_graph_runs_total", graph=name, outcome=outcome)


def test_series_of_a_graph_exist_before_its_first_run():
    """Иначе increase() не покажет первый прогон: ряд появился бы сразу с единицей."""
    graph("t_prepared")

    for outcome in metrics.RUN_OUTCOMES:
        assert REGISTRY.get_sample_value(
            "orbita_graph_runs_total", {"graph": "t_prepared", "outcome": outcome}
        ) == 0
    for node in ("ok", "ask", "boom"):
        labels = {"graph": "t_prepared", "node": node}
        for outcome in metrics.NODE_OUTCOMES:
            assert REGISTRY.get_sample_value(
                "orbita_graph_node_runs_total", {**labels, "outcome": outcome}
            ) == 0
        # Время тоже: узел, отработавший один раз, иначе остался бы без него.
        assert REGISTRY.get_sample_value("orbita_graph_node_duration_seconds_count", labels) == 0
        assert REGISTRY.get_sample_value("orbita_llm_cost_usd_total", labels) == 0
        assert REGISTRY.get_sample_value(
            "orbita_llm_tokens_total", {**labels, "kind": "output"}
        ) == 0
    assert REGISTRY.get_sample_value(
        "orbita_llm_tokens_total", {"graph": "t_prepared", "node": "__start__", "kind": "output"}
    ) is None


def test_a_pause_a_resume_and_a_failure_are_three_different_outcomes():
    app = graph("t_outcomes")
    paused = {"configurable": {"thread_id": "paused"}}

    app.invoke({"value": 1}, paused)
    assert runs("t_outcomes", "interrupted") == 1
    assert runs("t_outcomes", "completed") == 0

    app.invoke(Command(resume=5), paused)
    assert runs("t_outcomes", "completed") == 1

    with pytest.raises(RuntimeError):
        app.invoke({"value": 20}, {"configurable": {"thread_id": "failed"}})
    assert runs("t_outcomes", "failed") == 1

    def node_runs(node: str, outcome: str) -> float:
        return value("orbita_graph_node_runs_total", graph="t_outcomes", node=node, outcome=outcome)

    assert node_runs("ok", "ok") == 2
    assert node_runs("boom", "failed") == 1
    # Пауза узла — не ошибка: узел встал на подтверждение, а после ответа отработал.
    assert node_runs("ask", "interrupted") == 1
    assert node_runs("ask", "ok") == 1
    assert node_runs("ask", "failed") == 0
    assert value("orbita_graph_node_duration_seconds_count", graph="t_outcomes", node="ok") == 2
    assert value("orbita_graph_node_duration_seconds_count", graph="t_outcomes", node="ask") == 1
    assert value("orbita_graph_run_duration_seconds_count", graph="t_outcomes", outcome="failed") == 1
    assert value("orbita_graph_runs_in_progress", graph="t_outcomes") == 0


def test_the_async_server_path_is_counted_the_same_way():
    """Сервер LangGraph зовёт astream: обработчики идут через асинхронный менеджер."""
    app = graph("t_async")
    config = {"configurable": {"thread_id": "async"}}

    asyncio.run(app.ainvoke({"value": 1}, config))
    asyncio.run(app.ainvoke(Command(resume=2), config))

    assert runs("t_async", "interrupted") == 1
    assert runs("t_async", "completed") == 1
    assert value("orbita_graph_runs_in_progress", graph="t_async") == 0


@tool
def lookup(key: str) -> str:
    """Найти запись по ключу."""
    return f"запись {key}"


def test_tool_calls_are_counted_by_tool_inside_the_tools_node_and_outside_it():
    """Поход роли в Jira идёт внутри узла `tools`: без этого был бы виден только узел."""

    def ask(state: MessagesState):
        calls = [{"name": "lookup", "args": {"key": key}, "id": key} for key in ("ORB-1", "ORB-2")]
        return {"messages": [AIMessage("", tool_calls=calls)]}

    def direct(state: MessagesState, config: RunnableConfig):
        # Как execute_tools в nt_run: инструмент зовёт код узла, а не ToolNode.
        lookup.invoke({"key": "ORB-3"}, config=config)
        return {}

    builder = StateGraph(MessagesState)
    builder.add_node("ask", ask)
    builder.add_node("tools", ToolNode([lookup]))
    builder.add_node("direct", direct)
    builder.add_edge(START, "ask")
    builder.add_edge("ask", "tools")
    builder.add_edge("tools", "direct")
    builder.add_edge("direct", END)
    app = metrics.observe(builder.compile(), "t_tools", tools=["write_plan"])

    # Ряды есть до первого вызова: инструменты ToolNode найдены сами, остальные переданы.
    for name in ("lookup", "write_plan"):
        labels = {"graph": "t_tools", "tool": name}
        assert REGISTRY.get_sample_value("orbita_tool_calls_total", labels) == 0
        assert REGISTRY.get_sample_value("orbita_tool_duration_seconds_count", labels) == 0

    app.invoke({"messages": [HumanMessage("найди")]})

    assert value("orbita_tool_calls_total", graph="t_tools", tool="lookup") == 3
    assert value("orbita_tool_duration_seconds_count", graph="t_tools", tool="lookup") == 3
    assert value("orbita_graph_node_runs_total", graph="t_tools", node="tools", outcome="ok") == 1


def test_every_served_graph_is_observed_under_its_langgraph_json_key():
    """`graph_id` сервера и имя в метриках графа обязаны совпасть — на этом стоит доска."""
    import importlib

    served = json.loads((ROOT / "langgraph.json").read_text(encoding="utf-8"))["graphs"]
    for key, reference in served.items():
        path, attribute = reference.split(":")
        module = importlib.import_module("agent." + Path(path).stem)
        callbacks = getattr(module, attribute).config.get("callbacks") or []
        names = [item.graph for item in callbacks if isinstance(item, metrics.GraphMetrics)]
        assert names == [key], f"{reference}: граф не обёрнут metrics.observe(..., {key!r})"


# --------------------------------------------------------------------------
# Доступ к /metrics и метрики HTTP
# --------------------------------------------------------------------------
@pytest.fixture
def server(monkeypatch) -> TestClient:
    """Наши middleware в том же порядке, что на сервере, и роут /metrics как у LangGraph."""
    monkeypatch.setenv("API_ADMIN_TOKEN", ADMIN)
    monkeypatch.setenv("METRICS_TOKEN", SCRAPER)

    async def scrape(request):
        return PlainTextResponse(generate_latest().decode())

    app = Starlette(
        routes=[Route("/metrics", scrape), *api.routes],
        middleware=api.app.user_middleware,
    )
    return TestClient(app)


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_the_metrics_token_opens_the_metrics_and_nothing_else(server):
    scraped = server.get("/metrics", headers=bearer(SCRAPER))

    assert scraped.status_code == 200
    assert "orbita_llm_requests_total" in scraped.text
    assert "orbita_graph_runs_total" in scraped.text
    assert server.get("/metrics").status_code == 401
    assert server.get("/api/settings", headers=bearer(SCRAPER)).status_code == 401
    assert server.get("/api/logs", headers=bearer(SCRAPER)).status_code == 401
    assert server.post("/metrics", headers=bearer(SCRAPER)).status_code == 401
    # Обычный вход метрики тоже открывает.
    assert server.get("/metrics", headers=bearer(ADMIN)).status_code == 200


def test_a_short_metrics_token_is_not_accepted(server, monkeypatch):
    monkeypatch.setenv("METRICS_TOKEN", "short")

    assert server.get("/metrics", headers=bearer("short")).status_code == 401


def test_http_requests_are_labelled_by_route_template_not_by_path(server):
    labels = {"route": "/api/inputs/{task}/file", "method": "GET"}
    before = value("orbita_http_requests_total", **labels, status="401")
    probes = value("orbita_http_requests_total", route="other", method="GET", status="401")
    scrapes = value("orbita_http_request_duration_seconds_count", route="/metrics", method="GET")

    server.get("/api/inputs/one-task/file")
    server.get("/api/inputs/another-task/file")
    server.get("/wp-login.php")
    server.get("/metrics", headers=bearer(SCRAPER))

    assert value("orbita_http_requests_total", **labels, status="401") == before + 2
    assert value("orbita_http_requests_total", route="other", method="GET", status="401") == probes + 1
    # Сбор метрик и опрос здоровья в метрики HTTP не попадают.
    assert value(
        "orbita_http_request_duration_seconds_count", route="/metrics", method="GET"
    ) == scrapes == 0


def test_the_metrics_token_is_not_editable_from_the_interface():
    assert not settings_io.can_edit("METRICS_TOKEN")
    assert settings_io.is_secret("METRICS_TOKEN")


# --------------------------------------------------------------------------
# Интеграции и журнал
# --------------------------------------------------------------------------
def test_each_attempt_to_jira_is_counted_with_its_status(monkeypatch):
    replies = iter([503, 200])

    def session_request(method, url, **kwargs):
        response = requests.Response()
        response.status_code = next(replies)
        response._content = b"{}"
        return response

    monkeypatch.setattr(request_pacing, "_session", SimpleNamespace(request=session_request))
    monkeypatch.setenv("ATLASSIAN_REQUEST_INTERVAL_S", "0")
    labels = {"system": "jira", "method": "GET"}
    before = {code: value("orbita_integration_requests_total", **labels, status=code)
              for code in ("503", "200")}

    request_pacing.send("GET", "https://jira.example.test/rest", timeout=5, system="jira")
    request_pacing.send("GET", "https://jira.example.test/rest", timeout=5, system="jira")

    for code in ("503", "200"):
        assert value("orbita_integration_requests_total", **labels, status=code) == before[code] + 1


def test_a_request_without_an_answer_is_counted_as_network(monkeypatch):
    def refused(method, url, **kwargs):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(request_pacing, "_session", SimpleNamespace(request=refused))
    monkeypatch.setenv("ATLASSIAN_REQUEST_INTERVAL_S", "0")
    labels = {"system": "confluence", "method": "POST", "status": "network"}
    before = value("orbita_integration_requests_total", **labels)

    with pytest.raises(requests.ConnectionError):
        request_pacing.send("POST", "https://wiki.example.test/rest", timeout=5, system="confluence")

    assert value("orbita_integration_requests_total", **labels) == before + 1


def test_warnings_and_errors_of_the_log_are_counted():
    handler = logbook.Logbook()
    labels = {"level": "error", "logger": "agent.nodes"}
    before = value("orbita_log_records_total", **labels)
    quiet = value("orbita_log_records_total", level="info", logger="agent.nodes")

    for level in (logging.ERROR, logging.INFO):
        handler.emit(logging.LogRecord("agent.nodes.sub", level, __file__, 1, "узел упал", (), None))

    assert value("orbita_log_records_total", **labels) == before + 1
    assert value("orbita_log_records_total", level="info", logger="agent.nodes") == quiet == 0


# --------------------------------------------------------------------------
# Compose, Prometheus и Grafana
# --------------------------------------------------------------------------
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
PROMETHEUS = yaml.safe_load((ROOT / "config/prometheus/prometheus.yml").read_text(encoding="utf-8"))
DATASOURCES = yaml.safe_load(
    (ROOT / "config/grafana/provisioning/datasources/prometheus.yml").read_text(encoding="utf-8")
)
PROVIDERS = yaml.safe_load(
    (ROOT / "config/grafana/provisioning/dashboards/orbita.yml").read_text(encoding="utf-8")
)
DASHBOARD = json.loads((ROOT / "config/grafana/dashboards/orbita.json").read_text(encoding="utf-8"))


def test_monitoring_is_an_opt_in_profile_bound_to_loopback():
    for name, port in (("prometheus", ":9090"), ("grafana", ":3000")):
        service = COMPOSE[name]
        assert service["profiles"] == ["monitoring"]
        assert service["cap_drop"] == ["ALL"]
        assert all(item.startswith("127.0.0.1:") for item in service["ports"])
        assert service["ports"][0].endswith(port)
        assert "healthcheck" in service


def test_prometheus_scrapes_the_agent_with_the_token_its_entrypoint_writes():
    (job,) = PROMETHEUS["scrape_configs"]
    command = COMPOSE["prometheus"]["command"][0]

    assert job["static_configs"][0]["targets"] == ["agent:2024"]
    assert job["metrics_path"] == "/metrics"
    assert f"> {job['authorization']['credentials_file']}" in command
    assert "METRICS_TOKEN" in COMPOSE["prometheus"]["environment"]
    # Смонтирована папка, где лежит конфиг, который читает Prometheus.
    assert "./config/prometheus:/etc/prometheus:ro" in COMPOSE["prometheus"]["volumes"]
    assert "--config.file=/etc/prometheus/prometheus.yml" in command


def test_grafana_finds_its_datasource_and_dashboard():
    (source,) = DATASOURCES["datasources"]
    (provider,) = PROVIDERS["providers"]
    mounts = {item.split(":")[1]: item.split(":")[0] for item in COMPOSE["grafana"]["volumes"]}

    assert source["url"] == "http://prometheus:9090"
    assert mounts[provider["options"]["path"]] == "./config/grafana/dashboards"
    home = COMPOSE["grafana"]["environment"]["GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH"]
    assert home == provider["options"]["path"] + "/orbita.json"
    uids = {
        panel["datasource"]["uid"]
        for panel in DASHBOARD["panels"]
        if panel["type"] != "row"
    }
    assert uids == {source["uid"]}


def _exported() -> set[str]:
    names: set[str] = set()
    for family in REGISTRY.collect():
        if family.type == "counter":
            names.add(family.name + "_total")
        elif family.type == "histogram":
            names.update(family.name + suffix for suffix in ("_bucket", "_sum", "_count"))
        elif family.type == "info":
            names.add(family.name + "_info")
        else:
            names.add(family.name)
    return names


def test_the_dashboard_asks_only_for_metrics_the_agent_exports():
    """Переименованная метрика иначе молча превращает панель в «No data»."""
    queries = [
        target["expr"]
        for panel in DASHBOARD["panels"]
        for target in panel.get("targets", [])
    ]
    queries += [variable["definition"] for variable in DASHBOARD["templating"]["list"]]
    used = {name for query in queries for name in re.findall(r"\borbita_\w+", query)}

    assert used, "в доске нет ни одного запроса к метрикам Orbita"
    assert used <= _exported(), used - _exported()
