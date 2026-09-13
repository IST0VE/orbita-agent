"""Run browser_smoke plus frontend audit regressions against in-memory fixtures.

After npm run build: .venv/Scripts/python.exe web/tests/browser_regressions.py
No live backend, model calls, credentials or real settings writes.
"""

import base64
import json
import time

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


class Handler(smoke.Handler):
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
        if self.path == "/api/settings":
            return self.reply(
                {
                    "path": "fixture.env",
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
        if self.path.startswith("/api/ui/resources/orbita.publications?"):
            if not PUBLICATIONS_OK:
                return self.reply({"error": "Publication fixture unavailable"}, 503)
            return self.reply(
                {"documents": [{"name": "report.md", "title": "Test report", "size": 42}]}
            )
        return super().do_GET()

    def do_PUT(self):
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


def regressions(call, js, until, click):
    global HEALTH, PUBLICATIONS_OK, SAVE_OK, APPROVAL

    def fill(selector, value):
        js(f"""(()=>{{const e=document.querySelector({json.dumps(selector)});
            Object.getOwnPropertyDescriptor(e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value').set.call(e,{json.dumps(value)});
            e.dispatchEvent(new Event('input',{{bubbles:true}}));}})()""")

    def key(name, code, shift=False):
        for kind in ["keyDown", "keyUp"]:
            call(
                "Input.dispatchKeyEvent",
                {
                    "type": kind,
                    "key": name,
                    "code": name,
                    "windowsVirtualKeyCode": code,
                    "modifiers": 8 if shift else 0,
                },
            )

    call(
        "Emulation.setDeviceMetricsOverride",
        {"width": 1366, "height": 768, "deviceScaleFactor": 1, "mobile": False},
    )
    js("localStorage.setItem('orbita.graph','agent')")
    call("Page.reload")
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
        assert js("document.querySelector('.app').scrollWidth <= innerWidth + 1"), (
            f"Markdown overflow at {width}"
        )

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

    js(
        "[...document.querySelectorAll('.canvas-tools button')].find(b=>b.textContent==='[список]').click()"
    )
    until("!!document.querySelector('.graph-list tbody tr')")
    js("document.querySelector('.graph-list tbody tr').focus()")
    key("Enter", 13)
    until("!!document.querySelector('.node-details')")
    click("закрыть")
    js("document.querySelector('.graph-list tbody tr').focus()")
    key(" ", 32)
    until("!!document.querySelector('.node-details')")
    click("закрыть")

    js(
        "[...document.querySelectorAll('button')].find(b=>b.textContent.includes('настройки')).focus()"
    )
    click("настройки")
    until("!!document.querySelector('.set-section input')")
    assert js("document.querySelector('dialog').matches(':modal')")
    js(
        "[...document.querySelectorAll('button')].find(b=>b.textContent.includes('новый диалог')).focus()"
    )
    assert js("document.querySelector('dialog').contains(document.activeElement)"), (
        "Background can take focus"
    )
    js("[...document.querySelectorAll('dialog button')].at(-1).focus()")
    key("Tab", 9)
    assert js("document.querySelector('dialog').contains(document.activeElement)")
    key("Tab", 9, shift=True)
    assert js("document.querySelector('dialog').contains(document.activeElement)")

    fill(".set-section .set-field input", "first")
    click("править комментарий")
    fill(".set-comment textarea", "first note")
    click("сохранить")
    until(
        "[...document.querySelectorAll('dialog button')].some(b=>b.textContent.includes('запись…'))"
    )
    fill(".set-section .set-field input", "second")
    fill(".set-comment textarea", "second note")
    key("Escape", 27)
    until("document.querySelector('dialog') === null")
    assert js("document.activeElement.textContent.includes('настройки')"), (
        "Opener focus not restored"
    )
    click("настройки")
    until("!!document.querySelector('.panel-foot .ok')")
    assert SETTINGS["TEST_VALUE"] == "first"
    assert NOTES["TEST_VALUE"] == "first note"
    assert js("document.querySelector('.set-section input').value") == "second"
    assert js("document.querySelector('.set-comment textarea').value") == "second note"
    assert not js(
        "[...document.querySelectorAll('dialog button')].find(b=>b.textContent.includes('сохранить')).disabled"
    )
    click("сохранить")
    until("!document.querySelector('.panel-foot .ok')")
    until(
        "[...document.querySelectorAll('dialog button')].find(b=>b.textContent.includes('сохранить'))?.disabled && !!document.querySelector('.panel-foot .ok')"
    )
    assert SETTINGS["TEST_VALUE"] == "second"
    assert NOTES["TEST_VALUE"] == "second note"

    SAVE_OK = False
    fill(".set-section input", "retry value")
    click("сохранить")
    until(
        "document.querySelector('.panel-foot .error')?.textContent.includes('Save fixture unavailable')"
    )
    assert js("document.querySelector('.set-section input').value") == "retry value"
    SAVE_OK = True
    click("сохранить")
    until("!document.querySelector('.panel-foot .error')")
    until("!!document.querySelector('.panel-foot .ok')")
    assert SETTINGS["TEST_VALUE"] == "retry value"
    fill(".set-section input", "unsaved")
    # Незаписанный секрет не должен пережить закрытие окна, а обычная правка —
    # должна: иначе токен лежит в памяти вкладки всю сессию просто так.
    click("изменить")
    fill('input[type="password"]', "unsaved-secret")
    click("закрыть")
    click("настройки")
    until("!!document.querySelector('.set-section input')")
    assert js("document.querySelector('.set-section input').value") == "unsaved"
    assert js("document.querySelector('input[type=password]') === null"), "Secret draft survived"
    click("сбросить правки")
    until("document.querySelector('.set-section input').value === 'retry value'")
    (smoke.ARTIFACTS / "settings.png").write_bytes(
        base64.b64decode(call("Page.captureScreenshot")["data"])
    )
    click("закрыть")

    PUBLICATIONS_OK = False
    js("document.querySelector('.engine-outline').open=true")
    click("обновить список")
    until(
        "document.querySelector('.engine-outline [role=alert]')?.textContent.includes('Publication fixture unavailable')"
    )
    assert "пока пусто" not in js("document.querySelector('.engine-outline').textContent")
    js("document.querySelector('.engine-outline').open=false")
    assert "ошибка загрузки" in js("document.querySelector('.engine-outline summary').textContent")
    PUBLICATIONS_OK = True
    js("document.querySelector('.engine-outline').open=true")
    click("повторить загрузку")
    until(
        "!!document.querySelector('.engine-outline .resource-list button') && !document.querySelector('.engine-outline [role=alert]')"
    )

    HEALTH = "offline"
    js("window.dispatchEvent(new Event('focus'))")
    until("document.querySelector('.status').textContent.includes('нет сервера')")
    # Просроченный токен — это не упавший сервер: чинить надо разное, и
    # фоновая проверка не должна ни спрашивать токен, ни врать про сервер.
    HEALTH = "unauthorized"
    js("window.dispatchEvent(new Event('focus'))")
    until("document.querySelector('.status').textContent.includes('нет доступа')")
    HEALTH = "ok"
    js("window.dispatchEvent(new Event('focus'))")
    until("document.querySelector('.status').textContent.includes('на связи')")
    js("window.dispatchEvent(new Event('offline'))")
    until("document.querySelector('.status').textContent.includes('нет сервера')")
    js("window.dispatchEvent(new Event('online'))")
    until("document.querySelector('.status').textContent.includes('на связи')")
    # Итог прогона: три строки вместо четырёх блоков JSON. Проблема названа,
    # следующее действие сказано.
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
    call("Page.reload")
    until("document.querySelector('.approve') === null")

    (smoke.ARTIFACTS / "regressions.png").write_bytes(
        base64.b64decode(call("Page.captureScreenshot")["data"])
    )
    return [
        "Markdown structure and unsafe content",
        "array drafts and invalid submission",
        "graph Enter/Space",
        "native modal focus and Escape",
        "settings save race and close/reopen",
        "settings save failure/retry",
        "secret draft dropped on close",
        "publication failure/retry",
        "server disconnect/recovery and expired token",
        "run summary: outcome, problems, next action",
        "publish approval: destination, create/update and diff",
    ]


if __name__ == "__main__":
    smoke.Handler = Handler
    smoke.manifest = manifest
    smoke.main(extra_checks=regressions)
