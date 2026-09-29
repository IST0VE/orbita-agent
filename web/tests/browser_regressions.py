"""Run browser_smoke plus frontend audit regressions against in-memory fixtures.

After npm run build: .venv/Scripts/python.exe web/tests/browser_regressions.py
No live backend, model calls, credentials or real settings writes.
"""

import base64
import json
import threading
import time
import urllib.parse

import browser_smoke as smoke

SETTINGS = {"TEST_VALUE": "original", "TEST_SECRET": "original-secret"}
NOTES = {"TEST_VALUE": "original note"}
# Состояние /info: ok — сервер отвечает, unauthorized — отказ по токену,
# offline — сервер недоступен. Первые два внешне неразличимы без проверки.
HEALTH = "ok"
PUBLICATIONS_OK = True
SAVE_OK = True
APPROVAL = False

# Состояние прогона с проблемой: план публикации устарел после подтверждения.
# Ровно тот случай, ради которого итог стоит первым — иначе его собирают
# глазами по четырём блокам JSON.
RUN_STATE = {
    "summary": {
        "outcome": ["Документов этапов: 2", "Публикация: stale"],
        "problems": [
            "Публикация: план изменился после подтверждения",
            "Вызовов по неизвестному тарифу: 2",
        ],
        "next": [
            "Повторите ход и подтвердите публикацию по новому плану.",
            "Задайте тариф модели: иначе денежный лимит не проверяется.",
        ],
    },
}

# Остановка перед публикацией с различиями: «перезапишет существующую» без
# них — предупреждение без содержания.
APPROVAL_PAYLOAD = {
    "action": "publish",
    "target": "file",
    "title": "Сохранить документы",
    "drafts": [
        {
            "id": "requirements",
            "kind": "page",
            "title": "Orbita: тема — 01 Требования",
            "action": "update",
            "where": "file",
            "format": "markdown",
            "document": "новая версия документа",
            "chars": 24,
            "fields": [{"label": "Файл", "value": "/data/published/orbita-01.md"}],
            "diff": {
                "available": True,
                "added": 2,
                "removed": 1,
                "unchanged": False,
                "text": "\n".join([
                    "--- сейчас", "+++ после публикации",
                    "-старая строка", "+новая строка", "+ещё строка",
                ]),
            },
        }
    ],
}
MARKDOWN = """**bold** and *italic*

- one
- two

| A | B |
|---|---|
| 1 | 2 |

```python
# code comment
print('<script>not executable</script>')
```

[safe](https://example.com) [bad](javascript:alert%281%29)

<script>window.markdownExecuted = true</script>

![image](https://example.com/image.png)
"""
base_manifest = smoke.manifest


def manifest(graph):
    value = base_manifest(graph)
    # Файлы чата: по ним видно, чей список на экране и не потерян ли он.
    value["input"].append(
        {
            "id": "task",
            "target": "configurable.input_dir",
            "widget": "chat-files",
            "title": "Файлы чата",
            "options": {"fixed": "@chat"},
        }
    )
    value["input"].append(
        {
            "id": "array",
            "target": "configurable.items",
            "widget": "form",
            "options": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "items": {"type": "array", "items": {"type": "string"}},
                    },
                }
            },
        }
    )
    value["state"].extend(
        [
            {"id": "document", "path": "document", "widget": "markdown", "surface": "right"},
            {
                "id": "published",
                "path": "publication",
                "widget": "published-list",
                "surface": "left",
            },
            # Итог прогона: то, ради чего оператор открывает экран. Привязан
            # к состоянию целиком — он сводит воедино то, что лежит в четырёх
            # разных местах (см. `run-summary` в builtins).
            {"id": "summary", "path": "summary", "widget": "run-summary", "surface": "right"},
        ]
    )
    value["interrupts"] = [
        {
            "id": "publish-approval",
            "match": {"path": "action", "equals": "publish"},
            "widget": "approval",
            "resume_schema": {
                "type": "object",
                "required": ["decision"],
                "additionalProperties": False,
                "properties": {"decision": {"enum": ["approved", "rejected"]}},
            },
        }
    ]
    return value


REJECT_RUN = False
# Ответ «кто вошёл» придерживается, пока проверка его не отпустит: меню
# профиля открывается раньше, и набор его пунктов меняется под открытым меню.
ME_RELEASED = threading.Event()
ME_RELEASED.set()
# Сколько сервер заводит чат под первый файл черновика: за это время оператор
# успевает уйти в другой чат.
CREATE_DELAY = 0.0
# Куда уехали загруженные файлы: тред на каждый файл.
UPLOADS: list[str] = []
# С каким фильтром интерфейс запрашивал список чатов: он общий, фильтра нет.
SEARCHES: list[object] = []
# Сценарий чата после PATCH: чат без запросов уходит в новый сценарий.
CHAT_GRAPHS: dict[str, str] = {}
PATCHES: list[tuple[str, dict]] = []
LIMITS = {"max_bytes": 1024, "max_files": 10, "suffixes": [".md"]}


class Handler(smoke.Handler):
    def do_POST(self):
        # Отказ уже после успешного preflight: запуск не состоялся, хотя
        # интерфейс к этому моменту успел показать «выполняется».
        if REJECT_RUN and self.path.endswith("/runs/stream"):
            smoke.REQUESTS.append(self.path)
            self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)
            return self.reply({"error": "Fixture run rejected"}, 409)
        if self.path == "/threads/search":
            # Список один на все сценарии: чат agent с разговором, чат demo с
            # разговором и чат demo, в котором только файлы.
            smoke.REQUESTS.append(self.path)
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            SEARCHES.append(body.get("metadata"))
            rows = [
                ("saved-thread", "agent", "Сохранённый чат", True),
                ("demo-thread", "demo", "Чат demo", True),
                ("files-thread", CHAT_GRAPHS.get("files-thread", "demo"), "Файлы без запроса", False),
            ]
            return self.reply(
                [
                    {
                        "thread_id": thread,
                        "status": "idle",
                        "created_at": "2026-09-26T08:00:00+00:00",
                        "updated_at": "2026-09-26T09:00:00+00:00",
                        "metadata": {"graph_id": graph, "title": title},
                        **({"extracted": {"first": title}} if started else {}),
                    }
                    for thread, graph, title, started in rows
                ]
            )
        if self.path == "/threads":
            smoke.REQUESTS.append(self.path)
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            time.sleep(CREATE_DELAY)
            return self.reply(
                {
                    "thread_id": "late-chat",
                    "status": "idle",
                    "created_at": "2026-09-27T08:00:00+00:00",
                    "metadata": body.get("metadata", {}),
                }
            )
        return super().do_POST()

    def reply(self, value, status=200):
        if isinstance(value, dict) and "values" in value:
            value["values"]["document"] = MARKDOWN
            value["values"].update(RUN_STATE)
            if APPROVAL:
                value["values"]["__interrupt__"] = [
                    {"id": "publish-decision", "value": APPROVAL_PAYLOAD}
                ]
        return super().reply(value, status)

    def do_GET(self):
        if self.path == "/api/me":
            ME_RELEASED.wait(timeout=30)
            return super().do_GET()
        if self.path == "/api/settings":
            return self.reply(
                {
                    "path": "fixture.env",
                    # Что применено сейчас: файл и процесс — разные вещи, и
                    # разница между ними объясняет «я же поменял, а работает
                    # по-старому».
                    "applied": [
                        {"name": "AGENT_NAME", "value": "Орбита", "secret": False,
                         "source": "файл", "restart_required": False},
                        {"name": "LLM_MODEL", "value": "из окружения", "secret": False,
                         "source": "окружение", "restart_required": True},
                        {"name": "LLM_API_KEY", "value": "********", "secret": True,
                         "source": "окружение", "restart_required": False},
                    ],
                    "restart_required": True,
                    "note": "Файл читается при старте процесса.",
                    "sections": [
                        {
                            "title": "Test",
                            "fields": [
                                {
                                    "name": "TEST_VALUE",
                                    "description": "Test value",
                                    "default": "",
                                    "kind": "text",
                                    "secret": False,
                                    "editable": True,
                                    "comment": NOTES["TEST_VALUE"],
                                    "filled": True,
                                    "value": SETTINGS["TEST_VALUE"],
                                },
                                {
                                    "name": "TEST_SECRET",
                                    "description": "Test secret",
                                    "default": "",
                                    "kind": "text",
                                    "secret": True,
                                    "editable": True,
                                    "comment": "",
                                    "filled": True,
                                    "value": "ab****yz",
                                },
                            ],
                        }
                    ],
                }
            )
        if self.path == "/info":
            return self.reply({}, {"ok": 200, "unauthorized": 401, "offline": 503}[HEALTH])
        if self.path.startswith("/threads/files-thread"):
            # Чат, в который положили файлы, но ни о чём не спросили.
            smoke.REQUESTS.append(self.path)
            return smoke.Handler.reply(
                self,
                {"values": {}, "next": [], "tasks": [], "checkpoint": {"checkpoint_id": "t"}, "metadata": {}},
            )
        if self.path.startswith("/api/ui/resources/orbita.publications?"):
            if not PUBLICATIONS_OK:
                return self.reply({"error": "Publication fixture unavailable"}, 503)
            return self.reply(
                {"documents": [{"name": "report.md", "title": "Test report", "size": 42}]}
            )
        return super().do_GET()

    def do_PATCH(self):
        # Название или сценарий чата: метаданные треда дописываются, а не заменяются.
        smoke.REQUESTS.append(self.path)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        thread = self.path.split("/")[2]
        PATCHES.append((thread, body))
        graph = body.get("metadata", {}).get("graph_id")
        if graph:
            CHAT_GRAPHS[thread] = graph
        return self.reply({"thread_id": thread, "status": "idle", "metadata": body.get("metadata", {})})

    def do_PUT(self):
        if self.path.startswith("/api/chats/") and "/files?" in self.path:
            thread = self.path.split("/")[3]
            name = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)["name"][0]
            size = len(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            UPLOADS.append(thread)
            file = {"name": name, "size": size, "text": True}
            return self.reply({"thread_id": thread, "files": [file], "limits": LIMITS, "file": file})
        assert self.path == "/api/settings"
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        time.sleep(0.8)
        if not SAVE_OK:
            return self.reply({"error": "Save fixture unavailable"}, 503)
        SETTINGS.update(body.get("values", {}))
        NOTES.update(body.get("comments", {}))
        return self.reply(
            {"saved": list(body.get("values", {})), "path": "fixture.env", "restart_required": []}
        )


def regressions(call, js, until, click, shell):
    global HEALTH, PUBLICATIONS_OK, SAVE_OK, APPROVAL

    open_settings = shell["open_settings"]
    close_settings = shell["close_settings"]
    workspace_view = shell["workspace_view"]

    def fill(selector, value):
        js(f"""(()=>{{const e=document.querySelector({json.dumps(selector)});
            Object.getOwnPropertyDescriptor(e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value').set.call(e,{json.dumps(value)});
            e.dispatchEvent(new Event('input',{{bubbles:true}}));}})()""")

    def show_chat():
        # Открытый чат — вкладка «Чат» правой колонки.
        if not js("!!document.querySelector('.inspector')"):
            js("document.querySelector('[aria-label=\"Колонка чата и подробностей\"]').click()")
        until("!!document.querySelector('.inspector-tab')")
        js("document.querySelector('.inspector-tab').click()")
        until("!!document.querySelector('.chat-panel')")

    def show_materials():
        # Файлы и параметры стоят в колонке чата; у чата с разговором они
        # свёрнуты в строку и раскрываются щелчком.
        show_chat()
        toggle = "document.querySelector('.chat-panel-toggle')"
        until(f"!!{toggle}")
        if js(f"{toggle}.getAttribute('aria-expanded') === 'false'"):
            js(f"{toggle}.click()")
        until(f"{toggle}.getAttribute('aria-expanded') === 'true'")

    def key(name, code, shift=False, raw=False, text=None):
        # Две тонкости протокола. `rawKeyDown` нужен клавишам, чья работа —
        # действие самого браузера, а не обработчик страницы: перевод фокуса
        # по Tab при обычном `keyDown` не выполняется. А `text` нужен там, где
        # проверяется штатная активация элемента: без него Enter доходит до
        # обработчиков, но кнопку не нажимает.
        for kind in ["rawKeyDown" if raw else "keyDown", "keyUp"]:
            call(
                "Input.dispatchKeyEvent",
                {
                    "type": kind,
                    "key": name,
                    "code": name,
                    "windowsVirtualKeyCode": code,
                    "nativeVirtualKeyCode": code,
                    **({"text": text} if text and kind != "keyUp" else {}),
                    "modifiers": 8 if shift else 0,
                },
            )

    call(
        "Emulation.setDeviceMetricsOverride",
        {"width": 1366, "height": 768, "deviceScaleFactor": 1, "mobile": False},
    )
    js(
        "localStorage.setItem('orbita.graph','agent');"
        "localStorage.setItem('orbita.chat', JSON.stringify({thread:'saved-thread',graph:'agent'}))"
    )
    call("Page.reload")
    # Итог прогона занимает главную область, а не колонку справа: вкладка
    # «Результат» — то место, где его читают.
    until("!!document.querySelector('.workspace-bar .tab')", seconds=30)
    workspace_view("Результат")
    until("document.querySelector('.safe-markdown strong')?.textContent === 'bold'")
    assert js("document.querySelectorAll('.safe-markdown table tbody tr').length") == 1
    assert js("document.querySelectorAll('.safe-markdown ul li').length") == 2
    assert js(
        "document.querySelector('.safe-markdown pre code').textContent.includes('# code comment')"
    )
    assert (
        js(
            "document.querySelectorAll('.safe-markdown h1, .safe-markdown script, .safe-markdown img').length"
        )
        == 0
    )
    assert not js(
        "window.markdownExecuted || !!document.querySelector('.safe-markdown a[href^=javascript]')"
    )
    assert js(
        "document.querySelector('.safe-markdown a[href=\"https://example.com/\"]').rel.includes('noopener')"
    )
    for width in [390, 900, 1366]:
        call(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": 844, "deviceScaleFactor": 1, "mobile": width < 700},
        )
        time.sleep(0.2)
        assert js("document.querySelector('.app').scrollWidth <= innerWidth + 1"), (
            f"Markdown overflow at {width}"
        )

    # Поле ввода, которому манифест не назначил поверхность, — это параметр
    # прогона, и стоит он среди параметров чата в правой колонке, а не в поле
    # задачи.
    show_materials()
    until("!!document.querySelector('.inspector [data-widget=form]')")
    array = '[data-widget="form"] textarea'
    fill(array, '["')
    time.sleep(0.1)
    assert js(f"document.querySelector({json.dumps(array)}).value") == '["'
    assert js("document.querySelector('[data-widget=form] button').disabled")
    before = smoke.REQUESTS.count("/api/ui/actions/validate")
    js(
        "document.querySelector('[data-widget=form] form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))"
    )
    assert smoke.REQUESTS.count("/api/ui/actions/validate") == before
    fill(array, '["one", "two"]')
    until("!document.querySelector('[data-widget=form] button').disabled")
    assert js(f"document.querySelector({json.dumps(array)}).value") == '["one", "two"]'
    fill(array, "{}")
    until("document.querySelector('[data-widget=form] button').disabled")
    fill(array, '["one"]')

    # Структурированная форма — это параметры прогона (`input[].target`), а не
    # самостоятельный запуск. Раньше её кнопка слала `run.start` с объектом
    # формы, обработчик читал оттуда только `payload.value` и молча ничего не
    # делал: кнопка обещала ход, которого не было.
    assert js("document.querySelector('[data-widget=form] button').textContent.trim()") == "Применить"
    runs_before = len([path for path in smoke.REQUESTS if path.endswith("/runs/stream")])
    checks_before = smoke.REQUESTS.count("/api/ui/actions/validate")
    js(
        "document.querySelector('[data-widget=form] form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))"
    )
    time.sleep(0.5)
    assert len([path for path in smoke.REQUESTS if path.endswith("/runs/stream")]) == runs_before, (
        "Parameter form started a run"
    )
    assert smoke.REQUESTS.count("/api/ui/actions/validate") == checks_before

    # Представления графа переключаются значками: подписи «Схема» и «Список»
    # рядом с вкладками видов читались как ещё один ряд вкладок.
    workspace_view("Схема")
    js("document.querySelector('[data-representation=\"list\"]').click()")
    until("!!document.querySelector('.graph-list tbody tr')")
    js("document.querySelector('.graph-list tbody tr').focus()")
    key("Enter", 13)
    # Подробности узла открываются в инспекторе, а не карточкой поверх схемы.
    until("!!document.querySelector('.node-details')")
    js("document.querySelector('.inspector-clear').click()")
    until("document.querySelector('.node-details') === null")
    js("document.querySelector('.graph-list tbody tr').focus()")
    key(" ", 32)
    until("!!document.querySelector('.node-details')")
    js("document.querySelector('.inspector-clear').click()")
    js("document.querySelector('[data-representation=\"diagram\"]').click()")

    # Настройки — раздел приложения, а не окно поверх работы. Все переменные
    # целиком лежат в «Продвинутых»; фикстура отдаёт ровно их.
    open_settings(group="Продвинутые")
    until("!!document.querySelector('.set-section input')")
    assert js("document.querySelector('.app-body').hidden"), "Workspace still visible under settings"
    assert js("document.querySelector('dialog') === null"), "Settings must not be a modal"

    fill(".set-section .set-field input", "first")
    click("Править комментарий")
    fill(".set-comment textarea", "first note")
    click("Сохранить")
    until(
        "[...document.querySelectorAll('.settings-bar button')].some(b=>b.textContent.includes('Запись…'))"
    )
    fill(".set-section .set-field input", "second")
    fill(".set-comment textarea", "second note")
    key("Escape", 27)
    until("document.querySelector('.settings') === null")
    assert js("document.activeElement.getAttribute('aria-label') === 'Оператор и настройки'"), (
        "Opener focus not restored"
    )
    open_settings(group="Продвинутые")
    until("!!document.querySelector('.settings-bar .ok')")
    assert SETTINGS["TEST_VALUE"] == "first"
    assert NOTES["TEST_VALUE"] == "first note"
    assert js("document.querySelector('.set-section input').value") == "second"
    assert js("document.querySelector('.set-comment textarea').value") == "second note"
    assert not js(
        "[...document.querySelectorAll('.settings-bar button')].find(b=>b.textContent.includes('Сохранить')).disabled"
    )
    click("Сохранить")
    until("!document.querySelector('.settings-bar .ok')")
    until(
        "[...document.querySelectorAll('.settings-bar button')].find(b=>b.textContent.includes('Сохранить'))?.disabled && !!document.querySelector('.settings-bar .ok')"
    )
    assert SETTINGS["TEST_VALUE"] == "second"
    assert NOTES["TEST_VALUE"] == "second note"

    SAVE_OK = False
    fill(".set-section input", "retry value")
    click("Сохранить")
    until(
        "document.querySelector('.settings-bar .error')?.textContent.includes('Save fixture unavailable')"
    )
    assert js("document.querySelector('.set-section input').value") == "retry value"
    SAVE_OK = True
    click("Сохранить")
    until("!document.querySelector('.settings-bar .error')")
    until("!!document.querySelector('.settings-bar .ok')")
    assert SETTINGS["TEST_VALUE"] == "retry value"
    fill(".set-section input", "unsaved")
    # Незаписанный секрет не должен пережить закрытие раздела, а обычная
    # правка — должна: иначе токен лежит в памяти вкладки всю сессию просто так.
    click("Изменить")
    fill('input[type="password"]', "unsaved-secret")
    close_settings()
    open_settings(group="Продвинутые")
    until("!!document.querySelector('.set-section input')")
    assert js("document.querySelector('.set-section input').value") == "unsaved"
    assert js("document.querySelector('input[type=password]') === null"), "Secret draft survived"
    click("Сбросить правки")
    until("document.querySelector('.set-section input').value === 'retry value'")

    # Применённые настройки: значение, источник и «нужен перезапуск».
    # Секрет остаётся маской и здесь.
    until("document.querySelector('.set-applied') !== null")
    assert js("document.querySelector('.set-applied').open === true"), (
        "расхождение файла и процесса должно быть видно сразу"
    )
    assert js("document.querySelector('.set-applied-table').textContent.includes('окружение')")
    assert js("document.querySelector('.set-applied-table tr.warn').textContent.includes('LLM_MODEL')")
    assert js("document.querySelector('.set-applied-table').textContent.includes('********')")
    assert not js("document.querySelector('.set-applied-table').textContent.includes('sk-')")
    (smoke.ARTIFACTS / "settings.png").write_bytes(
        base64.b64decode(call("Page.captureScreenshot")["data"])
    )
    close_settings()

    # Результаты чата — во вкладке «Чат» правой колонки: выбор узла выше
    # переключил её на «Подробности». Список монтируется со вкладкой, и
    # первая загрузка должна пройти до того, как фикстура начнёт отказывать.
    show_chat()
    until("!!document.querySelector('.engine-outline')")
    js("document.querySelector('.engine-outline').open=true")
    until("[...document.querySelectorAll('.engine-outline button')].some(b=>b.textContent.includes('Обновить список'))")
    PUBLICATIONS_OK = False
    click("Обновить список")
    until(
        "document.querySelector('.engine-outline [role=alert]')?.textContent.includes('Publication fixture unavailable')"
    )
    assert "Пока пусто" not in js("document.querySelector('.engine-outline').textContent")
    js("document.querySelector('.engine-outline').open=false")
    assert "ошибка загрузки" in js("document.querySelector('.engine-outline summary').textContent")
    PUBLICATIONS_OK = True
    js("document.querySelector('.engine-outline').open=true")
    click("Повторить загрузку")
    until(
        "!!document.querySelector('.engine-outline .resource-list button') && !document.querySelector('.engine-outline [role=alert]')"
    )

    # Состояние связи — глобальный статус продукта, и стоит он один раз, в шапке.
    HEALTH = "offline"
    js("window.dispatchEvent(new Event('focus'))")
    until("document.querySelector('.system-status').textContent.includes('Нет сервера')")
    # Просроченный токен — это не упавший сервер: чинить надо разное, и
    # фоновая проверка не должна ни спрашивать токен, ни врать про сервер.
    HEALTH = "unauthorized"
    js("window.dispatchEvent(new Event('focus'))")
    until("document.querySelector('.system-status').textContent.includes('Нет доступа')")
    HEALTH = "ok"
    js("window.dispatchEvent(new Event('focus'))")
    until("document.querySelector('.system-status').textContent.includes('На связи')")
    js("window.dispatchEvent(new Event('offline'))")
    until("document.querySelector('.system-status').textContent.includes('Нет сервера')")
    js("window.dispatchEvent(new Event('online'))")
    until("document.querySelector('.system-status').textContent.includes('На связи')")

    # Итог прогона: три строки вместо четырёх блоков JSON. Проблема названа,
    # следующее действие сказано.
    workspace_view("Результат")
    until("document.querySelector('.run-summary') !== null")
    assert js("document.querySelector('.run-summary-outcome').textContent.includes('Документов этапов: 2')")
    assert js("document.querySelector('.run-summary-problems').textContent.includes('план изменился')")
    assert js("document.querySelector('.run-summary-problems').textContent.includes('неизвестному тарифу')")
    assert js("document.querySelector('.run-summary-next').textContent.includes('подтвердите публикацию')")
    assert js("document.querySelector('.run-summary-next').textContent.includes('тариф модели')")

    # Подтверждение публикации: назначение, судьба страницы и различия.
    # «Перезапишет существующую» без них — предупреждение без содержания.
    APPROVAL = True
    js("window.dispatchEvent(new Event('focus'))")
    call("Page.reload")
    until("document.querySelector('.approve') !== null")
    assert js("document.querySelector('.draft-action-update').textContent.includes('перезаписать')")
    assert js("document.querySelector('.draft-fields').textContent.includes('/data/published/orbita-01.md')")
    assert js("document.querySelector('.draft-diff > summary').textContent.includes('+2')")
    assert js("document.querySelector('.draft-diff > summary').textContent.includes('строк')")
    # Строки различий раскрываются по запросу, а не вываливаются сразу.
    assert js("document.querySelector('.draft-diff').open === false")
    js("document.querySelector('.draft-diff').open = true")
    until("document.querySelector('.draft-diff-body') !== null")
    assert js("document.querySelector('.draft-diff-body').textContent.includes('+новая строка')")
    # Окно остаётся модальным и доступным с клавиатуры.
    assert js("document.querySelector('.approve').getAttribute('role') === 'dialog'")
    # Кнопки решения доступны с клавиатуры: фокус ставится и остаётся.
    js("document.querySelector('.btn-yes').focus()")
    assert js("document.activeElement.classList.contains('btn-yes')")
    assert js("document.querySelector('.btn-yes').disabled === false")
    APPROVAL = False
    # Роль оператора приедет позже, чем откроется меню профиля.
    ME_RELEASED.clear()
    call("Page.reload")
    until("document.querySelector('.approve') === null")

    # Меню профиля: по пунктам ходит настоящий фокус, и Enter достаётся
    # сфокусированной кнопке. Раньше моделей фокуса было две — пункты стояли
    # в порядке обхода Tab, а Enter на контейнере выполнял пункт по
    # внутреннему счётчику, который Tab не двигал: фокус стоял на
    # «Оформлении», открывалась «Модель».
    # Страница только что перезагружена: ждём саму шапку, а не её меню.
    until("!!document.querySelector('.menu-avatar .menu-trigger')", seconds=30)
    js("document.querySelector('.menu-avatar .menu-trigger').click()")
    until("!!document.querySelector('.menu-list .menu-item')")
    # Текст сфокусированного пункта. Не `activeElement.textContent`: у
    # страницы, на которую падает потерянный фокус, в тексте есть всё меню.
    item = (
        "(document.activeElement.classList.contains('menu-item')"
        " ? document.activeElement.textContent : '')"
    )
    # Меню открыто до того, как сервер назвал роль: первым в нём стоит пункт
    # не для служебного входа. С ответом он исчезает, а на его место встаёт
    # пункт администратора. Раньше фокус уходил вместе с исчезнувшим пунктом
    # на страницу, и стрелки листали её, а не меню.
    assert js(f"{item}.includes('Мои подключения')"), (
        "Menu did not take focus on open"
    )
    ME_RELEASED.set()
    until(
        "[...document.querySelectorAll('.menu-list .menu-item')]"
        ".some(b=>b.textContent.includes('Настройки сервера'))",
        seconds=5,
    )
    assert js(f"{item}.includes('Настройки сервера')"), (
        "Menu lost focus when its items changed: "
        + str(js("document.activeElement.tagName + '.' + document.activeElement.className"))
    )
    key("ArrowDown", 40)
    assert js(f"{item}.includes('Оформление')"), (
        "Arrow key did not move the real focus"
    )
    # Подсветка и фокус — одно и то же, а не два независимых состояния.
    # Подсветка идёт за фокусом через перерисовку, поэтому ждём, а не
    # проверяем в тот же миг: иначе проверка ловит кадр между ними.
    until("document.querySelector('.menu-item[data-active=true]') === document.activeElement")
    key("Enter", 13, text="\r")
    # Страница появляется раньше, чем эффект SettingsPage применит выбранный
    # раздел: первый кадр ещё показывает «Модель». Ждём сам переход в
    # «Оформление»; неверный пункт меню по-прежнему завершит проверку ошибкой.
    until(
        "document.querySelector('.settings-nav-item[aria-current=page]')?.textContent.includes('Оформление')"
    )
    close_settings()

    # В порядке обхода стоит ровно один пункт: Tab уводит фокус из меню и
    # закрывает его, а не идёт по списку мимо подсветки.
    js("document.querySelector('.menu-avatar .menu-trigger').click()")
    until("!!document.querySelector('.menu-list .menu-item')")
    assert js("document.querySelectorAll('.menu-item:not([tabindex=\"-1\"])').length") == 1
    key("Tab", 9, raw=True)
    until("document.querySelector('.menu-list') === null")

    # Отказ запуска не уносит с собой неотправленный текст задачи: пока сервер
    # не принял ход, поле принадлежит оператору.
    global REJECT_RUN
    REJECT_RUN = True
    draft = "AUDIT IMPORTANT UNSENT DRAFT"
    composer = ".task-composer textarea"
    # The header/menu can mount before the asynchronously loaded workspace.
    # Wait for the actual input, not merely for the menu to close.
    until(f"!!document.querySelector({json.dumps(composer)})", seconds=30)
    fill(composer, draft)
    until("!document.querySelector('.composer-submit').disabled")
    js("document.querySelector('.composer-submit').click()")
    until("!!document.querySelector('.app-alerts .error')", seconds=15)
    # Ход не состоялся — текст возвращается в поле и снова принадлежит оператору.
    until(f"document.querySelector({json.dumps(composer)}).value === {json.dumps(draft)}")
    assert js("!document.querySelector('.composer-submit').disabled"), "Submit stayed locked after refusal"
    REJECT_RUN = False
    fill(composer, "")

    # Файлы чата. У чата с разговором они свёрнуты в строку, и счётчик стоит
    # в ней — «…», пока список не приехал.
    global CREATE_DELAY
    show_chat()
    count = "document.querySelector('.chat-panel-toggle .hint')?.textContent"
    active = "document.querySelector('.chat-item.active .chat-item-title')?.textContent"
    graph = "document.querySelector('.pick-button').dataset.graph"

    def chat(title):
        return (
            "[...document.querySelectorAll('.chat-item-open')]"
            f".find(b=>b.textContent.includes({json.dumps(title)}))"
        )

    def pick_scenario(graph_id):
        js("document.querySelector('.pick-button').click()")
        until(f"!!document.querySelector('.pick-menu [data-graph={graph_id}]')")
        js(f"document.querySelector('.pick-menu [data-graph={graph_id}]').click()")
        until(f"{graph} === {json.dumps(graph_id)} && !document.querySelector('.pick-menu')")

    saved = chat("Сохранённый чат")
    until(f"{active} === 'Сохранённый чат' && {count} === '0'")

    # Повторный клик по открытому чату очищал его список, а перечитывался
    # список по смене треда, которой не было: «…» оставалось навсегда.
    js(f"{saved}.click()")
    time.sleep(0.5)
    assert js(count) == "0", f"Файлы открытого чата пропали: {js(count)!r}"

    # Список чатов один на все сценарии: у каждого чата подписан его
    # сценарий, и фильтра по сценарию в запросе нет.
    titles = "[...document.querySelectorAll('button.chat-item-open .chat-item-title')].map(t=>t.textContent)"
    assert {"Сохранённый чат", "Чат demo", "Файлы без запроса"} <= set(js(titles)), js(titles)
    assert all(item is None for item in SEARCHES), SEARCHES
    assert js("document.querySelector('.chat-item[data-graph=demo] .chat-item-scenario')?.textContent") == "demo"

    # Чат другого сценария открывается вместе со своим сценарием: у треда
    # состояние своего графа, и чужая схема прочитала бы его как своё.
    js(f"{chat('Чат demo')}.click()")
    until(f"{graph} === 'demo' && {active} === 'Чат demo'")
    until("document.querySelectorAll('.chat-panel .msg').length > 0")
    assert "demo-thread" in js("localStorage.getItem('orbita.chat')")
    js(f"{saved}.click()")
    until(f"{graph} === 'agent' && {active} === 'Сохранённый чат'")

    # Чат, в который положили файлы и ни о чём не спросили, уходит в
    # выбранный сценарий вместе с файлами: сценарий у него ещё не выбран.
    js(f"{chat('Файлы без запроса')}.click()")
    until(f"{graph} === 'demo' && {active} === 'Файлы без запроса'")
    pick_scenario("agent")
    until(f"{active} === 'Файлы без запроса'")
    deadline = time.monotonic() + 5
    while not PATCHES and time.monotonic() < deadline:
        time.sleep(0.05)
    assert PATCHES == [("files-thread", {"metadata": {"graph_id": "agent"}})], PATCHES
    assert not js("!!document.querySelector('.chat-item.draft')"), "Чат без запросов не перешёл в сценарий"

    # Чат с разговором остаётся в своём сценарии: в новом открывается черновик.
    js(f"{saved}.click()")
    until(f"{active} === 'Сохранённый чат'")
    pick_scenario("demo")
    until("!!document.querySelector('.chat-item.active.draft')")
    assert len(PATCHES) == 1, PATCHES
    assert "Сохранённый чат" in js(titles)
    js(f"{saved}.click()")
    until(f"{graph} === 'agent' && {active} === 'Сохранённый чат'")

    # Файл брошен в черновик, и пока сервер заводит под него чат, оператор
    # уходит в другой. Поздний ответ не должен переключать его обратно.
    CREATE_DELAY = 1.5
    js("document.querySelector('.new-chat').click()")
    until("!!document.querySelector('.chat-item.active.draft')")
    # Схема сценария могла ещё не доехать: поле файлов появляется вместе с ней.
    until("!!document.querySelector('.chat-files input[type=file]')")
    js("""(()=>{const input=document.querySelector('.chat-files input[type=file]');
        const data=new DataTransfer();
        data.items.add(new File(['# note'],'note.md',{type:'text/markdown'}));
        input.files=data.files;
        input.dispatchEvent(new Event('change',{bubbles:true}));})()""")
    deadline = time.monotonic() + 5
    while "/threads" not in smoke.REQUESTS and time.monotonic() < deadline:
        time.sleep(0.05)
    assert "/threads" in smoke.REQUESTS, "Загрузка в черновик не завела чат"
    until(f"!!{saved}")
    js(f"{saved}.click()")
    until(f"{active} === 'Сохранённый чат'")
    deadline = time.monotonic() + 5
    while "late-chat" not in UPLOADS and time.monotonic() < deadline:
        time.sleep(0.05)
    assert UPLOADS == ["late-chat"], UPLOADS
    time.sleep(0.5)
    assert js(active) == "Сохранённый чат", f"Поздний ответ переключил чат: {js(active)!r}"
    assert js(count) == "0", f"На экране чужие файлы: {js(count)!r}"

    # То же, но оператор уходит в другой сценарий: черновик уезжает туда
    # черновиком, а поздний ответ не возвращает его в прежний.
    created = smoke.REQUESTS.count("/threads")
    js("document.querySelector('.new-chat').click()")
    until("!!document.querySelector('.chat-item.active.draft')")
    # Схема сценария могла ещё не доехать: поле файлов появляется вместе с ней.
    until("!!document.querySelector('.chat-files input[type=file]')")
    js("""(()=>{const input=document.querySelector('.chat-files input[type=file]');
        const data=new DataTransfer();
        data.items.add(new File(['# note'],'note.md',{type:'text/markdown'}));
        input.files=data.files;
        input.dispatchEvent(new Event('change',{bubbles:true}));})()""")
    deadline = time.monotonic() + 5
    while smoke.REQUESTS.count("/threads") == created and time.monotonic() < deadline:
        time.sleep(0.05)
    assert smoke.REQUESTS.count("/threads") > created, "Загрузка в черновик не завела чат"
    pick_scenario("demo")
    until("!!document.querySelector('.chat-item.active.draft')")
    deadline = time.monotonic() + 5
    while len(UPLOADS) < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert UPLOADS == ["late-chat", "late-chat"], UPLOADS
    time.sleep(0.5)
    assert js(graph) == "demo", f"Поздний ответ вернул сценарий: {js(graph)!r}"
    assert js("!!document.querySelector('.chat-item.active.draft')"), "Поздний ответ открыл чужой чат"
    CREATE_DELAY = 0.0

    # Сохранённые размеры — пожелание, а не приказ. Две колонки по 640,
    # растянутые при ширине 1920, после уменьшения окна до 1440 оставляли
    # схеме 112 пикселей. Поле задачи, растянутое вверх, в низком окне
    # сжимало схему до 2 пикселей и уводило кнопку отправки за край, и
    # перезагрузка возвращала то же самое: предел считался только ручкой.
    def viewport(width, height):
        call(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
        )

    viewport(1920, 900)
    js(
        "localStorage.setItem('orbita.leftWidth','640');"
        "localStorage.setItem('orbita.rightWidth','640');"
        "localStorage.setItem('orbita.composer.height','700')"
    )
    call("Page.reload")
    until("!!document.querySelector('.task-composer textarea')", seconds=30)
    if not js("!!document.querySelector('.inspector')"):
        js("document.querySelector('[aria-label=\"Колонка чата и подробностей\"]').click()")
    until("!!document.querySelector('.inspector')")
    viewport(1440, 900)
    until("document.querySelector('.app-main').getBoundingClientRect().width >= 379")
    fits = (
        "document.querySelector('.workspace').getBoundingClientRect().height >= 219"
        " && document.querySelector('.composer-submit').getBoundingClientRect().bottom"
        " <= document.querySelector('.app-body').getBoundingClientRect().bottom"
    )
    viewport(1440, 640)
    until(fits)
    # Выбранная высота не забыта: места снова стало больше — поле снова выше.
    squeezed = js("document.querySelector('.task-composer textarea').getBoundingClientRect().height")
    viewport(1440, 900)
    until(
        "document.querySelector('.task-composer textarea').getBoundingClientRect().height"
        f" > {squeezed}"
    )
    viewport(1440, 640)
    call("Page.reload")
    until("!!document.querySelector('.task-composer textarea')", seconds=30)
    until(fits)
    js(
        "['orbita.leftWidth','orbita.rightWidth','orbita.composer.height']"
        ".forEach((key) => localStorage.removeItem(key))"
    )
    viewport(1366, 768)

    (smoke.ARTIFACTS / "regressions.png").write_bytes(
        base64.b64decode(call("Page.captureScreenshot")["data"])
    )
    return [
        "Markdown structure and unsafe content",
        "array drafts and invalid submission",
        "graph Enter/Space and node inspector",
        "settings page: focus return and Escape",
        "settings save race and close/reopen",
        "settings save failure/retry",
        "secret draft dropped on close",
        "publication failure/retry",
        "server disconnect/recovery and expired token",
        "applied settings: value, source and restart",
        "run summary: outcome, problems, next action",
        "publish approval: destination, create/update and diff",
        "parameter form does not start a run",
        "menu keyboard: Enter runs the focused item",
        "refused run keeps the unsent task",
        "active chat click keeps its files",
        "one chat list for all scenarios",
        "another scenario's chat opens with its scenario",
        "chat without a request moves to the chosen scenario",
        "chat with a run stays, new scenario opens a draft",
        "late chat creation does not switch the chat",
        "late chat creation does not pull the operator back",
        "saved column and task field sizes yield to a smaller window",
    ]


if __name__ == "__main__":
    smoke.Handler = Handler
    smoke.manifest = manifest
    smoke.main(extra_checks=regressions)
