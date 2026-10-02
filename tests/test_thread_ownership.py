"""Треды принадлежат тому, кто их создал: проверка на настоящей сборке сервера LangGraph.

Сервер собирается в отдельном интерпретаторе, как в `test_server_security_integration`,
но с жизненным циклом: без него рантайм inmem не хранит треды. Рабочая папка —
временная: рантайм пишет своё хранилище в `./.langgraph_api`, и в корне проекта
тест загрузил бы и переписал треды разработчика.

Пользователи Keycloak подменены: проверка подписи — предмет `test_oidc_auth`,
а здесь важно, что сервер делает с уже проверенным пользователем.
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent import auth
from agent.security import SERVICE, Principal

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-only-integration-token-32-characters"

SCRIPT = """
from agent import security
from agent.security import Principal

USERS = {"alice": Principal("sub-alice", "alice"), "bob": Principal("sub-bob", "bob", admin=True)}
checked = security.authenticate
security.authenticate = lambda headers: USERS.get(
    headers.get("authorization", "").removeprefix("Bearer "), None
) or checked(headers)

from langgraph_api.server import app
from starlette.testclient import TestClient

def as_(name, **extra):
    token = "%(token)s" if name == "service" else name
    return {"Authorization": "Bearer " + token, **extra}

with TestClient(app) as client:
    # Чужой владелец в запросе не принимается: владелец — тот, кто создал.
    created = client.post("/threads", json={"metadata": {"owner": "sub-bob"}}, headers=as_("alice"))
    assert created.status_code == 200, created.text
    assert created.json()["metadata"]["owner"] == "sub-alice"
    tid = created.json()["thread_id"]

    assert client.get("/threads/" + tid, headers=as_("alice")).status_code == 200
    assert client.get("/threads/" + tid, headers=as_("service")).status_code == 200
    # Роль администратора Keycloak — про настройки сервера, не про чужие треды.
    assert client.get("/threads/" + tid, headers=as_("bob")).status_code == 404
    assert client.get("/threads/" + tid + "/state", headers=as_("bob")).status_code == 404
    # В `langgraph dev` этот заголовок делал бы любого вошедшего «пользователем
    # Studio» мимо правил; `disable_studio_auth` в langgraph.json закрывает его.
    studio = as_("bob", **{"x-auth-scheme": "langsmith"})
    assert client.get("/threads/" + tid, headers=studio).status_code == 404

    mine = [t["thread_id"] for t in client.post("/threads/search", json={}, headers=as_("alice")).json()]
    theirs = [t["thread_id"] for t in client.post("/threads/search", json={}, headers=as_("bob")).json()]
    assert tid in mine and tid not in theirs

    assert client.patch("/threads/" + tid, json={"metadata": {"x": 1}}, headers=as_("bob")).status_code == 404
    stolen = client.patch("/threads/" + tid, json={"metadata": {"owner": "sub-bob"}}, headers=as_("alice"))
    assert stolen.json()["metadata"]["owner"] == "sub-alice"
    assert client.delete("/threads/" + tid, headers=as_("bob")).status_code == 404

    # Свой роут с thread_id: фильтры LangGraph его не касаются, сверка своя.
    assert client.post("/api/ui/pause", json={"thread_id": tid}, headers=as_("bob")).status_code == 404
    assert client.post("/api/ui/pause", json={"thread_id": tid}, headers=as_("alice")).status_code == 200

    # Store — долгая память по всем пользователям; ассистенты — общие графы.
    assert client.post("/store/items/search", json={"namespace_prefix": []}, headers=as_("bob")).status_code == 403
    assert client.post("/assistants/search", json={}, headers=as_("alice")).status_code == 200
    denied = client.post("/assistants", json={"graph_id": "agent"}, headers=as_("alice"))
    assert denied.status_code == 403, (denied.status_code, denied.text)
    assert client.post("/runs/crons/search", json={}, headers=as_("alice")).status_code in (403, 404)
print("ok")
"""


def test_threads_are_private_to_their_owner(tmp_path):
    # The server validates graph_id before authorization. A registered fixture
    # ensures the assistant-creation assertion actually tests permissions.
    (tmp_path / "fixture_graph.py").write_text(
        "from langgraph.graph import END, START, StateGraph\n"
        "builder = StateGraph(dict)\n"
        "builder.add_node('noop', lambda state: {})\n"
        "builder.add_edge(START, 'noop')\n"
        "builder.add_edge('noop', END)\n"
        "graph = builder.compile()\n",
        encoding="utf-8",
    )
    config = json.loads((ROOT / "langgraph.json").read_text(encoding="utf-8"))
    http = {**config["http"], "app": str(ROOT / "src/agent/api.py") + ":app"}
    auth_config = {**config["auth"], "path": str(ROOT / "src/agent/auth.py") + ":auth"}
    env = dict(os.environ)
    env.update(
        PYTHON_DOTENV_DISABLED="1",
        LANGGRAPH_HTTP=json.dumps(http),
        LANGGRAPH_AUTH=json.dumps(auth_config),
        LANGGRAPH_GRAPHS="{}",
        LANGSERVE_GRAPHS=json.dumps({"agent": "./fixture_graph.py:graph"}),
        REDIS_URI="fake",
        DATABASE_URI=":memory:",
        LANGGRAPH_RUNTIME_EDITION="inmem",
        LANGSMITH_LANGGRAPH_API_VARIANT="local_dev",
        LANGSMITH_TRACING="false",
        LANGCHAIN_TRACING_V2="false",
        API_ADMIN_TOKEN=TOKEN,
    )
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT % {"token": TOKEN}],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120, check=False,
    )

    assert result.returncode == 0, result.stderr[-4000:]
    assert result.stdout.strip().endswith("ok")
    # Хранилище рантайма легло во временную папку, а не в проект.
    assert (tmp_path / ".langgraph_api").is_dir()


class Ctx:
    def __init__(self, principal: Principal):
        self.permissions = ["service", "admin"] if principal.service else ["admin"] * principal.admin
        self.user = type("User", (), {"identity": principal.subject})()


@pytest.mark.parametrize("value", [{}, {"metadata": None}, {"metadata": {"owner": "x", "a": 1}}])
def test_a_new_thread_is_stamped_with_whoever_created_it(value):
    user = Principal("sub-1", "u")

    assert asyncio.run(auth.create_thread(Ctx(user), value)) == {"owner": "sub-1"}
    assert value["metadata"]["owner"] == "sub-1"


def test_the_admin_token_sees_every_thread():
    assert asyncio.run(auth.own_threads(Ctx(SERVICE), {})) is None
    assert asyncio.run(auth.deny_by_default(Ctx(SERVICE), {})) is True
    admin = Principal("sub-1", "u", admin=True)
    assert asyncio.run(auth.deny_by_default(Ctx(admin), {})) is False
