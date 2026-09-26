"""
Метрики Orbita для Prometheus: деньги, токены, модель, прогоны, интеграции.

Отдаются тем же адресом, что и метрики самого сервера LangGraph, — `/metrics`
на порту агента. Сервер выгружает глобальный реестр `prometheus_client`, и всё,
что зарегистрировано здесь, попадает в ту же выдачу: второй порт и второй
HTTP-сервер не нужны. Prometheus ходит за ними с `METRICS_TOKEN` (security.py),
графики лежат в config/grafana, список метрик — в docs/MONITORING.md.

Сервер сам знает про HTTP своих роутов, воркеры, память и CPU процесса
(`lg_api_*`, `process_*`). Здесь то, чего он знать не может: сколько стоил
вызов модели и сколько сэкономил кеш, какая роль тратит больше всех, сколько
прогонов закончилось ошибкой, где запрос ждал очереди шлюза, как отвечают
Jira и Confluence.

Метки берутся только из закрытых множеств: имя графа и узла, модель, система,
исход. Тред, пользователь и путь с идентификатором в метку не попадают: каждое
новое значение — новый временной ряд, и Prometheus хранит их все.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import metadata as dist
from threading import Lock
from typing import Any
from uuid import UUID

import prometheus_client
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from langgraph.errors import GraphBubbleUp, GraphInterrupt
from langgraph.prebuilt import ToolNode
from prometheus_client import Counter, Gauge, Histogram
from prometheus_client.core import GaugeMetricFamily, InfoMetricFamily
from prometheus_client.registry import REGISTRY, Collector
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from agent import config as cfg
from agent import cost

# Ряды `*_created` — время создания каждого счётчика. Сервер отдаёт метрики в
# классическом текстовом формате, где Prometheus их не использует, а строк в
# выдаче от них вдвое больше.
prometheus_client.disable_created_metrics()

#: Метка для вызова, сделанного вне графа или вне узла: демо, тесты, скрипты.
NONE = "none"

LLM_OUTCOMES = ("ok", "rate_limited", "timeout", "error")
TOKEN_KINDS = ("cache_hit", "cache_miss", "cache_write", "output")
RUN_OUTCOMES = ("completed", "interrupted", "failed", "cancelled")
NODE_OUTCOMES = ("ok", "failed", "interrupted")

# --------------------------------------------------------------------------
# Модель
# --------------------------------------------------------------------------
LLM_REQUESTS = Counter(
    "orbita_llm_requests",
    "Запросы к модели по графу, узлу и исходу (ok, rate_limited, timeout, error)",
    ["graph", "node", "outcome"],
)
LLM_LATENCY = Histogram(
    "orbita_llm_request_duration_seconds",
    "Время ответа модели без ожидания в очереди шлюза",
    ["graph", "model"],
    buckets=(0.5, 1, 2.5, 5, 10, 20, 30, 60, 120, 180, 300, 600),
)
LLM_IN_FLIGHT = Gauge("orbita_llm_requests_in_flight", "Запросы к модели, ждущие ответа")
LLM_QUEUE_WAIT = Histogram(
    "orbita_llm_queue_wait_seconds",
    "Сколько запрос ждал места в минутном окне шлюза до отправки",
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 120, 300),
)
LLM_TOKENS = Counter(
    "orbita_llm_tokens",
    "Токены по статьям: cache_hit, cache_miss, cache_write — вход, output — ответ",
    ["graph", "node", "kind"],
)
LLM_COST = Counter(
    "orbita_llm_cost_usd",
    "Стоимость вызовов модели по тарифу на момент вызова, $",
    ["graph", "node"],
)
LLM_COST_WITHOUT_CACHE = Counter(
    "orbita_llm_cost_without_cache_usd",
    "Во что обошлись бы те же вызовы без кеша: весь вход по цене промаха, $",
    ["graph", "node"],
)
LLM_UNPRICED = Counter(
    "orbita_llm_unpriced_requests",
    "Ответы модели по неизвестному тарифу: в стоимость не вошли",
    ["graph"],
)

# --------------------------------------------------------------------------
# Графы
# --------------------------------------------------------------------------
GRAPH_RUNS = Counter(
    "orbita_graph_runs",
    "Прогоны графа по исходу; продолжение после паузы — новый прогон",
    ["graph", "outcome"],
)
GRAPH_RUN_DURATION = Histogram(
    "orbita_graph_run_duration_seconds",
    "Длительность прогона до конца, паузы или ошибки",
    ["graph", "outcome"],
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600, 7200),
)
GRAPH_RUNS_ACTIVE = Gauge("orbita_graph_runs_in_progress", "Прогоны, идущие сейчас", ["graph"])
# Узлы бывают и в миллисекунды (ворота, контекст), и в десятки минут (роль с
# инструментами под лимитом шлюза): корзины покрывают обе крайности.
NODE_DURATION = Histogram(
    "orbita_graph_node_duration_seconds",
    "Длительность узла графа, завершившегося без ошибки",
    ["graph", "node"],
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600, 1800),
)
NODE_RUNS = Counter(
    "orbita_graph_node_runs",
    "Запуски узла по исходу: ok, failed — исключение, interrupted — пауза на подтверждение",
    ["graph", "node", "outcome"],
)
TOOL_CALLS = Counter(
    "orbita_tool_calls",
    "Вызовы инструментов моделью: файлы задачи, Jira, Confluence, метрики НТ",
    ["graph", "tool"],
)
TOOL_DURATION = Histogram(
    "orbita_tool_duration_seconds",
    "Время одного вызова инструмента",
    ["graph", "tool"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)

# --------------------------------------------------------------------------
# Интеграции, HTTP API, журнал
# --------------------------------------------------------------------------
INTEGRATION_REQUESTS = Counter(
    "orbita_integration_requests",
    "HTTP-запросы к Jira и Confluence по коду ответа; network — ответа не было",
    ["system", "method", "status"],
)
INTEGRATION_LATENCY = Histogram(
    "orbita_integration_request_duration_seconds",
    "Время одного HTTP-запроса к Jira или Confluence",
    ["system", "method"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)
HTTP_REQUESTS = Counter(
    "orbita_http_requests",
    "Запросы к API агента, включая отказы входа",
    ["route", "method", "status"],
)
HTTP_LATENCY = Histogram(
    "orbita_http_request_duration_seconds",
    "Время до начала ответа API; у потока прогона — до первого байта",
    ["route", "method"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
LOG_RECORDS = Counter(
    "orbita_log_records",
    "Предупреждения и ошибки журнала сервера",
    ["level", "logger"],
)


# --------------------------------------------------------------------------
# Модель: callback на клиенте
# --------------------------------------------------------------------------
def _model(params: dict | None) -> str:
    params = params or {}
    name = params.get("model") or params.get("model_name") or ""
    return str(name)[:80] or NONE


def _llm_outcome(error: BaseException) -> str:
    response = getattr(error, "response", None)
    status = getattr(error, "status_code", None) or getattr(response, "status_code", None)
    if status == 429:
        return "rate_limited"
    # openai.APITimeoutError, httpx.ReadTimeout и их родня у других SDK.
    if "timeout" in type(error).__name__.lower():
        return "timeout"
    return "error"


def _usage(response: LLMResult) -> dict[str, int]:
    """Счётчики ответа тем же разбором, что и учёт в состоянии треда."""
    total: dict[str, int] = {}
    for generations in response.generations:
        for generation in generations:
            message = getattr(generation, "message", None)
            if message is None:
                continue
            for key, value in cost.extract_usage(message).items():
                total[key] = total.get(key, 0) + value
    return total


@dataclass(frozen=True)
class _Call:
    began: float
    graph: str
    node: str
    model: str


class LlmMetrics(BaseCallbackHandler):
    """
    Каждый запрос к модели: исход, время, токены и деньги.

    Вешается на клиента в `providers.build_llm` рядом с `Pacer` и после него:
    ожидание в очереди шлюза в время ответа не входит, его меряет сам `Pacer`.
    Граф и узел берутся из метаданных прогона — `graph_id` кладёт сервер
    LangGraph, `langgraph_node` — сам граф.

    Деньги считаются так же, как в состоянии треда (`cost.extract_usage`,
    тариф на момент вызова), но это не второй источник правды для бюджета:
    ворота бюджета смотрят на тред, а здесь — сумма по всем тредам сразу.
    """

    run_inline = True
    ignore_chain = True
    ignore_agent = True
    ignore_retriever = True
    ignore_retry = True
    ignore_custom_event = True

    def __init__(self) -> None:
        self._calls: dict[UUID, _Call] = {}
        self._lock = Lock()

    def on_chat_model_start(
        self,
        serialized: dict,
        messages: list[list[Any]],
        *,
        run_id: UUID,
        metadata: dict | None = None,
        invocation_params: dict | None = None,
        **kwargs: Any,
    ) -> None:
        meta = metadata or {}
        call = _Call(
            time.perf_counter(),
            str(meta.get("graph_id") or NONE),
            str(meta.get("langgraph_node") or NONE),
            _model(invocation_params),
        )
        with self._lock:
            self._calls[run_id] = call
        LLM_IN_FLIGHT.inc()

    def _pop(self, run_id: UUID) -> _Call | None:
        with self._lock:
            call = self._calls.pop(run_id, None)
        if call is not None:
            LLM_IN_FLIGHT.dec()
            LLM_LATENCY.labels(call.graph, call.model).observe(time.perf_counter() - call.began)
        return call

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        call = self._pop(run_id)
        if call is None:
            return
        LLM_REQUESTS.labels(call.graph, call.node, "ok").inc()
        usage = _usage(response)
        for kind in TOKEN_KINDS:
            if usage.get(kind):
                LLM_TOKENS.labels(call.graph, call.node, kind).inc(usage[kind])
        if not cfg.price_info().complete:
            LLM_UNPRICED.labels(call.graph).inc()
            return
        LLM_COST.labels(call.graph, call.node).inc(cost.estimate_cost(usage))
        LLM_COST_WITHOUT_CACHE.labels(call.graph, call.node).inc(cost.naive_cost(usage))

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        call = self._pop(run_id)
        if call is not None:
            LLM_REQUESTS.labels(call.graph, call.node, _llm_outcome(error)).inc()


#: Один на процесс, как и `Pacer`: состояние у него — только незавершённые вызовы.
LLM = LlmMetrics()


# --------------------------------------------------------------------------
# Графы: callback на скомпилированном графе
# --------------------------------------------------------------------------
@dataclass
class _Run:
    began: float
    interrupted: bool = False


class GraphMetrics(BaseCallbackHandler):
    """
    Прогоны графа, его узлы и инструменты, которые зовёт модель.

    Корень прогона — цепочка без родителя, узлы — её прямые потомки с тем же
    именем, что в `langgraph_node`. Всё глубже (подграфы, цепочки внутри узла)
    пропускается. Пауза на подтверждение приходит как `GraphInterrupt` из узла,
    а сам прогон заканчивается штатно — поэтому исход корня помнит, что один
    из его узлов остановился.

    Инструменты считаются на любой глубине: поход роли в Jira или Confluence
    идёт внутри узла `tools`, и без этого был бы виден только сам узел.
    Исхода у инструмента нет намеренно: недоступную Jira инструмент сообщает
    модели текстом, а не исключением, и «ошибок: 0» вводило бы в заблуждение.
    Отказы систем видны в `orbita_integration_requests_total`.
    """

    run_inline = True
    ignore_llm = True
    ignore_chat_model = True
    # События инструментов LangChain шлёт под этим флагом.
    ignore_agent = False
    ignore_retriever = True
    ignore_retry = True
    ignore_custom_event = True

    def __init__(self, graph: str) -> None:
        self.graph = graph
        self._runs: dict[UUID, _Run] = {}
        self._nodes: dict[UUID, tuple[UUID, str, float]] = {}
        self._tools: dict[UUID, tuple[str, float]] = {}
        self._lock = Lock()

    def on_chain_start(
        self,
        serialized: dict | None,
        inputs: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        metadata: dict | None = None,
        **kwargs: Any,
    ) -> None:
        now = time.perf_counter()
        if parent_run_id is None:
            with self._lock:
                self._runs[run_id] = _Run(now)
            GRAPH_RUNS_ACTIVE.labels(self.graph).inc()
            return
        name = str(kwargs.get("name") or "")
        if name.startswith("__") or (metadata or {}).get("langgraph_node") != name:
            return
        with self._lock:
            if parent_run_id in self._runs:
                self._nodes[run_id] = (parent_run_id, name, now)

    def on_chain_end(self, outputs: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._finish(run_id, None)

    def on_tool_start(
        self, serialized: dict | None, input_str: str, *, run_id: UUID, **kwargs: Any
    ) -> None:
        name = str((serialized or {}).get("name") or kwargs.get("name") or NONE)[:64]
        with self._lock:
            self._tools[run_id] = (name, time.perf_counter())

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._tool_done(run_id)

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._tool_done(run_id)

    def _tool_done(self, run_id: UUID) -> None:
        with self._lock:
            found = self._tools.pop(run_id, None)
        if found is not None:
            name, began = found
            TOOL_CALLS.labels(self.graph, name).inc()
            TOOL_DURATION.labels(self.graph, name).observe(time.perf_counter() - began)

    def on_chain_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._finish(run_id, error)

    def _finish(self, run_id: UUID, error: BaseException | None) -> None:
        elapsed_at = time.perf_counter()
        with self._lock:
            node = self._nodes.pop(run_id, None)
            run = None if node else self._runs.pop(run_id, None)
            if node and isinstance(error, GraphInterrupt) and node[0] in self._runs:
                self._runs[node[0]].interrupted = True
            if run is not None:
                # Узлы, чьё завершение не пришло: прогон отменили посреди шага.
                for left in [key for key, value in self._nodes.items() if value[0] == run_id]:
                    del self._nodes[left]
        if node is not None:
            _, name, began = node
            if error is None:
                NODE_RUNS.labels(self.graph, name, "ok").inc()
                NODE_DURATION.labels(self.graph, name).observe(elapsed_at - began)
            elif isinstance(error, GraphInterrupt):
                NODE_RUNS.labels(self.graph, name, "interrupted").inc()
            elif not isinstance(error, (GraphBubbleUp, asyncio.CancelledError)):
                NODE_RUNS.labels(self.graph, name, "failed").inc()
            return
        if run is None:
            return
        if error is None:
            outcome = "interrupted" if run.interrupted else "completed"
        elif isinstance(error, GraphBubbleUp):
            outcome = "interrupted"
        elif isinstance(error, asyncio.CancelledError):
            outcome = "cancelled"
        else:
            outcome = "failed"
        GRAPH_RUNS_ACTIVE.labels(self.graph).dec()
        GRAPH_RUNS.labels(self.graph, outcome).inc()
        GRAPH_RUN_DURATION.labels(self.graph, outcome).observe(elapsed_at - run.began)


def _prepare(graph: str, nodes: list[str], tools: list[str]) -> None:
    """
    Завести ряды с нулём до первого события.

    `increase()` в Prometheus считает прирост от первой точки ряда. Ряд,
    появившийся сразу с единицей, этой единицы не покажет: первый упавший
    прогон или первый вызов роли за время жизни процесса пропал бы с графиков.
    Узлы и инструменты графа известны заранее, поэтому ряды по ним создаются
    при импорте — и гистограммы времени тоже: иначе узел, отработавший за
    процесс один раз (подтверждение, публикация), остался бы без времени.
    Это несколько тысяч строк в выдаче /metrics; сбор раз в 15 секунд их
    переносит без труда.
    """
    for outcome in RUN_OUTCOMES:
        GRAPH_RUNS.labels(graph, outcome)
    GRAPH_RUNS_ACTIVE.labels(graph)
    LLM_UNPRICED.labels(graph)
    for tool in tools:
        TOOL_CALLS.labels(graph, tool)
        TOOL_DURATION.labels(graph, tool)
    for node in nodes:
        NODE_DURATION.labels(graph, node)
        for outcome in NODE_OUTCOMES:
            NODE_RUNS.labels(graph, node, outcome)
        LLM_COST.labels(graph, node)
        LLM_COST_WITHOUT_CACHE.labels(graph, node)
        for outcome in LLM_OUTCOMES:
            LLM_REQUESTS.labels(graph, node, outcome)
        for kind in TOKEN_KINDS:
            LLM_TOKENS.labels(graph, node, kind)


def observe(graph: Any, name: str, *, tools: Iterable[Any] = ()) -> Any:
    """
    Граф с метриками прогонов, узлов и инструментов. `name` — ключ графа в langgraph.json.

    Ключ совпадает с `graph_id`, который сервер кладёт в метаданные прогона и
    по которому `LlmMetrics` подписывает вызовы модели: ряды одного графа
    сходятся на одной доске.

    Инструменты узла `ToolNode` находятся сами. `tools` — те, что граф зовёт
    из своего кода, мимо `ToolNode`: их из собранного графа не видно.
    """
    names = {str(getattr(item, "name", item)) for item in tools}
    for spec in graph.builder.nodes.values():
        if isinstance(spec.runnable, ToolNode):
            names.update(spec.runnable.tools_by_name)
    _prepare(name, [node for node in graph.nodes if not node.startswith("__")], sorted(names))
    return graph.with_config({"callbacks": [GraphMetrics(name)]})


# --------------------------------------------------------------------------
# Интеграции и журнал
# --------------------------------------------------------------------------
def integration(system: str, method: str, status: int | str, seconds: float) -> None:
    """Один HTTP-запрос к внешней системе. Вызывается транспортом, не бизнес-кодом."""
    method = method.upper()
    INTEGRATION_REQUESTS.labels(system, method, str(status)).inc()
    INTEGRATION_LATENCY.labels(system, method).observe(seconds)


def log_record(level: str, logger: str) -> None:
    # Два первых сегмента имени: `agent.llm_retry`, `langgraph_api.worker`.
    LOG_RECORDS.labels(level.lower(), ".".join(logger.split(".")[:2]) or "root").inc()


# --------------------------------------------------------------------------
# HTTP API
# --------------------------------------------------------------------------
#: Опросы здоровья и сам сбор метрик: каждые несколько секунд, о работе ничего.
_UNMEASURED = frozenset({"/", "/ok", "/metrics"})
_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
#: Префиксы встроенных роутов LangGraph. Остальное — «other»: сканер, перебирающий
#: пути, иначе заводил бы по ряду на каждую попытку.
_BUILTIN = frozenset(
    {"assistants", "threads", "runs", "store", "crons", "info", "docs", "openapi.json", "mcp", "a2a", "ui"}
)


class HttpMetricsMiddleware:
    """
    Запросы ко всему API агента: наши `/api/*` и встроенные роуты LangGraph.

    Стоит снаружи `ApiSecurityMiddleware`, поэтому видит и отказы входа —
    всплеск 401 заметен на графике раньше, чем в жалобах. Время меряется до
    начала ответа: поток прогона открыт минутами, и полная длительность
    говорила бы о задаче, а не о сервере.
    """

    def __init__(self, app: ASGIApp, routes: list | tuple = ()) -> None:
        self.app = app
        self._routes = [route for route in routes if isinstance(route, Route)]

    def _route(self, path: str) -> str:
        if path.startswith("/api/"):
            for route in self._routes:
                if route.path_regex.match(path):
                    return route.path
            return "/api/*"
        head = path.strip("/").split("/", 1)[0]
        return f"/{head}" if head in _BUILTIN else "other"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in _UNMEASURED:
            await self.app(scope, receive, send)
            return
        began = time.perf_counter()
        route = self._route(scope.get("path", ""))
        method = scope.get("method", "")
        method = method if method in _METHODS else "OTHER"
        answered = False

        def record(status: int) -> None:
            nonlocal answered
            answered = True
            HTTP_REQUESTS.labels(route, method, str(status)).inc()
            HTTP_LATENCY.labels(route, method).observe(time.perf_counter() - began)

        async def measured_send(message: Message) -> None:
            if message["type"] == "http.response.start" and not answered:
                record(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, measured_send)
        except Exception:
            if not answered:
                record(500)
            raise


# --------------------------------------------------------------------------
# Состояние в момент сбора
# --------------------------------------------------------------------------
class _Snapshot(Collector):
    """
    То, что не копится, а читается при каждом сборе: окно шлюза и настройки.

    Окно — то же, по которому `llm_pacing` решает, ждать ли: сколько запросов
    и токенов ушло за последнюю минуту и сколько ещё держит пауза после 429.
    Рядом лимиты из `.env`, чтобы на графике было видно, насколько близко к ним.
    """

    def describe(self):
        return []

    def collect(self):
        from agent import llm_pacing

        requests, tokens, paused = llm_pacing.budget().load()
        yield GaugeMetricFamily(
            "orbita_llm_window_requests", "Запросы к модели за последнюю минуту", value=requests
        )
        yield GaugeMetricFamily(
            "orbita_llm_window_tokens",
            "Токены за последнюю минуту: оценка до ответа, факт после",
            value=tokens,
        )
        yield GaugeMetricFamily(
            "orbita_llm_gateway_pause_seconds",
            "Сколько ещё ждать после отказа шлюза (429)",
            value=paused,
        )
        for name, text, read in (
            ("orbita_llm_limit_requests_per_minute", "LLM_REQUESTS_PER_MINUTE, 0 — без лимита",
             cfg.llm_requests_per_minute),
            ("orbita_llm_limit_tokens_per_minute", "LLM_TOKENS_PER_MINUTE, 0 — без лимита",
             cfg.llm_tokens_per_minute),
        ):
            try:
                value = read()
            except cfg.ConfigError:
                continue
            yield GaugeMetricFamily(name, text, value=value)
        try:
            provider, model = cfg.llm_provider(), cfg.model_name()
        except cfg.ConfigError:
            provider = model = ""
        try:
            version = dist.version("orbita-agent")
        except dist.PackageNotFoundError:
            version = ""
        info = InfoMetricFamily("orbita", "Версия агента и модель по умолчанию")
        info.add_metric([], {"version": version, "provider": provider, "model": model})
        yield info


REGISTRY.register(_Snapshot())
