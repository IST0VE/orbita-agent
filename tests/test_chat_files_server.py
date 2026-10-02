"""Файлы чата на настоящей сборке сервера LangGraph: чужой чат не открыть ничем.

Сервер собирается в отдельном интерпретаторе, как в `test_thread_ownership`:
рантайм inmem пишет хранилище в `./.langgraph_api`, поэтому рабочая папка —
временная. Пользователи Keycloak подменены: проверка подписи — предмет
`test_oidc_auth`, а здесь важно, что сервер делает с уже проверенным.

Граф `probe` — одна нода, которая возвращает папку из `runtime.options()`.
Так проверяется главное: браузер присылает имя папки, а граф получает папку
чата своего треда.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-only-integration-token-32-characters"

PROBE = """
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from agent import publishers
from agent.runtime import options


class State(TypedDict, total=False):
    dir: str
    base: str


def probe(state: State, config: RunnableConfig) -> dict:
    chosen = options(config)
    title = config["configurable"].get("publish")
    if title:
        publishers.FilePublisher().publish(title, "текст")
    return {"dir": chosen.get("input_dir", ""), "base": chosen.get("base_dir", "")}


builder = StateGraph(State)
builder.add_node("probe", probe)
builder.add_edge(START, "probe")
builder.add_edge("probe", END)
graph = builder.compile()
"""

SCRIPT = """
import os
from pathlib import Path

from agent import security
from agent.security import Principal

USERS = {"alice": Principal("sub-alice", "alice"), "bob": Principal("sub-bob", "bob", admin=True)}
checked = security.authenticate
security.authenticate = lambda headers: USERS.get(
    headers.get("authorization", "").removeprefix("Bearer "), None
) or checked(headers)

from langgraph_api.server import app
from starlette.testclient import TestClient

CHATS = Path(os.environ["CHAT_FILES_DIR"])


def as_(name):
    return {"Authorization": "Bearer " + ("%(token)s" if name == "service" else name)}


def run(client, thread, who, **configurable):
    return client.post(
        "/threads/" + thread + "/runs/wait",
        json={"assistant_id": "probe", "input": {}, "config": {"configurable": configurable}},
        headers=as_(who),
    )


with TestClient(app) as client:
    tid = client.post("/threads", json={}, headers=as_("alice")).json()["thread_id"]
    files = "/api/chats/" + tid + "/files"

    up = client.put(files, params={"name": "ticket.md"}, content="# Задача".encode(), headers=as_("alice"))
    assert up.status_code == 200, up.text
    assert [f["name"] for f in up.json()["files"]] == ["ticket.md"]
    assert (CHATS / tid / "ticket.md").is_file()
    read = client.get(files + "/content", params={"name": "ticket.md"}, headers=as_("alice"))
    assert read.json()["text"] == "# Задача", read.text

    # Чужой чат: тот же 404, что у несуществующего, на каждом роуте.
    for method, path, extra in [
        ("get", files, {}),
        ("get", files + "/content", {"params": {"name": "ticket.md"}}),
        ("put", files, {"params": {"name": "x.md"}, "content": b"x"}),
        ("delete", files, {"params": {"name": "ticket.md"}}),
        ("post", "/api/chats/" + tid + "/attach", {"json": {"source": "published", "name": "a.md"}}),
        ("delete", "/api/chats/" + tid, {}),
    ]:
        response = getattr(client, method)(path, headers=as_("bob"), **extra)
        assert response.status_code == 404, (method, path, response.status_code, response.text)
    assert (CHATS / tid / "ticket.md").is_file()
    missing = "/api/chats/00000000-0000-4000-8000-000000000000/files"
    assert client.get(missing, headers=as_("alice")).status_code == 404
    assert client.get(missing, headers=as_("service")).status_code == 404

    # Старые роуты папок задач не открывают файлы чата по имени `@chat/<тред>`.
    legacy = client.get(
        "/api/ui/resources/orbita.tasks",
        params={"operation": "read", "task": "@chat/" + tid, "name": "ticket.md"},
        headers=as_("bob"),
    )
    assert legacy.status_code == 404, legacy.text

    # Потолок загрузки свой, и держит его middleware до обработчика.
    big = client.put(files, params={"name": "big.md"}, content=b"x" * 4096, headers=as_("alice"))
    assert big.status_code == 413, big.text

    # Папку прогона задаёт тред, а не имя из запроса.
    probe = run(client, tid, "alice", input_dir="partial-refund", base_dir="@chat/" + tid)
    assert probe.status_code == 200, probe.text
    assert probe.json() == {"dir": "@chat/" + tid, "base": "@chat/" + tid}, probe.json()
    assert run(client, tid, "bob", input_dir="@chat/" + tid).status_code == 404

    # Админ-токену имя папки задачи по-прежнему можно назвать.
    service_thread = client.post("/threads", json={}, headers=as_("service")).json()["thread_id"]
    named = run(client, service_thread, "service", input_dir="partial-refund")
    assert named.json()["dir"] == "partial-refund", named.json()
    chat = run(client, service_thread, "service", input_dir="@chat")
    assert chat.json()["dir"] == "@chat/" + service_thread, chat.json()

    # Опубликованное прогоном видно его автору по HTTP, а не только в прогоне.
    # Сервер ставит каждому запросу конфиг с хранилищем, и раньше по нему
    # пользователь терялся: список читался из общего корня.
    assert run(client, tid, "alice", publish="Страница Алисы").status_code == 200
    assert run(client, service_thread, "service", publish="Общая страница").status_code == 200

    def published(who):
        listed = client.get(
            "/api/ui/resources/orbita.publications",
            params={"operation": "list"},
            headers=as_(who),
        )
        assert listed.status_code == 200, listed.text
        return [item["title"] for item in listed.json()["documents"]]

    assert published("alice") == ["Страница Алисы"]
    assert published("bob") == []
    assert published("service") == ["Общая страница"]
    name = client.get("/api/published", headers=as_("alice")).json()["documents"][0]["name"]
    opened = client.get(
        "/api/ui/resources/orbita.publications",
        params={"operation": "read", "name": name},
        headers=as_("alice"),
    )
    assert opened.status_code == 200 and "текст" in opened.json()["text"], opened.text

    # Библиотека примеров общая, а заводит папки в ней только администратор.
    assert client.post("/api/inputs", json={"name": "mine"}, headers=as_("alice")).status_code == 403
    assert client.post("/api/inputs", json={"name": "shared"}, headers=as_("service")).status_code == 200
    assert client.get("/api/library", headers=as_("alice")).status_code == 200

    # Удаление чата уносит и тред, и файлы.
    gone = client.delete("/api/chats/" + tid, headers=as_("alice"))
    assert gone.status_code == 200, gone.text
    assert gone.json()["files_removed"] is True
    assert not (CHATS / tid).exists()
    assert client.get("/threads/" + tid, headers=as_("alice")).status_code == 404
print("ok")
"""


def test_chat_files_are_private_and_runs_get_them_by_thread(tmp_path):
    (tmp_path / "probe.py").write_text(PROBE, encoding="utf-8")
    config = json.loads((ROOT / "langgraph.json").read_text(encoding="utf-8"))
    http = {**config["http"], "app": str(ROOT / "src/agent/api.py") + ":app"}
    auth_config = {**config["auth"], "path": str(ROOT / "src/agent/auth.py") + ":auth"}
    env = dict(os.environ)
    env.update(
        PYTHON_DOTENV_DISABLED="1",
        # Иначе упавшая проверка с кириллицей приезжает в кодировке консоли
        # Windows, и вместо причины тест показывает UnicodeDecodeError.
        PYTHONIOENCODING="utf-8",
        LANGGRAPH_HTTP=json.dumps(http),
        LANGGRAPH_AUTH=json.dumps(auth_config),
        LANGGRAPH_GRAPHS="{}",
        # Графы сервер берёт из LANGSERVE_GRAPHS (так их передаёт `langgraph dev`).
        LANGSERVE_GRAPHS=json.dumps({"probe": "./probe.py:graph"}),
        REDIS_URI="fake",
        DATABASE_URI=":memory:",
        LANGGRAPH_RUNTIME_EDITION="inmem",
        LANGSMITH_LANGGRAPH_API_VARIANT="local_dev",
        LANGSMITH_TRACING="false",
        LANGCHAIN_TRACING_V2="false",
        API_ADMIN_TOKEN=TOKEN,
        CHAT_FILES_DIR=str(tmp_path / "chats"),
        CHAT_FILE_MAX_BYTES="2048",
        AGENT_INPUT_DIR=str(tmp_path / "input"),
        PUBLISH_DIR=str(tmp_path / "published"),
    )
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT % {"token": TOKEN}],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=180, check=False,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr[-4000:]
    assert result.stdout.strip().endswith("ok")
