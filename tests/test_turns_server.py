"""Правка отправленного запроса и его версии на настоящей сборке сервера LangGraph.

Сервер собирается в отдельном интерпретаторе, как в `test_chat_files_server`:
рантайм inmem пишет хранилище в `./.langgraph_api`, поэтому рабочая папка —
временная. Граф `chat` — одна нода, которая отвечает эхом на последний запрос:
модель здесь не нужна, проверяется то, что делает с историей треда сервер.

Правку интерфейс отправляет обычным прогоном с `checkpoint` из `/turns`, —
так же поступает и этот тест.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-only-integration-token-32-characters"

CHAT = """
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt


class State(TypedDict):
    messages: Annotated[list, add_messages]


def answer(state: State) -> dict:
    text = state["messages"][-1].content
    if text.startswith("спроси"):
        # Как подтверждение публикации: ответ на остановку — отдельный прогон.
        reply = interrupt({"question": "точно?"})
        return {"messages": [AIMessage(content=f"ответ после «{reply}»")]}
    return {"messages": [AIMessage(content="ответ: " + text)]}


builder = StateGraph(State)
builder.add_node("answer", answer)
builder.add_edge(START, "answer")
builder.add_edge("answer", END)
graph = builder.compile()
"""

SCRIPT = """
from agent import security
from agent.security import Principal

USERS = {"alice": Principal("sub-alice", "alice"), "bob": Principal("sub-bob", "bob")}
checked = security.authenticate
security.authenticate = lambda headers: USERS.get(
    headers.get("authorization", "").removeprefix("Bearer "), None
) or checked(headers)

from langgraph_api.server import app
from starlette.testclient import TestClient


def as_(name):
    return {"Authorization": "Bearer " + name}


def ask(client, thread, text, message_id=None, checkpoint=None):
    message = {"type": "human", "content": text}
    if message_id:
        message["id"] = message_id
    body = {"assistant_id": "chat", "input": {"messages": [message]}}
    if checkpoint:
        body["checkpoint"] = {"checkpoint_id": checkpoint}
    response = client.post("/threads/" + thread + "/runs/wait", json=body, headers=as_("alice"))
    assert response.status_code == 200, response.text
    return [m["content"] for m in response.json()["messages"]]


def turns(client, thread, who="alice"):
    response = client.get("/api/chats/" + thread + "/turns", headers=as_(who))
    assert response.status_code == 200, response.text
    return response.json()["turns"]


def shown(client, thread):
    state = client.get("/threads/" + thread + "/state", headers=as_("alice")).json()
    return [m["content"] for m in state["values"]["messages"]]


def switch(client, thread, message_id, version, who="alice"):
    return client.post(
        "/api/chats/" + thread + "/turns/switch",
        json={"message_id": message_id, "version": version},
        headers=as_(who),
    )


with TestClient(app) as client:
    tid = client.post("/threads", json={"metadata": {"graph_id": "chat"}}, headers=as_("alice")).json()["thread_id"]
    ask(client, tid, "первый", "h1")
    ask(client, tid, "второй", "h2")

    listed = turns(client, tid)
    assert [(t["message_id"], t["version"], t["versions"]) for t in listed] == [("h1", 1, 1), ("h2", 1, 1)], listed
    assert listed[1]["question"] == "второй"

    # Правка второго запроса: прогон с его чекпоинта — новая ветка, первый ответ цел.
    assert ask(client, tid, "второй, исправленный", "h2b", listed[1]["fork"]) == [
        "первый", "ответ: первый", "второй, исправленный", "ответ: второй, исправленный",
    ]
    listed = turns(client, tid)
    assert [(t["message_id"], t["version"], t["versions"]) for t in listed] == [("h1", 1, 1), ("h2b", 2, 2)], listed

    # Правка самого первого запроса: ветка начинается с пустого разговора.
    assert ask(client, tid, "первый, исправленный", "h1b", listed[0]["fork"]) == [
        "первый, исправленный", "ответ: первый, исправленный",
    ]
    listed = turns(client, tid)
    assert [(t["message_id"], t["version"], t["versions"]) for t in listed] == [("h1b", 2, 2)], listed

    # Вернуться к первой версии первого запроса: открывается её ветка там, где
    # в ней остановились, — с последней правкой второго запроса.
    back = switch(client, tid, "h1b", 1)
    assert back.status_code == 200, back.text
    assert shown(client, tid) == ["первый", "ответ: первый", "второй, исправленный", "ответ: второй, исправленный"]
    assert [(t["message_id"], t["version"], t["versions"]) for t in back.json()["turns"]] == [("h1", 1, 2), ("h2b", 2, 2)]

    # И к первой версии второго.
    assert switch(client, tid, "h2b", 1).status_code == 200
    assert shown(client, tid) == ["первый", "ответ: первый", "второй", "ответ: второй"]

    # Следующий запрос продолжает показанную ветку.
    assert ask(client, tid, "третий", "h3")[-2:] == ["третий", "ответ: третий"]
    assert shown(client, tid)[:4] == ["первый", "ответ: первый", "второй", "ответ: второй"]

    # Несуществующая версия и чужой чат.
    assert switch(client, tid, "h3", 5).status_code == 409
    assert switch(client, tid, "h1", 2, who="bob").status_code == 404
    assert client.get("/api/chats/" + tid + "/turns", headers=as_("bob")).status_code == 404

    # Ветка, которая продолжилась ответом на остановку: голова — после ответа,
    # а не на самом вопросе (ответ идёт отдельным прогоном со своим run_id).
    asked = client.post("/threads", json={"metadata": {"graph_id": "chat"}}, headers=as_("alice")).json()["thread_id"]
    ask(client, asked, "спроси меня", "q1")
    resumed = client.post(
        "/threads/" + asked + "/runs/wait",
        json={"assistant_id": "chat", "command": {"resume": "да"}},
        headers=as_("alice"),
    )
    assert resumed.status_code == 200, resumed.text
    assert shown(client, asked) == ["спроси меня", "ответ после «да»"]
    ask(client, asked, "другой запрос", "q1b", turns(client, asked)[0]["fork"])
    assert switch(client, asked, "q1b", 1).status_code == 200
    state = client.get("/threads/" + asked + "/state", headers=as_("alice")).json()
    assert [m["content"] for m in state["values"]["messages"]] == ["спроси меня", "ответ после «да»"]
    assert state["next"] == [], state["next"]

    # Запросы без id (до этой правки интерфейс их не давал) узнаются по тексту.
    old = client.post("/threads", json={"metadata": {"graph_id": "chat"}}, headers=as_("alice")).json()["thread_id"]
    ask(client, old, "старый запрос")
    legacy = turns(client, old)
    assert [(t["question"], t["version"], t["versions"]) for t in legacy] == [("старый запрос", 1, 1)], legacy
    ask(client, old, "старый, исправленный", "n1", legacy[0]["fork"])
    assert [(t["version"], t["versions"]) for t in turns(client, old)] == [(2, 2)]
    message_id = turns(client, old)[0]["message_id"]
    assert switch(client, old, message_id, 1).status_code == 200
    assert shown(client, old) == ["старый запрос", "ответ: старый запрос"]
print("ok")
"""


def test_an_edited_request_starts_a_branch_and_versions_switch(tmp_path):
    (tmp_path / "chat.py").write_text(CHAT, encoding="utf-8")
    config = json.loads((ROOT / "langgraph.json").read_text(encoding="utf-8"))
    http = {**config["http"], "app": str(ROOT / "src/agent/api.py") + ":app"}
    auth_config = {**config["auth"], "path": str(ROOT / "src/agent/auth.py") + ":auth"}
    env = dict(os.environ)
    env.update(
        PYTHON_DOTENV_DISABLED="1",
        PYTHONIOENCODING="utf-8",
        LANGGRAPH_HTTP=json.dumps(http),
        LANGGRAPH_AUTH=json.dumps(auth_config),
        LANGGRAPH_GRAPHS="{}",
        LANGSERVE_GRAPHS=json.dumps({"chat": "./chat.py:graph"}),
        REDIS_URI="fake",
        DATABASE_URI=":memory:",
        LANGGRAPH_RUNTIME_EDITION="inmem",
        LANGSMITH_LANGGRAPH_API_VARIANT="local_dev",
        LANGSMITH_TRACING="false",
        LANGCHAIN_TRACING_V2="false",
        API_ADMIN_TOKEN=TOKEN,
        CHAT_FILES_DIR=str(tmp_path / "chats"),
        AGENT_INPUT_DIR=str(tmp_path / "input"),
        PUBLISH_DIR=str(tmp_path / "published"),
    )
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=180, check=False,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr[-4000:]
    assert result.stdout.strip().endswith("ok")
