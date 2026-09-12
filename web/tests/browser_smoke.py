"""Browser regression checks against a local fixture API (no model calls).

Run after npm run build with the project venv. Uses the installed Chromium
and websockets package; UI_TEST_CHROME can point to another Chromium binary.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / ".tmp" / "ui-smoke"
ARTIFACTS.mkdir(parents=True, exist_ok=True)


# Имена исполняемого файла Chromium в порядке предпочтения. Соседние файлы
# каталога — chrome_crashpad_handler, chrome_sandbox — под точное совпадение
# не попадают.
CHROME_BINARIES = ("chrome.exe", "chrome", "Google Chrome for Testing", "Chromium")


def _chrome() -> Path:
    """
    Chromium для проверок: `UI_TEST_CHROME`, иначе кеш Playwright.

    Ни ревизия, ни раскладка каталога внутри неё не угадываются. Ревизия — не
    константа: зашитый номер отвалился бы на первом же обновлении Playwright.
    Подкаталог — тем более: он называется `chrome-win64` на Windows,
    `chrome-linux64` на Linux и `chrome-mac-*/…​.app/Contents/MacOS` на macOS,
    и написанная по памяти строка `chrome-linux` уже один раз уронила CI при
    полностью установленном браузере. Поэтому берётся самая свежая ревизия, а
    бинарник ищется в ней по имени.

    Каталог `chromium_headless_shell-*` под шаблон не попадает намеренно:
    оболочка не принимает часть флагов обычного Chromium.
    """
    if override := os.environ.get("UI_TEST_CHROME"):
        return Path(override)
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Caches" / "ms-playwright"
    else:
        root = Path.home() / ".cache" / "ms-playwright"
    installed = sorted(
        (
            (int(entry.name.rpartition("-")[2]), entry)
            for entry in root.glob("chromium-*")
            if entry.name.rpartition("-")[2].isdigit()
        ),
        reverse=True,
    )
    for _, entry in installed:
        for name in CHROME_BINARIES:
            binary = next((found for found in entry.rglob(name) if found.is_file()), None)
            if binary is not None:
                return binary
    raise SystemExit(
        f"Chromium не найден в {root}. Поставьте его командой "
        "`python -m playwright install chromium` или укажите путь в UI_TEST_CHROME."
    )


CHROME = _chrome()
MESSAGES = [
    {
        "id": str(i),
        "type": "human" if i % 2 == 0 else "ai",
        "content": f"Message {i}: " + "test " * 80,
    }
    for i in range(1000)
]
REQUESTS: list[str] = []
INTERRUPTED = False


def manifest(graph: str) -> dict:
    return {
        "schema_version": "1.0",
        "manifest_version": "1.0.0",
        "graph_id": graph,
        "title": graph,
        "description": "Browser fixture",
        "nodes": {"context": {"title": "Context"}, "analyst": {"title": "Analyst"}},
        "input": [{"id": "question", "target": "messages", "widget": "chat-input"}],
        "state": [{"id": "messages", "path": "messages", "widget": "messages", "surface": "main"}],
        "surfaces": [{"id": "main", "widgets": ["messages", "question"]}],
        "actions": [{"kind": "run.start"}, {"kind": "run.stop"}],
        "capabilities": {"stop_run": True},
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "web" / "dist"), **kwargs)

    def log_message(self, *_args):
        pass

    def reply(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        REQUESTS.append(self.path)
        if self.path == "/info":
            return self.reply({})
        if self.path == "/api/ui/capabilities":
            return self.reply({"engine": "orbita-ui", "manifest_versions": ["1.0"], "features": {}})
        if "/bundle?" in self.path:
            if INTERRUPTED:
                time.sleep(0.8)
            graph = self.path.split("graph_id=")[-1]
            return self.reply(
                {
                    "assistant": {"assistant_id": graph, "graph_id": graph},
                    "manifest": manifest(graph),
                }
            )
        if self.path.endswith("/manifest"):
            return self.reply(manifest(self.path.split("/")[-2]))
        if self.path.endswith("/graph"):
            ids = ["__start__", "context", "analyst", "__end__"]
            return self.reply(
                {
                    "nodes": [{"id": node} for node in ids],
                    "edges": [
                        {"source": a, "target": b} for a, b in zip(ids, ids[1:], strict=False)
                    ],
                }
            )
        if self.path.startswith("/threads/"):
            values = {"messages": MESSAGES}
            if INTERRUPTED:
                values["__interrupt__"] = [
                    {"id": "saved-decision", "value": {"question": "Continue?"}}
                ]
            return self.reply(
                {
                    "values": values,
                    "next": [],
                    "tasks": [],
                    "checkpoint": {"checkpoint_id": "test"},
                    "metadata": {},
                }
            )
        return super().do_GET()

    def do_POST(self):
        global INTERRUPTED
        REQUESTS.append(self.path)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/fixture/interrupt":
            INTERRUPTED = True
            return self.reply({})
        if self.path == "/assistants/search":
            return self.reply(
                [
                    {"assistant_id": graph, "graph_id": graph, "name": graph}
                    for graph in ["agent", "demo"]
                ]
            )
        if self.path == "/api/ui/actions/validate":
            time.sleep(0.3)
            if body.get("payload", {}).get("value") == "Reject":
                return self.reply({"error": "Fixture validation failure"}, 503)
            return self.reply(
                {"valid": True, "idempotency_key": body.get("idempotency_key")}
            )
        if self.path.endswith("/runs/stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                self.wfile.write(
                    b'event: metadata\ndata: {"run_id":"test-run","thread_id":"saved-thread"}\n\n'
                )
                self.wfile.flush()
                time.sleep(2)
                self.wfile.write(b'event: values\ndata: {"messages":[]}\n\n')
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            return
        return self.reply({})


def main(extra_checks=None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    profile = ARTIFACTS / f"profile-{time.time_ns()}"
    browser_log = (ARTIFACTS / "chromium.log").open("w")
    browser = subprocess.Popen(
        [
            str(CHROME),
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--no-first-run",
            "--remote-debugging-port=0",
            f"--user-data-dir={profile}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=browser_log,
        # Окно консоли прячем только там, где оно бывает: флаг Windows-only.
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    try:
        active_port = profile / "DevToolsActivePort"
        deadline = time.monotonic() + 15
        while not active_port.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        time.sleep(0.5)
        port = active_port.read_text().splitlines()[0]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/json") as response:
            page = json.load(response)[0]
        with connect(page["webSocketDebuggerUrl"], max_size=20_000_000, proxy=None) as ws:
            sequence = 0
            errors = []

            def call(method, params=None):
                nonlocal sequence
                sequence += 1
                ws.send(json.dumps({"id": sequence, "method": method, "params": params or {}}))
                while True:
                    result = json.loads(ws.recv(timeout=15))
                    if result.get("method") == "Runtime.exceptionThrown":
                        errors.append(result["params"])
                    if result.get("id") == sequence:
                        assert "error" not in result, result
                        return result.get("result", {})

            def js(expression):
                result = call(
                    "Runtime.evaluate",
                    {"expression": expression, "returnByValue": True, "awaitPromise": True},
                )
                assert "exceptionDetails" not in result, result
                return result.get("result", {}).get("value")

            def until(expression):
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if js(expression):
                        return
                    time.sleep(0.1)
                raise AssertionError(
                    {
                        "condition": expression,
                        "errors": errors,
                        "page": js("document.body.innerText.slice(0, 2000)"),
                        "requests": REQUESTS[-20:],
                    }
                )

            def click(label):
                js(
                    f"[...document.querySelectorAll('button')].find(b=>b.textContent.includes({json.dumps(label)})).click()"
                )

            call("Runtime.enable")
            call("Page.enable")
            call(
                "Emulation.setDeviceMetricsOverride",
                {"width": 1366, "height": 768, "deviceScaleFactor": 1, "mobile": False},
            )
            call(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": "localStorage.setItem('orbita.thread.agent','saved-thread');"},
            )
            call("Page.navigate", {"url": f"http://127.0.0.1:{server.server_port}"})
            until("document.querySelectorAll('.msg').length === 50")
            assert js("document.querySelector('.pick').getBoundingClientRect().width > 0"), (
                "Agent picker is hidden on desktop"
            )
            assert not any("/history" in path for path in REQUESTS), REQUESTS
            assert js("document.querySelectorAll('.engine-timeline').length") == 0
            js(
                "window.framesPainted=0; new MutationObserver(()=>window.framesPainted++).observe(document.querySelector('.orbit'), {childList:true});"
            )
            time.sleep(1)
            assert js("window.framesPainted") == 0, "Idle background keeps rendering"
            click("анимация:")
            until("window.framesPainted > 2")
            call(
                "Emulation.setEmulatedMedia",
                {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]},
            )
            time.sleep(0.2)
            js("window.framesPainted=0")
            time.sleep(0.4)
            assert js("window.framesPainted") == 0, "Reduced motion keeps animating"
            click("анимация:")
            click("показать предыдущие")
            until("document.querySelectorAll('.msg').length === 100")
            click("подробности")
            until("document.querySelector('.engine-timeline') !== null")
            screenshot = call("Page.captureScreenshot")["data"]
            (ARTIFACTS / "desktop.png").write_bytes(base64.b64decode(screenshot))
            for width in [1366, 900, 390]:
                call(
                    "Emulation.setDeviceMetricsOverride",
                    {"width": width, "height": 844, "deviceScaleFactor": 1, "mobile": width < 700},
                )
                time.sleep(0.15)
                assert js("document.querySelector('.app').scrollWidth <= window.innerWidth + 1"), (
                    f"Horizontal overflow at {width}"
                )
            (ARTIFACTS / "mobile.png").write_bytes(
                base64.b64decode(call("Page.captureScreenshot")["data"])
            )
            js(
                "const draft=document.querySelector('textarea'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(draft,'Reject'); draft.dispatchEvent(new Event('input',{bubbles:true}));"
            )
            click("запустить")
            until(
                "document.querySelector('[role=alert]')?.textContent.includes('Fixture validation failure')"
            )
            assert js("document.querySelector('textarea').value") == "Reject", (
                "Rejected submission erased the draft"
            )
            js(
                "const input=document.querySelector('textarea'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,'Test'); input.dispatchEvent(new Event('input',{bubbles:true}));"
            )
            until(
                "[...document.querySelectorAll('button')].some(b=>b.textContent.includes('запустить')&&!b.disabled)"
            )
            click("запустить")
            click("запустить")
            until("document.querySelector('select[aria-label=\"Агент\"]').disabled")
            until(
                "[...document.querySelectorAll('button')].some(b=>b.textContent.includes('остановить'))"
            )
            assert js(
                "[...document.querySelectorAll('button')].find(b=>b.textContent.includes('новый диалог')).disabled"
            )
            click("остановить")
            until("document.querySelector('.status').textContent.includes('остановлен')")
            time.sleep(0.5)
            assert js("document.querySelector('.status').textContent.includes('остановлен')")
            assert REQUESTS.count("/threads/saved-thread/runs/stream") == 1, "Duplicate submission"
            until(
                "![...document.querySelectorAll('button')].find(b=>b.textContent.includes('новый диалог')).disabled"
            )
            click("новый диалог")
            until("document.querySelectorAll('.msg').length === 0")
            js(
                "const picker=document.querySelector('.pick select'); picker.value='demo'; picker.dispatchEvent(new Event('change',{bubbles:true}));"
            )
            until(
                "document.querySelector('.pick select').value === 'demo' && document.querySelector('.graph') !== null"
            )
            assert js("document.querySelectorAll('.msg').length") == 0
            additional = extra_checks(call, js, until, click) if extra_checks else []
            js("fetch('/fixture/interrupt',{method:'POST'})")
            call(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": "localStorage.setItem('orbita.graph','agent');"},
            )
            call("Page.reload")
            until(
                "!!document.querySelector('.graph') && !!document.querySelector('.engine-interrupt-overlay')"
            )
            time.sleep(0.3)
            assert js("document.querySelector('.engine-interrupt-overlay') !== null"), (
                "Delayed bundle erased the saved interrupt"
            )
            assert js("document.querySelector('.engine-interrupt-overlay').matches(':modal')")
            call(
                "Input.dispatchKeyEvent",
                {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27},
            )
            call(
                "Input.dispatchKeyEvent",
                {"type": "keyUp", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27},
            )
            assert js("document.querySelector('.engine-interrupt-overlay').matches(':modal')"), (
                "Escape dismissed approval"
            )
            assert not errors, errors
            print(
                json.dumps(
                    {
                        "passed": True,
                        "checks": [
                            "1000-message history limited to 50",
                            "older messages accessible",
                            "no full checkpoint history request",
                            "idle scene paints zero frames",
                            "animation respects reduced motion",
                            "journal mounts on demand",
                            "desktop/tablet/mobile overflow",
                            "agent picker visible",
                            "active run locks graph and thread",
                            "double submission blocked",
                            "rejected submission preserves draft",
                            "cancellation stays cancelled",
                            "new thread clears messages",
                            "graph switching",
                            "saved interrupt survives delayed bundle",
                        ]
                        + additional,
                        "screenshots": str(ARTIFACTS),
                    },
                    ensure_ascii=False,
                )
            )
            # Let Chromium close its child processes and profile before waiting.
            ws.send(json.dumps({"id": sequence + 1, "method": "Browser.close"}))
    finally:
        # Уборка не имеет права решать судьбу проверок. Chromium иногда не
        # успевает закрыть дочерние процессы и за десять секунд после
        # `Browser.close`, и раньше такой прогон — со всеми зелёными
        # проверками — падал на таймауте ожидания. Поэтому здесь лестница
        # «подождать → попросить → убить», и ни одна ступень не бросает.
        for step in (None, browser.terminate, browser.kill):
            if step is not None:
                step()
            try:
                browser.wait(timeout=10)
                break
            except subprocess.TimeoutExpired:
                continue
        browser_log.close()
        server.shutdown()


if __name__ == "__main__":
    main()
