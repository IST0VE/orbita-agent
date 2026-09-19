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


REJECT_RUN = False


class Handler(smoke.Handler):
    def do_POST(self):
        # Отказ уже после успешного preflight: запуск не состоялся, хотя
        # интерфейс к этому моменту успел показать «выполняется».
        if REJECT_RUN and self.path.endswith("/runs/stream"):
            smoke.REQUESTS.append(self.path)
            self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)
            return self.reply({"error": "Fixture run rejected"}, 409)
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


def regressions(call, js, until, click, shell):
    global HEALTH, PUBLICATIONS_OK, SAVE_OK, APPROVAL

    open_settings = shell["open_settings"]
    close_settings = shell["close_settings"]
    workspace_view = shell["workspace_view"]

    def fill(selector, value):
        js(f"""(()=>{{const e=document.querySelector({json.dumps(selector)});
            Object.getOwnPropertyDescriptor(e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value').set.call(e,{json.dumps(value)});
            e.dispatchEvent(new Event('input',{{bubbles:true}}));}})()""")

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
    js("localStorage.setItem('orbita.graph','agent')")
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
    # прогона, и стоит он среди параметров, а не в поле задачи.
    until("!!document.querySelector('.sidebar [data-widget=form]')")
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

    PUBLICATIONS_OK = False
    js("document.querySelector('.engine-outline').open=true")
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
    assert js("document.activeElement.textContent.includes('Настройки приложения')"), (
        "Menu did not take focus on open"
    )
    key("ArrowDown", 40)
    assert js("document.activeElement.textContent.includes('Оформление')"), (
        "Arrow key did not move the real focus"
    )
    # Подсветка и фокус — одно и то же, а не два независимых состояния.
    assert js(
        "document.querySelector('.menu-item[data-active=true]') === document.activeElement"
    ), "Highlight and focus disagree"
    key("Enter", 13, text="\r")
    until("!!document.querySelector('.settings')")
    assert js(
        "document.querySelector('.settings-nav-item[aria-current=page]').textContent.includes('Оформление')"
    ), "Enter executed a different menu item than the focused one"
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
    fill(composer, draft)
    until("!document.querySelector('.composer-submit').disabled")
    js("document.querySelector('.composer-submit').click()")
    until("!!document.querySelector('.app-alerts .error')", seconds=15)
    # Ход не состоялся — текст возвращается в поле и снова принадлежит оператору.
    until(f"document.querySelector({json.dumps(composer)}).value === {json.dumps(draft)}")
    assert js("!document.querySelector('.composer-submit').disabled"), "Submit stayed locked after refusal"
    REJECT_RUN = False
    fill(composer, "")

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
    ]


if __name__ == "__main__":
    smoke.Handler = Handler
    smoke.manifest = manifest
    smoke.main(extra_checks=regressions)
