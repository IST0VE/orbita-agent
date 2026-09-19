"""Real pointer/keyboard regressions for the graph, against a cyclic fixture API.

Run after npm run build: .venv/Scripts/python.exe web/tests/browser_canvas.py
No backend or model calls. Artifacts: .tmp/ui-smoke/canvas-*.png.
"""
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import browser_smoke as smoke
from playwright.sync_api import expect, sync_playwright

TITLES = {
    "__start__": "Старт", "context": "Контекст", "requirements": "Системные требования",
    "tools": "Инструменты", "gate_api": "Ворота · Контракт API", "api": "Контракт API",
    "gate_data": "Ворота · Данные", "data": "Данные и события",
    "gate_architecture": "Ворота · Архитектура", "architecture": "Архитектура",
    "gate_review": "Ворота · Ревью", "review": "Ревью",
    "over_budget": "Лимит бюджета", "halted": "Остановлено оператором",
    "remember": "Память", "prepare_publish": "Подготовка публикации",
    "approve": "Подтверждение публикации", "publish": "Публикация", "__end__": "Конец",
}
CHAIN = ["__start__", "context", "requirements", "gate_api", "api", "gate_data", "data",
         "gate_architecture", "architecture", "gate_review", "review", "remember",
         "prepare_publish", "approve", "publish", "__end__"]
LARGE_IDS = ["__start__", "context", *[f"stage_{i}" for i in range(76)], "__end__"]
LARGE_EDGES = [{"source": a, "target": b} for a, b in zip(LARGE_IDS, LARGE_IDS[1:], strict=False)]
LARGE_EDGES += [{"source": f"stage_{i}", "target": f"stage_{i-3}", "conditional": True} for i in range(5, 76, 7)]
EDGES = [{"source": a, "target": b} for a, b in zip(CHAIN, CHAIN[1:], strict=False)]
EDGES += [{"source": "requirements", "target": "tools", "conditional": True},
          {"source": "tools", "target": "requirements", "conditional": True},
          {"source": "tools", "target": "over_budget", "conditional": True},
          {"source": "__start__", "target": "over_budget", "conditional": True},
          {"source": "over_budget", "target": "remember"},
          {"source": "halted", "target": "remember"}]
for gate in (key for key in TITLES if key.startswith("gate_")):
    EDGES += [{"source": gate, "target": target, "conditional": True} for target in ["over_budget", "halted"]]

base_manifest = smoke.manifest


def graph_menu(page, label):
    """
    Действие из меню схемы.

    Мини-карта, авторасстановка и сброс масштаба уехали из панели в меню за
    многоточием: на виду остались поиск, масштаб и переключатель представления.
    Проверки ходят тем же путём, что оператор, — через публичный интерфейс.
    """
    page.get_by_role("button", name="Ещё действия со схемой", exact=True).click()
    page.locator(".menu-list .menu-item").filter(has_text=label).click()


def manifest(graph):
    value = base_manifest(graph)
    value["nodes"] = {key: {"title": title, "description": f"Этап: {title}",
                            "kind": "router" if key.startswith("gate_") else "task"}
                      for key, title in TITLES.items() if not key.startswith("__")}
    value["state"].append({"id": "published", "path": "publication", "widget": "published-list", "surface": "left"})
    value["actions"].append({"kind": "publication.open"})
    return value


class Handler(smoke.Handler):
    def do_GET(self):
        if self.path.startswith("/api/ui/resources/orbita.publications?"):
            query = parse_qs(urlsplit(self.path).query)
            if query.get("operation") == ["read"]:
                return self.reply({"name": "report.md", "text": "# Проверочный документ\n\nСодержимое результата."})
            return self.reply({"documents": [{"name": "report.md", "title": "Документ проверки", "size": 42}]})
        if self.path.endswith("/graph"):
            if "/demo/graph" in self.path:
                return self.reply({"nodes": [{"id": key} for key in LARGE_IDS], "edges": LARGE_EDGES})
            return self.reply({"nodes": [{"id": key} for key in TITLES], "edges": EDGES})
        return super().do_GET()


def main():
    smoke.manifest = manifest
    smoke.MESSAGES = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(smoke.CHROME), headless=True)
            page = browser.new_page(viewport={"width": 1600, "height": 1000}, has_touch=True)
            page.add_init_script("localStorage.setItem('orbita.thread.agent','saved-thread')")
            workers = []
            page.on("worker", lambda worker: workers.append(worker.url))
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            page.goto(f"http://127.0.0.1:{server.server_port}")
            try:
                page.locator(".react-flow__node").first.wait_for(timeout=15000)
            except Exception:
                print(json.dumps({"errors": errors, "layout": page.locator('.canvas-layout-state').get_attribute('title')}, ensure_ascii=False))
                raise
            page.wait_for_timeout(500)
            assert page.locator(".react-flow__node").count() == len(TITLES)
            assert page.locator(".react-flow__edge-path").count() == len(EDGES)
            assert page.locator('.manifest-warning').count() == 0
            page.screenshot(path=str(smoke.ARTIFACTS / "canvas-overview.png"))

            view = page.locator(".react-flow__viewport")
            graph = page.locator(".graph-flow")
            bounds = graph.bounding_box()
            assert bounds
            before = view.get_attribute("style")
            x, y = bounds["x"] + bounds["width"] * 0.5, bounds["y"] + 35
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x + 90, y + 70, steps=10)
            page.mouse.up()
            assert view.get_attribute("style") != before, "Left drag did not pan the default diagram"
            assert page.locator(".node-details").count() == 0

            # Zoom must preserve the world point under the pointer.
            world_point = """([x,y]) => {
              const r=document.querySelector('.graph-flow').getBoundingClientRect();
              const m=new DOMMatrix(getComputedStyle(document.querySelector('.react-flow__viewport')).transform);
              const p=new DOMPoint(x-r.x,y-r.y).matrixTransform(m.inverse());
              return {x:p.x,y:p.y,zoom:m.a};
            }"""
            anchor_before = page.evaluate(world_point, [x, y])
            page.mouse.move(x, y)
            page.keyboard.down("Control")
            page.mouse.wheel(0, -150)
            page.keyboard.up("Control")
            page.wait_for_timeout(200)
            anchor_after = page.evaluate(world_point, [x, y])
            assert anchor_after['zoom'] > anchor_before['zoom'], "Ctrl+wheel did not zoom"
            assert abs(anchor_before['x']-anchor_after['x']) < 1 and abs(anchor_before['y']-anchor_after['y']) < 1

            # Touch panning uses the same camera, without native page scrolling.
            cdp = page.context.new_cdp_session(page)
            touch_before = view.get_attribute("style")
            cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
            for step in range(1, 6):
                cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x + step * 8, "y": y + step * 5}]})
            cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            assert view.get_attribute("style") != touch_before, "Touch drag did not pan"

            # At 100%, drag displacement must match pointer displacement. The
            # card is centered first so there is no accidental auto-pan.
            page.locator('.react-flow__pane').click(position={"x": 20, "y": 20})
            page.keyboard.press("0")
            page.wait_for_timeout(100)
            # Fit then zoom by a toolbar click, keeping all tests in public UI.
            page.get_by_role("button", name="Вписать граф", exact=True).click()
            node = page.locator('.react-flow__node[data-id="requirements"]')
            # fitView применяется асинхронно. hover ждёт стабильной карточки
            # и попадания указателя в неё, прежде чем мы читаем координаты.
            node.hover()
            original_position = node.get_attribute("style")
            rect = node.bounding_box()
            assert rect
            sx, sy = rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2
            paths_before = page.locator(".react-flow__edge-path").evaluate_all("els => els.map(e=>e.getAttribute('d'))")
            page.mouse.move(sx, sy)
            page.mouse.down()
            page.mouse.move(sx + 35, sy + 65, steps=12)
            page.mouse.up()
            expect(node, "Dragging a node did not change its position").not_to_have_attribute(
                "style", original_position,
            )
            moved_position = node.get_attribute("style")
            assert page.locator(".node-details").count() == 0, "Dragging opened node details"
            assert page.locator(".react-flow__edge-path").evaluate_all("els => els.map(e=>e.getAttribute('d'))") != paths_before

            # Every SVG endpoint must coincide with the corresponding handle,
            # including after pan/zoom/drag. Compare in screen coordinates.
            deviations = page.evaluate("""() => [...document.querySelectorAll('.react-flow__edge')].flatMap(edge => {
              const path = edge.querySelector('.react-flow__edge-path');
              const i = edge.dataset.id.replace('edge-', '');
              return [['out-'+i, 0], ['in-'+i, path.getTotalLength()]].map(([id, length]) => {
                const h = document.querySelector('[data-handleid="'+id+'"]');
                const r = h.getBoundingClientRect();
                const pt = path.getPointAtLength(length).matrixTransform(path.getScreenCTM());
                return Math.hypot(pt.x-r.x-r.width/2, pt.y-r.y-r.height/2);
              });
            })""")
            assert max(deviations) < 1.5, deviations

            camera = view.get_attribute("style")
            # Схема и список — представления графа; схема, результат и документ
            # — виды рабочей области. Имена совпадают, адресация разная.
            page.locator('[data-representation="list"]').click()
            page.locator('[data-representation="diagram"]').click()
            assert node.get_attribute("style") == moved_position, "Tab switch reset manual positions"
            assert view.get_attribute("style") == camera, "Tab switch reset the viewport"
            page.locator('.engine-outline summary').click()
            page.get_by_role("button", name="Документ проверки").click()
            page.locator('.workspace-bar .tab[data-view="document"]').wait_for()
            assert not graph.is_visible()
            page.locator('.workspace-bar .tab[data-view="graph"]').click()
            assert node.get_attribute("style") == moved_position, "Opening a document reset manual positions"
            assert view.get_attribute("style") == camera, "Opening a document reset the viewport"

            page.locator('textarea').fill("Test")
            page.get_by_role("button", name="Запустить", exact=True).click()
            page.get_by_role("button", name="Остановить", exact=True).wait_for()
            assert node.get_attribute("style") == moved_position, "Run update reset manual positions"
            assert view.get_attribute("style") == camera, "Run update reset the viewport"
            page.get_by_role("button", name="Остановить", exact=True).click()
            page.get_by_role("button", name="Запустить", exact=True).wait_for()
            graph_menu(page, "Мини-карта")
            assert page.locator(".react-flow__minimap").count() == 1
            assert node.get_attribute("style") == moved_position

            page.get_by_role("button", name="Вписать граф", exact=True).click()
            node.click()
            assert page.locator(".node-details").count() == 1
            page.keyboard.press("Escape")
            node.focus()
            page.keyboard.press("Enter")
            assert page.locator(".node-details").count() == 1, "Enter did not open node details"
            keyboard_position = node.get_attribute("style")
            page.keyboard.press("ArrowDown")
            assert node.get_attribute("style") != keyboard_position, "Arrow key did not move selected node"
            page.keyboard.press("Space")
            assert page.locator(".node-details").count() == 0, "Space did not toggle node selection"
            graph_menu(page, "Авторасстановка")
            page.locator(".canvas-layout-state").wait_for(state="hidden")
            page.wait_for_timeout(200)
            assert node.get_attribute("style") == original_position, "Auto-layout did not restore initial positions"
            page.screenshot(path=str(smoke.ARTIFACTS / "canvas-minimap.png"))

            # Large graph uses a separate routing worker, and remains draggable.
            page.locator('.pick-button').click()
            page.locator('.pick-menu [data-graph=demo]').click()
            page.locator('.react-flow__node').nth(len(LARGE_IDS) - 1).wait_for()
            assert page.locator('.react-flow__node').count() == len(LARGE_IDS)
            page.wait_for_timeout(250)
            large_node = page.locator('.react-flow__node[data-id="context"]')
            large_node.hover()
            large_before = large_node.get_attribute('style')
            rect = large_node.bounding_box()
            assert rect
            x, y = rect['x'] + rect['width']/2, rect['y'] + rect['height']/2
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x+50, y+70, steps=12)
            page.mouse.up()
            expect(large_node, "Dragging a large-graph node did not change its position").not_to_have_attribute(
                "style", large_before,
            )
            assert any('routing.worker' in url for url in workers), workers
            assert page.locator('.react-flow__edge-path').count() == len(LARGE_EDGES)
            page.screenshot(path=str(smoke.ARTIFACTS / "canvas-large.png"))

            # An unavailable layout worker is recoverable, not an empty canvas.
            failure = browser.new_page(viewport={"width": 1366, "height": 900})
            failure.route('**/assets/elk-worker*', lambda route: route.abort())
            failure.goto(f"http://127.0.0.1:{server.server_port}")
            failure.get_by_role('button', name='Повторить', exact=True).wait_for()
            failure.unroute('**/assets/elk-worker*')
            failure.get_by_role('button', name='Повторить', exact=True).click()
            failure.locator('.react-flow__node').first.wait_for()
            failure.close()
            assert not errors, errors
            browser.close()
            print(json.dumps({"passed": True, "nodes": len(TITLES), "edges": len(EDGES),
                              "checks": ["default left-drag pan", "node drag vs click", "live attached endpoints",
                                         "tab persistence", "minimap", "details", "auto-layout",
                                         "Ctrl+wheel cursor anchor", "touch pan", "keyboard selection and movement",
                                         "document and runtime preserve manual geometry", "large graph routing worker",
                                         "layout failure and retry"]}))
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
