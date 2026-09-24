"""Retry actual provider requests without bypassing pacing or repeating a role."""

from __future__ import annotations

import json
import logging

import httpx
import httpx2
import pytest
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from openai import APIError, APIStatusError

from agent import llm_pacing, llm_retry, nodes, providers, roles, tool_compat
from agent.tools import FILE_TOOLS


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(llm_retry, "_sleep", clock.sleep)
    monkeypatch.setattr(llm_retry.random, "uniform", lambda low, high: 0.0)
    monkeypatch.setattr(
        llm_pacing, "_budget", llm_pacing.MinuteBudget(clock=clock, sleep=clock.sleep)
    )
    monkeypatch.setattr(providers, "_pacer", llm_pacing.Pacer())
    monkeypatch.setattr(providers, "_CLIENTS", {})
    monkeypatch.setattr(nodes, "_BOUND", {})
    monkeypatch.setattr(tool_compat, "_PROMPT_ENDPOINTS", set())
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_API_KEY", "sk-private-test-key")
    monkeypatch.setenv("LLM_API_BASE", "https://model.invalid/v1")
    return clock


def install_endpoint(monkeypatch, handler, *, http_module=httpx, streaming=False):
    client = http_module.Client(transport=http_module.MockTransport(handler))
    monkeypatch.setitem(
        providers._FACTORIES,
        "openai",
        lambda model, kwargs: ChatOpenAI(
            model=model, http_client=client, streaming=streaming, **kwargs
        ),
    )
    return client


def completion(content="Document", *, tool_call=False):
    message = {"role": "assistant", "content": content}
    if tool_call:
        message["tool_calls"] = [
            {
                "id": "call-test",
                "type": "function",
                "function": {"name": "list_task_files", "arguments": "{}"},
            }
        ]
    return httpx.Response(
        200,
        json={
            "id": "chat-test",
            "object": "chat.completion",
            "created": 1,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "tool_calls" if tool_call else "stop",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        },
    )


def failure(status, *, code="temporary_error", retry_after=None):
    return httpx.Response(
        status,
        headers={} if retry_after is None else {"retry-after": str(retry_after)},
        json={
            "error": {
                "code": code,
                "message": "private-response-body sk-private-test-key",
            }
        },
    )


@pytest.mark.parametrize("status", [408, 409, 429, 500, 502, 503, 504])
def test_transient_http_failure_retries_the_same_request(monkeypatch, clock, caplog, status):
    payloads = []

    def endpoint(request):
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return failure(status, retry_after=7 if status == 429 else None)
        return completion()

    with install_endpoint(monkeypatch, endpoint), caplog.at_level(logging.WARNING):
        model = providers.build_llm()
        assert model.max_retries == 0
        response = llm_retry.invoke(model, [HumanMessage("private-prompt-body")])

    assert response.content == "Document"
    assert len(payloads) == 2
    assert payloads[0] == payloads[1]
    assert clock.sleeps == [7.0 if status == 429 else 5.0]
    assert any(record.name == "agent.llm_retry" for record in caplog.records)
    for secret in ("sk-private-test-key", "private-response-body", "private-prompt-body"):
        assert secret not in caplog.text


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout])
def test_transient_transport_failure_is_retried(monkeypatch, clock, error_type):
    attempts = []

    def endpoint(request):
        attempts.append(request)
        if len(attempts) == 1:
            raise error_type("Connection reset by peer", request=request)
        return completion()

    with install_endpoint(monkeypatch, endpoint):
        response = llm_retry.invoke(providers.build_llm(), [HumanMessage("Task")])

    assert response.content == "Document"
    assert len(attempts) == 2
    assert clock.sleeps == [5.0]


def stream_chunk(content, *, final=False):
    event = {
        "id": "chat-stream-test",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "test-model",
        "choices": [{
            "index": 0,
            "delta": {"role": "assistant", "content": content},
            "finish_reason": "stop" if final else None,
        }],
    }
    if final:
        event["usage"] = {
            "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
        }
    return ("data: " + json.dumps(event) + "\n\n").encode()


@pytest.mark.parametrize("http_module", [httpx, httpx2], ids=["httpx", "httpx2"])
@pytest.mark.parametrize("error_name", ["ReadError", "RemoteProtocolError", "TimeoutException"])
def test_stream_disconnect_retries_with_pacing_and_discards_partial_response(
    monkeypatch, clock, http_module, error_name
):
    monkeypatch.setenv("LLM_REQUESTS_PER_MINUTE", "1")
    attempts = []
    closed = []

    class ResponseStream(http_module.SyncByteStream):
        def __init__(self, request, fail):
            self.request = request
            self.fail = fail

        def __iter__(self):
            if self.fail:
                yield stream_chunk("discard this incomplete answer")
                raise getattr(http_module, error_name)(
                    "Disconnected during response", request=self.request
                )
            yield stream_chunk("Final answer", final=True)
            yield b"data: [DONE]\n\n"

        def close(self):
            closed.append(self.fail)

    def endpoint(request):
        payload = json.loads(request.content)
        attempts.append((clock(), payload))
        assert payload["stream"] is True
        return http_module.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=ResponseStream(request, fail=len(attempts) == 1),
        )

    with install_endpoint(monkeypatch, endpoint, http_module=http_module, streaming=True):
        response = llm_retry.invoke(providers.build_llm(), [HumanMessage("Task")])

    assert response.content == "Final answer"
    assert response.usage_metadata["total_tokens"] == 120
    assert len(attempts) == 2
    assert attempts[0][1] == attempts[1][1]
    assert [moment for moment, _ in attempts] == [1000.0, 1060.0]
    assert clock.sleeps == [5.0, 55.0]
    assert closed == [True, False]
    assert not providers._pacer._pending


def test_unclassified_sse_api_error_is_not_retried(monkeypatch, clock):
    attempts = []

    class ErrorStream(httpx.SyncByteStream):
        def __iter__(self):
            yield stream_chunk("unfinished")
            yield b'data: {"error":{"message":"Unclassified stream error"}}\n\n'

    def endpoint(request):
        attempts.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=ErrorStream(),
        )

    with install_endpoint(monkeypatch, endpoint, streaming=True), pytest.raises(APIError):
        llm_retry.invoke(providers.build_llm(), [HumanMessage("Task")])

    assert len(attempts) == 1
    assert clock.sleeps == []


def test_application_error_is_not_retried(clock):
    calls = []
    error = RuntimeError("Application bug")

    class BrokenModel:
        def invoke(self, messages):
            calls.append(messages)
            raise error

    with pytest.raises(RuntimeError) as caught:
        llm_retry.invoke(BrokenModel(), [HumanMessage("Task")])

    assert caught.value is error
    assert len(calls) == 1
    assert clock.sleeps == []


@pytest.mark.parametrize("retries", [None, 0, 1, 2])
def test_retry_limit_counts_repeats_and_preserves_final_error(monkeypatch, clock, retries):
    if retries is not None:
        monkeypatch.setenv("LLM_MAX_RETRIES", str(retries))
    attempts = []

    def endpoint(request):
        attempts.append(request)
        return failure(503)

    with install_endpoint(monkeypatch, endpoint), pytest.raises(APIStatusError) as caught:
        llm_retry.invoke(providers.build_llm(), [HumanMessage("Task")])

    configured = 2 if retries is None else retries
    assert len(attempts) == configured + 1
    assert clock.sleeps == [5.0, 10.0][:configured]
    assert caught.value.status_code == 503
    assert "private-response-body" in str(caught.value)


def test_exponential_backoff_stays_bounded(monkeypatch, clock):
    monkeypatch.setenv("LLM_MAX_RETRIES", "6")
    attempts = []

    def endpoint(request):
        attempts.append(request)
        return failure(503)

    with install_endpoint(monkeypatch, endpoint), pytest.raises(APIStatusError):
        llm_retry.invoke(providers.build_llm(), [HumanMessage("Task")])

    assert len(attempts) == 7
    assert clock.sleeps == [5.0, 10.0, 20.0, 40.0, 60.0, 60.0]


def test_service_retry_after_is_respected(monkeypatch, clock):
    attempts = []

    def endpoint(request):
        attempts.append(request)
        return failure(503, retry_after=23) if len(attempts) == 1 else completion()

    with install_endpoint(monkeypatch, endpoint):
        llm_retry.invoke(providers.build_llm(), [HumanMessage("Task")])

    assert len(attempts) == 2
    assert clock.sleeps == [23.0]


def test_explicit_server_no_retry_is_respected(monkeypatch, clock):
    attempts = []

    def endpoint(request):
        attempts.append(request)
        response = failure(503)
        response.headers["x-should-retry"] = "false"
        return response

    with install_endpoint(monkeypatch, endpoint), pytest.raises(APIStatusError):
        llm_retry.invoke(providers.build_llm(), [HumanMessage("Task")])

    assert len(attempts) == 1
    assert clock.sleeps == []


@pytest.mark.parametrize(
    "status,code",
    [(400, "invalid_request"), (401, "invalid_api_key"), (403, "forbidden"),
     (404, "model_not_found"), (429, "insufficient_quota")],
)
def test_terminal_api_errors_are_not_retried(monkeypatch, clock, status, code):
    attempts = []

    def endpoint(request):
        attempts.append(request)
        return failure(status, code=code)

    with install_endpoint(monkeypatch, endpoint), pytest.raises(APIStatusError):
        llm_retry.invoke(providers.build_llm(), [HumanMessage("Task")])

    assert len(attempts) == 1
    assert clock.sleeps == []


def test_request_that_cannot_fit_budget_fails_without_retry(monkeypatch, clock):
    monkeypatch.setenv("LLM_TOKENS_PER_MINUTE", "1")
    attempts = []

    def endpoint(request):
        attempts.append(request)
        return completion()

    with install_endpoint(monkeypatch, endpoint), pytest.raises(llm_pacing.RateLimitTooSmall):
        llm_retry.invoke(providers.build_llm(), [HumanMessage("Task")])

    assert attempts == []
    assert clock.sleeps == []


def test_each_retry_passes_through_the_minute_request_budget(monkeypatch, clock):
    monkeypatch.setenv("LLM_REQUESTS_PER_MINUTE", "1")
    sent_at = []

    def endpoint(request):
        sent_at.append(clock())
        return failure(503) if len(sent_at) == 1 else completion()

    with install_endpoint(monkeypatch, endpoint):
        response = llm_retry.invoke(providers.build_llm(), [HumanMessage("Task")])

    assert response.content == "Document"
    assert sent_at == [1000.0, 1060.0]
    assert clock.sleeps == [5.0, 55.0]
    assert not providers._pacer._pending


def test_prompt_tool_fallback_retries_its_own_request(monkeypatch, clock):
    monkeypatch.setenv("LLM_TOOL_MODE", "auto")
    payloads = []

    def endpoint(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        if "tools" in payload:
            return httpx.Response(
                400,
                json={"error": {"message": (
                    '"auto" tool choice requires --enable-auto-tool-choice '
                    "and --tool-call-parser to be set"
                )}},
            )
        return failure(503) if len(payloads) == 2 else completion("# Final document")

    with install_endpoint(monkeypatch, endpoint):
        response = tool_compat.invoke(
            nodes.model_for, [HumanMessage("Task")], {}, FILE_TOOLS, allow_tools=True
        )

    assert response.content == "# Final document"
    assert response.response_metadata["tool_mode"] == "prompt"
    assert len(payloads) == 3
    assert payloads[1] == payloads[2]
    assert "tools" not in payloads[1]
    assert clock.sleeps == [5.0]


def test_review_second_call_retries_without_replaying_first_response(monkeypatch, clock):
    monkeypatch.setenv("LLM_TOOL_MODE", "native")
    for kind in ("CACHE_HIT", "CACHE_MISS", "CACHE_WRITE", "OUTPUT"):
        monkeypatch.setenv(f"PRICE_{kind}_PER_MTOK", "1")
    payloads = []

    def endpoint(request):
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return completion("", tool_call=True)
        if len(payloads) == 2:
            return failure(503)
        return completion("# Final review")

    with install_endpoint(monkeypatch, endpoint):
        update = nodes.make_role_node(roles.LAST)(
            {"messages": [HumanMessage("Task")], "task": "Task"}, {}
        )

    assert len(payloads) == 3
    assert payloads[1] == payloads[2]
    assert payloads[1]["messages"][-1]["content"] == nodes.NO_TOOLS_NOW
    assert update["artifacts"][roles.LAST.key] == "# Final review"
    assert update["usage"]["calls"] == 2
    assert update["usage"]["output"] == 40
    assert clock.sleeps == [5.0]
