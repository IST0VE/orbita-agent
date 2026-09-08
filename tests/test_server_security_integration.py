"""Exercise the actual LangGraph server assembly in an isolated interpreter."""

import json
import os
import subprocess
import sys
from pathlib import Path


def test_actual_langgraph_server_protects_custom_builtin_and_meta_routes():
    root = Path(__file__).resolve().parents[1]
    http = json.loads((root / "langgraph.json").read_text(encoding="utf-8"))["http"]
    env = dict(os.environ)
    env.update(
        PYTHON_DOTENV_DISABLED="1", LANGGRAPH_HTTP=json.dumps(http),
        REDIS_URI="fake", DATABASE_URI=":memory:", LANGGRAPH_RUNTIME_EDITION="inmem",
        LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false",
        API_ADMIN_TOKEN="test-only-integration-token-32-characters",
    )
    # No lifespan: no workers, telemetry, model calls, Redis or database connections.
    result = subprocess.run(
        [sys.executable, "-c", """
from langgraph_api.server import app
from starlette.testclient import TestClient
client = TestClient(app)
for path in ['/api/settings', '/assistants/search', '/threads', '/runs', '/store/items',
             '/info', '/noauth/threads']:
    response = client.get(path)
    assert response.status_code == 401, (path, response.status_code)
headers = {'Authorization': 'Bearer test-only-integration-token-32-characters'}
assert client.get('/info', headers=headers).status_code == 200
assert client.get('/api/settings', headers=headers).status_code == 200
"""],
        cwd=root, env=env, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
