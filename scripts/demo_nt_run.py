"""Exercise the real graph + HTTP runner + real k6 against a temporary loopback API.

Only LLM decisions are scripted. No model key, external target or metric server is used.
"""

import argparse
import asyncio
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main():
    # Clean configuration before importing modules that may load provider settings.
    for name in list(os.environ):
        if name.startswith(("NT_", "LLM_", "JIRA_", "CONFLUENCE_", "LANGSMITH_", "LANGCHAIN_",
                            "MEMORY_", "KNOWLEDGE_", "PUBLISH_", "CHECKPOINT_", "BUDGET_", "AGENT_")):
            os.environ.pop(name, None)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["LANGSMITH_TRACING"] = "false"

    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage, HumanMessage
    from scripts.serve_nt_runner import make_handler

    from agent.nt_run.client import RunnerHTTP
    from agent.nt_run.runner import Runner
    from agent.nt_run_graph import build_graph

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--k6", default="k6")
    parser.add_argument("--check-stops", action="store_true", help="Also verify explicit stop and heartbeat expiry")
    args = parser.parse_args()
    count = [0]
    orders = []

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            count[0] += 1
            body = b'{"status":"ok"}'
            self.send_response(200 if self.path == "/orders/created-1" else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            count[0] += 1
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            orders.append(body)
            valid = self.path == "/orders" and body == {"sku": "test-product"}
            response = b'{"data":{"id":"created-1"}}'
            self.send_response(201 if valid else 400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *_):
            pass

    target = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    root = Path(".nt-runs") / ("demo-" + uuid.uuid4().hex[:10])
    runner = Runner(root, {"k6_binary": str(Path(args.k6).resolve()), "targets": {"local": {
        "url": f"http://127.0.0.1:{target.server_port}", "target_service": "demo-api",
        "environment": "nt", "namespace": "local"}}})
    token = uuid.uuid4().hex
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(runner, token))
    for service in (target, server):
        threading.Thread(target=service.serve_forever, daemon=True).start()
    # The nt subgraph intentionally reports INCONCLUSIVE when historical telemetry is absent.
    os.environ["NT_RUNNER_URL"] = f"http://127.0.0.1:{server.server_port}"
    os.environ["NT_RUNNER_TOKEN"] = token
    os.environ["NT_METRIC_QUERIES"] = "{}"
    os.environ["PUBLISH_REQUIRE_APPROVAL"] = "0"
    plan = {"objective": "Проверить локальный демонстрационный HTTP API", "target": "local",
            "target_rps": 2, "duration_seconds": 3, "virtual_users": 1,
            "sla_p95_ms": 500.0, "sla_error_rate": .01,
            "stop_p95_ms": 2000.0, "stop_error_rate": .2,
            "dataset": [{"sku": "test-product"}],
            "steps": [{"name": "create", "method": "POST", "path": "/orders", "expected_status": 201,
                       "body": {"sku": "{{sku}}"}, "extract": {"order_id": "data.id"}},
                      {"name": "read", "path": "/orders/{{order_id}}"}]}
    model = GenericFakeChatModel(messages=iter([
        AIMessage(content="", tool_calls=[{"id": "plan", "name": "write_test_files", "args": {"plan": plan}}]),
        AIMessage(content='{"action":"ready"}'),
        AIMessage(content='{"action":"finish","conclusion":"Локальный прогон выполнен. Исторические метрики не подключены."}'),
    ]))
    app = build_graph(model, runner=RunnerHTTP.from_env(), auto_approve=True, poll_seconds=.5).compile()
    try:
        state = asyncio.run(app.ainvoke({"messages": [HumanMessage("Выполни локальную проверку k6")]},
            {"configurable": {"publish": False}, "recursion_limit": 200}))
        (root / "report.md").write_text(state["artifacts"]["report"], encoding="utf-8")
        result = {"requests_received": count[0], "runs": [
            {"kind": r["kind"], "test_id": r["result"]["test_id"], "status": r["result"]["test_status"],
             "summary": r["result"].get("summary")} for r in state["runs"]],
            "analysis": state.get("run_analysis"), "report": str((root / "report.md").resolve())}
        print(json.dumps(result, ensure_ascii=True, indent=2))
        assert len(state["runs"]) == 2, state.get("last_error")
        assert all(r["result"]["test_status"] == "completed" for r in state["runs"]), result
        assert count[0] >= 4
        assert orders and all(row == {"sku": "test-product"} for row in orders)
        if args.check_stops:
            def wait_terminal(test_id, heartbeat=True):
                until = time.time() + 45
                while time.time() < until:
                    status = runner.get_test_status(test_id)
                    if status["test_status"] in {"completed", "stopped", "failed"}:
                        return status
                    if heartbeat:
                        runner.heartbeat(test_id)
                    time.sleep(.5)
                raise AssertionError("test did not terminate")

            longer = {**plan, "duration_seconds": 35}
            prepared = runner.prepare_test(longer, "stops")["prepared_id"]
            smoke = runner.start_test(prepared, "stop-smoke", smoke=True)
            assert wait_terminal(smoke["test_id"])["test_status"] == "completed"
            explicit = runner.start_test(prepared, "explicit-stop")
            time.sleep(1)
            stopped = runner.stop_test(explicit["test_id"])
            assert stopped["test_status"] == "stopped"
            print("Explicit stop: confirmed", flush=True)
            lease = runner.start_test(prepared, "lease-expiry")
            assert runner.start_test(prepared, "lease-expiry")["test_id"] == lease["test_id"]
            stopped = wait_terminal(lease["test_id"], heartbeat=False)
            assert stopped["test_status"] == "stopped" and stopped["stop_reason"] == "lease_expired", stopped
            print("Lost graph heartbeat: independently stopped by worker", flush=True)
    finally:
        for service in (server, target):
            service.shutdown()
            service.server_close()


if __name__ == "__main__":
    main()
