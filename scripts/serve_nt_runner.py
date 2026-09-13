"""Run a separate, authenticated local k6 control server. See docs/NT_RUN.md."""

import argparse
import hmac
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dotenv import load_dotenv

from agent.nt_run.runner import Runner


def make_handler(runner, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, code, data):
            body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def handle_api(self, method):
            if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                self.respond(401, {"success": False, "error": "unauthorized"})
                return
            try:
                payload = {}
                if method == "POST":
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 <= size <= 128000:
                        raise ValueError("request too large")
                    payload = json.loads(self.rfile.read(size) or b"{}")
                    if not isinstance(payload, dict):
                        raise ValueError("object required")
                match = re.fullmatch(r"/tests/(k6-[a-f0-9]{40})(/results|/stop|/heartbeat)?", self.path)
                if method == "GET" and self.path == "/capabilities":
                    data = runner.capabilities()
                elif method == "POST" and self.path == "/prepare":
                    data = runner.prepare_test(payload["plan"], payload["key"])
                elif method == "POST" and self.path == "/start":
                    data = runner.start_test(payload["prepared_id"], payload["key"], smoke=payload.get("smoke", False))
                elif match:
                    test_id, suffix = match.groups()
                    operation = {("GET", None): runner.get_test_status, ("GET", "/results"): runner.get_test_results,
                                 ("POST", "/stop"): runner.stop_test, ("POST", "/heartbeat"): runner.heartbeat}.get((method, suffix))
                    if not operation:
                        raise ValueError("unsupported operation")
                    data = operation(test_id)
                else:
                    self.respond(404, {"success": False, "error": "not found"})
                    return
                # Flat test metadata also satisfies the existing historical HTTPLoadTesting adapter.
                self.respond(200, {**(data if match else {}), "success": True, "data": data})
            except (ValueError, KeyError, TypeError, OSError):
                self.respond(400, {"success": False, "error": "invalid request or unavailable runner operation"})

        def do_GET(self):
            self.handle_api("GET")

        def do_POST(self):
            self.handle_api("POST")

    return Handler


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/nt-runner.json")
    parser.add_argument("--root", default=".nt-runs")
    parser.add_argument("--port", type=int, default=8077)
    parser.add_argument("--release", metavar="TEST_ID",
                        help="Recovery for a worker that vanished: mark the test failed and unblock "
                             "the runner. Run it only after checking that no k6 process survives.")
    args = parser.parse_args()
    runner = Runner(Path(args.root), json.loads(Path(args.config).read_text(encoding="utf-8")))
    if args.release:
        # Recovery is a local operator command on purpose: the control API never offers it.
        released = runner.release_lost_job(args.release)
        print(f"released {released['test_id']}: {released['test_status']} "
              f"({released.get('stop_reason')})", flush=True)
        return
    token = os.getenv("NT_RUNNER_TOKEN", "")
    if len(token) < 16:
        parser.error("NT_RUNNER_TOKEN must contain at least 16 characters")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(runner, token))
    print(f"k6 runner: http://127.0.0.1:{args.port}; artifacts: {runner.root}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
