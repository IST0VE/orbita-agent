"""
Журнал сервера для интерфейса: что попадает в буфер, что из него вычищается
и как его отдаёт `/api/logs`.
"""

from __future__ import annotations

import logging

import pytest
from langchain_core.runnables.config import var_child_runnable_config
from starlette.testclient import TestClient

from agent import api, logbook

TOKEN = "test-only-auth-token-with-32-characters"


def record(
    message: object,
    level: int = logging.WARNING,
    name: str = "agent.test",
    *,
    exc_info=None,
    **extra,
) -> logging.LogRecord:
    item = logging.LogRecord(name, level, __file__, 1, message, (), exc_info)
    for key, value in extra.items():
        setattr(item, key, value)
    return item


def emit(book: logbook.Logbook, item: logging.LogRecord) -> None:
    # Через `handle`, как это делает logging: `emit` рассчитывает на его замок.
    book.handle(item)


def test_secret_value_from_environment_never_reaches_the_buffer(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "plain-value-without-known-format")
    book = logbook.Logbook()

    emit(book, record("provider refused key plain-value-without-known-format"))

    [entry] = book.snapshot()["records"]
    assert "plain-value-without-known-format" not in entry["message"]
    assert logbook.MASK in entry["message"]


def test_short_or_unrelated_environment_values_are_left_alone(monkeypatch):
    """`HOME=/app` не секрет: иначе каталог исчез бы из каждой трассировки."""
    monkeypatch.setenv("HOME", "/app")
    monkeypatch.setenv("JIRA_API_TOKEN", "1")
    book = logbook.Logbook()

    emit(book, record("File /app/src/agent/nodes.py, line 1"))

    assert book.snapshot()["records"][0]["message"] == "File /app/src/agent/nodes.py, line 1"


@pytest.mark.parametrize(
    "leak",
    [
        "Authorization: Bearer " + "x" * 32,
        "https://user:hunter2hunter2@wiki.example.com/rest",
        "api_key=abcdefgh12345678",
        "sk-" + "a" * 24,
    ],
)
def test_known_secret_formats_are_masked(leak):
    book = logbook.Logbook()

    emit(book, record(f"request failed: {leak}"))

    message = book.snapshot()["records"][0]["message"]
    assert logbook.MASK in message
    assert "hunter2" not in message
    assert "x" * 32 not in message
    assert "abcdefgh12345678" not in message


def test_traceback_is_kept_and_scrubbed(monkeypatch):
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "confluence-secret-value")
    book = logbook.Logbook()
    try:
        raise RuntimeError("401 for confluence-secret-value")
    except RuntimeError:
        import sys

        emit(book, record("publish failed", logging.ERROR, exc_info=sys.exc_info()))

    [entry] = book.snapshot()["records"]
    assert "Traceback" in entry["exception"]
    assert "RuntimeError" in entry["exception"]
    assert "confluence-secret-value" not in entry["exception"]


def test_structlog_event_dict_is_unpacked():
    """Сервер LangGraph пишет через structlog: в `msg` лежит словарь события."""
    book = logbook.Logbook()
    event = {
        "event": "Run encountered an error in graph",
        "exception": "Traceback (most recent call last):\nValueError: boom",
        "run_id": "run-1",
        "thread_id": "thread-1",
        "logger": "langgraph_api.worker",
        "timestamp": "2026-09-24T00:00:00Z",
        "path_params": {"not": "scalar"},
    }

    emit(book, record(event, logging.ERROR, name="langgraph_api.worker"))

    [entry] = book.snapshot()["records"]
    assert entry["message"] == "Run encountered an error in graph"
    assert entry["exception"].endswith("ValueError: boom")
    assert entry["run_id"] == "run-1"
    assert entry["thread_id"] == "thread-1"
    assert "timestamp" not in entry["fields"]
    assert "path_params" not in entry["fields"]


def test_record_inside_a_graph_node_is_bound_to_its_thread():
    """Наши модули не пишут thread_id сами — его даёт контекст прогона."""
    book = logbook.Logbook()
    token = var_child_runnable_config.set(
        {
            "metadata": {"langgraph_node": "analyst", "run_id": "run-7"},
            "configurable": {"thread_id": "thread-7"},
        }
    )
    try:
        emit(book, record("gateway 429, retrying", name="agent.llm_retry"))
    finally:
        var_child_runnable_config.reset(token)

    [entry] = book.snapshot()["records"]
    assert (entry["thread_id"], entry["run_id"], entry["node"]) == ("thread-7", "run-7", "analyst")
    assert book.snapshot(thread_id="thread-7")["records"]
    assert not book.snapshot(thread_id="other")["records"]


def test_consecutive_repeats_fold_into_a_counter():
    book = logbook.Logbook()

    emit(book, record("GET /info 401 3ms", name="asgi"))
    first = book.snapshot()
    emit(book, record("GET /info 401 5ms", name="asgi"))
    emit(book, record("GET /info 401 4ms", name="asgi"))

    [entry] = book.snapshot()["records"]
    assert entry["repeats"] == 3
    # Повтор меняет запись — опрос с `after` обязан его увидеть.
    [changed] = book.snapshot(after=first["next"])["records"]
    assert changed["id"] == entry["id"]


def test_problems_are_not_evicted_by_routine_records():
    book = logbook.Logbook(problems=5, routine=5)

    emit(book, record("the one traceback that matters", logging.ERROR))
    for number in range(50):
        emit(book, record(f"routine {number}", logging.INFO))

    messages = [entry["message"] for entry in book.snapshot()["records"]]
    assert "the one traceback that matters" in messages
    assert book.snapshot()["evicted"] == 45


def test_successful_http_requests_are_not_stored_but_failures_are():
    book = logbook.Logbook()

    emit(book, record("GET /api/logs 200 1ms", logging.INFO, name="asgi"))
    emit(book, record("POST /threads/x/runs 500 12ms", logging.ERROR, name="asgi"))

    assert [entry["message"] for entry in book.snapshot()["records"]] == [
        "POST /threads/x/runs 500 12ms"
    ]


def test_level_filter_and_limit():
    book = logbook.Logbook()
    for number in range(5):
        emit(book, record(f"info {number}", logging.INFO))
    emit(book, record("warning", logging.WARNING))

    assert [entry["message"] for entry in book.snapshot(level=logging.WARNING)["records"]] == [
        "warning"
    ]
    limited = book.snapshot(limit=2)
    assert [entry["message"] for entry in limited["records"]] == ["info 4", "warning"]
    assert limited["truncated"] is True


def test_install_attaches_one_handler():
    root = logging.getLogger()
    first = logbook.install()
    second = logbook.install()

    assert first is second
    assert sum(isinstance(handler, logbook.Logbook) for handler in root.handlers) == 1


def test_logs_route_is_protected_by_the_admin_token(monkeypatch):
    monkeypatch.setenv("API_ADMIN_TOKEN", TOKEN)

    assert TestClient(api.app).get("/api/logs").status_code == 401


def test_logs_route_returns_records_and_server_info(monkeypatch):
    monkeypatch.setenv("API_ADMIN_TOKEN", TOKEN)
    book = logbook.Logbook()
    monkeypatch.setattr(api, "_LOGBOOK", book)
    emit(book, record("node failed", logging.ERROR, thread_id="thread-9"))
    emit(book, record("unrelated", logging.WARNING))
    client = TestClient(api.app, headers={"Authorization": f"Bearer {TOKEN}"})

    body = client.get("/api/logs", params={"level": "warning", "thread_id": "thread-9"}).json()

    assert [entry["message"] for entry in body["records"]] == ["node failed"]
    assert body["server"]["python"]
    assert "levelno" not in body["records"][0]
    assert client.get("/api/logs", params={"level": "loud"}).status_code == 400
    assert client.get("/api/logs", params={"after": "x"}).status_code == 400


@pytest.mark.parametrize(
    ("name", "message"),
    [
        # structlog жалуется на свою цепочку при каждой трассировке.
        (
            "py.warnings",
            "stdlib.py:1166: UserWarning: Remove `format_exc_info` from your processor chain "
            "if you want pretty exceptions.",
        ),
        # Напоминание о флаге запуска, которое сервер пишет на каждом старте.
        (
            "langgraph_runtime_inmem.queue",
            "Heads up: You've set --allow-blocking, which allows synchronous blocking I/O "
            "operations.",
        ),
    ],
)
def test_notes_about_how_the_server_is_launched_are_not_stored(name, message):
    book = logbook.Logbook()

    emit(book, record(message, name=name))

    assert book.snapshot()["records"] == []


def test_other_warnings_from_the_same_logger_are_kept():
    book = logbook.Logbook()

    emit(book, record("Worker died unexpectedly", name="langgraph_runtime_inmem.queue"))

    assert [entry["message"] for entry in book.snapshot()["records"]] == [
        "Worker died unexpectedly"
    ]
