"""Скриншоты портала Orbita для руководств: сценарии аналитики и настройки.

Снимки в `docs/assets/screenshots/` делаются с того же интерфейса, в котором
работает оператор, и по настоящим прогонам: скрипт выбирает материалы, пишет
задачу, ждёт этапы и подтверждения. Поэтому кадры соответствуют текущему
интерфейсу и коду, а не памяти о них.

Перед запуском нужны три вещи:

    cd ../nt-testbed && docker compose up -d      # моки Jira и Confluence
    python scripts/serve_nt_testbed.py            # backend на координатах стенда
    npm --prefix web run dev                      # портал

Стендовый backend выбран намеренно: публикация у него идёт в файл, Jira и
Confluence — моки, так что прогон ради картинки ничего не создаёт в настоящих
системах. Затем:

    python scripts/shoot_portal.py                        # все сцены
    python scripts/shoot_portal.py agent drawio           # выбранные
    python scripts/shoot_portal.py --out .tmp/shots --headed

Сцены `agent`, `drawio`, `jira` и `update` — это вызовы модели и записи в
PUBLISH_DIR; `start`, `scenarios`, `inputs` и `settings` модель не вызывают.
Настройки лучше снимать с backend на обычном `.env` (`langgraph dev`):
стендовый честно предупреждает, что файл разошёлся с работающим процессом, и
это предупреждение попадёт в кадр.

    python scripts/shoot_portal.py settings --env .env
"""

import argparse
from pathlib import Path

from portal_shots import (
    ROOT,
    SCROLL_CONSOLE_TO_END,
    SCROLL_TO_TEXT,
    TESTBED_ENV,
    approve_all,
    center_on,
    console_tab,
    folder,
    launch,
    open_portal,
    pick,
    playwright,
    run_task,
    shoot,
    view,
    zoom_out,
)

# Прогон конвейера по демо-шлюзу модели занимает до получаса: пять ролей по
# несколько тысяч токенов ответа, а шлюз пускает 30 тысяч за окно около десяти
# минут, и клиент честно ждёт следующего. Ожидание с запасом дешевле, чем
# оборванный на середине прогон.
STEP_MS = 3_600_000

CONSOLE_HEIGHT = 250

RUNNING = ".graph-node.node-running"
PASSED = ".graph-node.node-completed"

AGENT_TASK = (
    "Собери системные требования и проект решения по частичному возврату заказа. "
    "Укажи противоречия в источниках и открытые вопросы."
)
# Пример выбора для руководства по материалам: встреча и более позднее письмо —
# ровно те два источника, что противоречат друг другу.
AGENT_PICKED = ("meeting-2026-08-18.md", "mail-finance.md")

DRAWIO_TASK = (
    "Опиши систему по выбранной схеме. Перечисли компоненты, границы и интеграции, "
    "восстанови основной поток. Отдели факты со схемы от предположений "
    "и заверши списком вопросов, на которые схема не отвечает."
)

JIRA_TASK = (
    "Разложи выбранный пакет аналитики на задачи команды. "
    "Выдели Epic, задачи по сервисам и слоям, зависимости и критерии приёмки. "
    "Нерешённые вопросы сохрани явно; не придумывай согласованные оценки."
)
# Проект мока Jira на стенде: частичный возврат — это заказы.
JIRA_PROJECT = "ORD"

UPDATE_TASK = (
    "Обнови основной документ по новым материалам о частичном возврате. "
    "Меняй только подтверждённые фрагменты. Для каждой правки укажи основание. "
    "Если решения противоречат друг другу или данных недостаточно, "
    "оставь открытый вопрос вместо придуманного контракта."
)


def open_published(page, needle: str) -> None:
    """Открыть свежий опубликованный документ, в заголовке которого есть `needle`.

    Список идёт свежими вверх, поэтому первый совпавший — документ этого
    прогона, а не прошлых.
    """
    outline = page.locator("details.engine-outline").first
    if outline.get_attribute("open") is None:
        outline.locator("summary").first.click()
    button = outline.locator(".resource-list button", has_text=needle).first
    button.wait_for(state="visible", timeout=60_000)
    button.click()
    page.locator(".document-view .engine-document").first.wait_for(timeout=60_000)
    page.wait_for_timeout(800)


def scroll_document(page, *needles: str) -> None:
    for needle in needles:
        if page.evaluate(SCROLL_TO_TEXT, needle):
            page.wait_for_timeout(500)
            return


def scene_start(browser, args) -> None:
    """Первое открытие: ничего не выбрано, прогона не было."""
    context, page = open_portal(browser, args.url, "agent", env_file=args.env,
                                published_open=False)
    shoot(page, args.out, "start.png")
    context.close()


def scene_inputs(browser, args) -> None:
    """Папка и отдельно отмеченные в ней документы — до запуска."""
    context, page = open_portal(browser, args.url, "agent", env_file=args.env,
                                published_open=False)
    folder(page, "partial-refund")
    for name in AGENT_PICKED:
        pick(page, name)
    shoot(page, args.out, "inputs.png")
    context.close()


def scene_scenarios(browser, args) -> None:
    context, page = open_portal(browser, args.url, "agent", env_file=args.env,
                                published_open=False)
    page.locator(".app-header button", has_text="Архитектурная аналитика").first.click()
    page.wait_for_timeout(700)
    shoot(page, args.out, "scenarios.png")
    context.close()


def scene_settings(browser, args) -> None:
    context, page = open_portal(browser, args.url, "agent", env_file=args.env,
                                published_open=False)
    page.locator(".avatar").first.click()
    page.get_by_text("Настройки приложения").first.click()
    page.get_by_text("Состояние подключения").first.wait_for(timeout=30_000)
    page.wait_for_timeout(2500)
    shoot(page, args.out, "settings.png")
    context.close()


def scene_agent(browser, args) -> None:
    context, page = open_portal(browser, args.url, "agent", env_file=args.env,
                                published_open=False, console_height=CONSOLE_HEIGHT,
                                thread=args.thread)
    if args.thread:
        # Досъёмка: прогон уже идёт или стоит на подтверждении, кадр хода
        # снят раньше.
        zoom_out(page, 2)
    else:
        # Аналитика читает папку целиком — так её и советует запускать
        # руководство.
        folder(page, "partial-refund")
        run_task(page, AGENT_TASK)

        # Кадр прогона — когда первая роль уже выпустила документ, а следующая
        # работает: видно и ход по графу, и что появилось в результатах.
        page.locator(".artifact-list details").first.wait_for(timeout=STEP_MS)
        page.wait_for_timeout(3000)
        zoom_out(page, 2)
        if not center_on(page, RUNNING):
            center_on(page, PASSED)
        console_tab(page, "События")
        page.evaluate(SCROLL_CONSOLE_TO_END)
        shoot(page, args.out, "run.png")

    approve_all(page, lambda p: shoot(p, args.out, "approval.png"), timeout_ms=STEP_MS,
                thread=args.thread)

    # Итог: граф пройден, в колонках — документы, стоимость и публикация.
    view(page, "Схема")
    center_on(page, PASSED)
    console_tab(page, "События")
    page.evaluate(SCROLL_CONSOLE_TO_END)
    shoot(page, args.out, "workspace.png")

    open_published(page, "Системные требования")
    scroll_document(page, "Открытые вопросы", "Противоречия")
    shoot(page, args.out, "document.png")
    context.close()


def scene_drawio(browser, args) -> None:
    context, page = open_portal(browser, args.url, "drawio", env_file=args.env,
                                published_open=True, console_height=CONSOLE_HEIGHT)
    folder(page, "diagram")
    pick(page, "orders-export.drawio")
    run_task(page, DRAWIO_TASK)
    approve_all(page, timeout_ms=STEP_MS)
    open_published(page, "Страница документации")
    shoot(page, args.out, "diagram.png")
    context.close()


def scene_jira(browser, args) -> None:
    context, page = open_portal(browser, args.url, "jira", env_file=args.env,
                                published_open=False, console_height=CONSOLE_HEIGHT)
    folder(page, "partial-refund-package")
    project = page.get_by_label("Проект Jira").first
    project.fill(JIRA_PROJECT)
    run_task(page, JIRA_TASK)

    def card(p) -> None:
        # Снимается остановка перед заведением задач: в ней поле проекта.
        # Остальные — публикация документов — просто подтверждаются.
        dialog = p.locator(".approve").first
        if dialog.get_by_text("Проект Jira").count():
            field = dialog.locator("input[list='approve-projects']").first
            if not field.input_value():
                field.fill(JIRA_PROJECT)
            shoot(p, args.out, "jira.png")

    approve_all(page, card, timeout_ms=STEP_MS)
    context.close()


def scene_update(browser, args) -> None:
    context, page = open_portal(browser, args.url, "update", env_file=args.env,
                                published_open=False, console_height=CONSOLE_HEIGHT)
    folder(page, "partial-refund", picker=0)
    pick(page, "current-api.md", picker=0)
    folder(page, "partial-refund", picker=1)
    for name in ("meeting-2026-08-18.md", "mail-finance.md"):
        pick(page, name, picker=1)
    run_task(page, UPDATE_TASK)
    approve_all(page, lambda p: shoot(p, args.out, "update.png"), timeout_ms=STEP_MS)
    context.close()


SCENES = {
    "start": scene_start,
    "scenarios": scene_scenarios,
    "inputs": scene_inputs,
    "settings": scene_settings,
    "agent": scene_agent,
    "drawio": scene_drawio,
    "jira": scene_jira,
    "update": scene_update,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenes", nargs="*", metavar="scene",
                        help=f"какие сцены снять: {', '.join(SCENES)}; по умолчанию все")
    parser.add_argument("--url", default="http://localhost:5173")
    parser.add_argument("--out", default=str(ROOT / "docs" / "assets" / "screenshots"))
    parser.add_argument("--env", default=str(TESTBED_ENV),
                        help="файл с API_ADMIN_TOKEN того backend, с которым идёт съёмка")
    parser.add_argument("--thread", help="сцена agent: доснять подтверждение и итог по "
                        "уже запущенному треду, без нового прогона")
    parser.add_argument("--headed", action="store_true", help="показать браузер")
    args = parser.parse_args()
    if unknown := [name for name in args.scenes if name not in SCENES]:
        parser.error(f"нет сцен: {', '.join(unknown)}")
    args.out = Path(args.out).resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    args.env = Path(args.env).resolve()

    with playwright() as driver:
        browser = launch(driver, headed=args.headed)
        for name in args.scenes or SCENES:
            print(f"сцена: {name}", flush=True)
            SCENES[name](browser, args)
        browser.close()


if __name__ == "__main__":
    main()
