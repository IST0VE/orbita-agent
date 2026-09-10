"""Exercise the actual HTTP payload and graph loop without network or credentials."""

import json

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from openai import BadRequestError

from agent import config as cfg
from agent import nodes, providers, roles, tool_compat
from agent.builder import build_graph
from agent.tools import FILE_TOOLS

UNSUPPORTED = (
    '"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set'
)


@pytest.fixture(autouse=True)
def clients(monkeypatch):
    monkeypatch.setattr(tool_compat, "_PROMPT_ENDPOINTS", set())
    monkeypatch.setattr(providers, "_CLIENTS", {})
    monkeypatch.setattr(nodes, "_BOUND", {})
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "company-model")
    monkeypatch.setenv("MEMORY_ENABLED", "0")


def install_endpoint(monkeypatch, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setitem(
        providers._FACTORIES,
        "openai",
        lambda model, kwargs: ChatOpenAI(
            model=model,
            api_key="test",
            base_url="https://company.invalid/v1",
            http_client=client,
            max_retries=0,
        ),
    )
    return client


def completion(content):
    return httpx.Response(
        200,
        json={
            "id": "chat-test",
            "object": "chat.completion",
            "created": 1,
            "model": "company-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        },
    )


@pytest.mark.parametrize("stale_source", ["none", "configurable", "context", "both"])
def test_auto_fallback_completes_graph_and_reads_real_file(monkeypatch, tmp_path, stale_source):
    task = tmp_path / "input" / "task"
    task.mkdir(parents=True)
    (task / "requirements.md").write_text("Refund limit is 731 units.", encoding="utf-8")
    payloads = []
    answers = iter(
        [
            json.dumps(
                {
                    "orbita_tool_calls": [
                        {"name": "read_task_file", "args": {"name": "requirements.md"}}
                    ]
                }
            ),
            *[f"# {role.title}\nRefund limit is 731 units." for role in roles.ROLES],
        ]
    )

    def endpoint(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        assert payload["model"] == "company-model"
        if "tools" in payload:
            return httpx.Response(400, json={"error": {"message": UNSUPPORTED}})
        assert "tool_choice" not in payload
        assert all(m["role"] != "tool" and "tool_calls" not in m for m in payload["messages"])
        return completion(next(answers))

    configurable = {"input_dir": "task", "thread_id": "compat-test"}
    context = {}
    if stale_source in ("configurable", "both"):
        configurable["model"] = "qwen3.6-35b-a3b-fp8"
    if stale_source in ("context", "both"):
        context["model"] = "qwen3.6-35b-a3b-fp8"

    with install_endpoint(monkeypatch, endpoint):
        state = (
            build_graph()
            .compile()
            .invoke(
                {
                    "messages": [
                        HumanMessage("Read the attached requirements and prepare the analysis.")
                    ]
                },
                {"configurable": configurable},
                context=context,
            )
        )
    assert sum("tools" in payload for payload in payloads) == 1
    assert len(payloads) == 7  # rejected probe + tool request + five documents
    assert {key[1] for key in tool_compat._PROMPT_ENDPOINTS} == {"company-model"}
    assert set(state["artifacts"]) == set(roles.PIPELINE.keys)
    assert state["usage"]["calls"] == 6
    assert state["usage"]["output"] == 120
    result = next(m for m in state["messages"] if isinstance(m, ToolMessage))
    assert result.content == "Refund limit is 731 units."
    assert "731" in json.dumps(payloads[2]["messages"])
    assert all(
        m.response_metadata["tool_mode"] == "prompt"
        for m in state["messages"]
        if isinstance(m, AIMessage)
    )


@pytest.mark.parametrize(
    "mode,error", [("native", UNSUPPORTED), ("auto", "context length exceeded")]
)
def test_native_mode_and_unrelated_errors_do_not_fallback(monkeypatch, mode, error):
    monkeypatch.setenv("LLM_TOOL_MODE", mode)
    payloads = []

    def endpoint(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"message": error}})

    with install_endpoint(monkeypatch, endpoint), pytest.raises(BadRequestError):
        tool_compat.invoke(
            nodes.model_for, [HumanMessage("task")], {}, FILE_TOOLS, allow_tools=True
        )
    assert len(payloads) == 1
    assert not tool_compat._PROMPT_ENDPOINTS


def test_prompt_mode_never_sends_native_tool_fields(monkeypatch):
    monkeypatch.setenv("LLM_TOOL_MODE", "prompt")
    call = {"name": "read_task_file", "args": {"name": "a.md"}, "id": "old"}
    history = [
        SystemMessage("system"),
        HumanMessage("task"),
        AIMessage(
            content="",
            tool_calls=[call],
            additional_kwargs={"tool_calls": [{"native": "old"}]},
        ),
        ToolMessage(content="source data", tool_call_id="old", name="read_task_file"),
    ]

    def endpoint(request):
        payload = json.loads(request.content)
        assert "tools" not in payload and "tool_choice" not in payload
        assert [m["role"] for m in payload["messages"]] == ["system", "user", "assistant", "user"]
        assert all("tool_calls" not in m for m in payload["messages"])
        assert "source data" in payload["messages"][-1]["content"]
        return completion("# Document")

    with install_endpoint(monkeypatch, endpoint):
        result = tool_compat.invoke(nodes.model_for, history, {}, FILE_TOOLS, allow_tools=True)
    assert result.content == "# Document"
    assert history[2].tool_calls == [dict(call, type="tool_call")]
    assert history[2].additional_kwargs


@pytest.mark.parametrize(
    "content",
    [
        '{"orbita_tool_calls": [',
        '{"orbita_tool_calls": []}',
        '{"orbita_tool_calls": [{"name":"shell","args":{}}]}',
        '{"orbita_tool_calls": [{"name":"read_task_file","args":"a.md"}]}',
    ],
)
def test_invalid_tool_envelope_cannot_be_published_as_a_document(content):
    with pytest.raises(ValueError, match="текстовый запрос"):
        tool_compat.parse_response(AIMessage(content), FILE_TOOLS, allow_tools=True)


def test_reasoning_and_fenced_json_preserve_usage_and_generate_unique_ids():
    response = AIMessage(
        "<think>Need source data.</think>\n```json\n"
        '{"orbita_tool_calls":[{"name":"list_task_files","args":{}},'
        '{"name":"list_task_files","args":{}}]}\n```',
        id="response-id",
        usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
    )
    result = tool_compat.parse_response(response, FILE_TOOLS, allow_tools=True)
    assert len({c["id"] for c in result.tool_calls}) == 2
    assert result.id == response.id and result.usage_metadata == response.usage_metadata
    assert result.content == "" and not response.tool_calls
    with pytest.raises(ValueError):
        tool_compat.parse_response(response, FILE_TOOLS, allow_tools=False)


def test_protocol_example_inside_markdown_is_not_executed():
    text = '# API example\n```json\n{"orbita_tool_calls": []}\n```'
    result = tool_compat.parse_response(AIMessage(text), FILE_TOOLS, allow_tools=True)
    assert result.content == text and not result.tool_calls


def test_unknown_mode_rejected(monkeypatch):
    monkeypatch.setenv("LLM_TOOL_MODE", "disabled")
    with pytest.raises(cfg.ConfigError, match="LLM_TOOL_MODE"):
        cfg.llm_tool_mode()
