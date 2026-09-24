"""Скриншоты портала Orbita на прогоне графа nt по стенду nt-testbed.

Снимки для docs/NT.md делаются не с терминала, а с того же интерфейса, в котором
работает оператор: прогон, журнал выполнения, подтверждение публикации и готовый
отчёт. Скрипт прогоняет анализ целиком, поэтому картинки всегда соответствуют
текущему коду, а не старому прогону.

Перед запуском нужны три вещи:

    cd ../nt-testbed && docker compose up -d      # стенд
    python scripts/serve_nt_testbed.py            # backend на координатах стенда
    npm --prefix web run dev                      # портал

Затем:

    python scripts/shoot_nt_portal.py
    python scripts/shoot_nt_portal.py --headed --out docs/image/NT

Прогон стоит несколько вызовов модели и заканчивается записью отчёта в
PUBLISH_DIR: публикация в стенде настроена файлом, мок-wiki не трогается.
"""

import argparse
from pathlib import Path

from dotenv import dotenv_values
from portal_shots import (
    ROOT,
    SCROLL_CONSOLE_TO_END,
    SCROLL_TO_TEXT,
    TESTBED_ENV,
    approve_all,
    console_tab,
    launch,
    open_portal,
    playwright,
    run_task,
    shoot,
    shoot_box,
    shown,
    view,
)

TASK = "Проведи анализ НТ по NT-123.\ntest_id=nt-run-2291\nprevious_test_id=nt-run-2187"

# Сам прогон по стенду — одна-три минуты, но на демо-шлюзе модели каждый ход
# может ждать окна квоты. Ожидание с запасом дешевле, чем оборванный на
# середине прогон.
STEP_MS = 3_600_000


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:5173")
    parser.add_argument("--out", default=str(ROOT / "docs" / "image" / "NT"))
    parser.add_argument("--report", default=str(ROOT / "docs" / "examples" / "nt-run-2291.md"))
    parser.add_argument("--thread", help="доснять подтверждения, отчёт и журнал по уже "
                        "запущенному треду, без нового прогона")
    parser.add_argument("--headed", action="store_true", help="показать браузер")
    args = parser.parse_args()

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    with playwright() as driver:
        browser = launch(driver, headed=args.headed)
        context, page = open_portal(browser, args.url, "nt", console_height=250,
                                    thread=args.thread)
        if not args.thread:
            run_task(page, TASK)

            # Первый снимок — когда код уже посчитал вердикт и ranking, а модель
            # ещё разбирает причины: на нём видно, что чем занято. Итоги живут
            # во вкладке «Результат», и ждать надо не заголовок виджета (он
            # есть сразу), а строку ranking: до неё показывать нечего.
            view(page, "Результат")
            page.get_by_role("cell", name="order-service").first.wait_for(timeout=STEP_MS)
            page.wait_for_timeout(1000)
            shoot(page, out, "portal-run.png")

        # Остановок может быть две: сначала запрос, который составила модель
        # (если она за ним пошла), потом публикация.
        composed: list[bool] = []

        def card(p) -> None:
            if p.locator(".approve .approve-queries").count():
                if not composed:
                    shoot(p, out, "portal-query-approval.png")
                    composed.append(True)
            else:
                shoot(p, out, "portal-approval.png")

        approve_all(page, card, timeout_ms=STEP_MS,
                thread=args.thread)
        if not composed:
            print("модель не составляла запрос: portal-query-approval.png не обновлён")

        # Опубликованный документ открывается вкладкой «Документ»: это и есть
        # то, что читает человек после прогона. Список идёт свежими вверх.
        document_button = page.locator(
            "details.engine-outline .resource-list button", has_text="Проведи анализ НТ").first
        document_button.wait_for(state="visible", timeout=STEP_MS)
        document_button.click()
        page.locator(".document-view .engine-document").first.wait_for(timeout=60_000)
        page.wait_for_timeout(800)
        shoot(page, out, "portal-report.png")

        # Гипотезы лежат в середине отчёта — второй снимок: текст без шапки,
        # каким его и видит читающий.
        page.evaluate(SCROLL_TO_TEXT, "Root cause analysis")
        page.wait_for_timeout(500)
        shoot(page, out, "portal-hypotheses.png")

        # Журнал снимается последним: к концу прогона в нём есть и вызовы
        # инструментов, и остановки на подтверждении, а не первые восемь строк.
        # У досъёмки журнала нет: интерфейс собирает события из живого потока,
        # и у треда, открытого заново, он пуст.
        if not args.thread:
            console_tab(page, "События")
            page.evaluate(SCROLL_CONSOLE_TO_END)
            page.wait_for_timeout(500)
            shoot_box(page, page.locator(".console").first, out, "portal-log.png", pad=0)
        context.close()
        browser.close()

    export_report(Path(args.report).resolve())


def export_report(target: Path) -> None:
    """Копия опубликованного отчёта рядом с документацией, как пример выхода."""
    env = dotenv_values(ROOT / ".env") | dotenv_values(TESTBED_ENV)
    published = ROOT / (env.get("PUBLISH_DIR") or "published")
    files = sorted(published.glob("*nt-run-2291*.md"), key=lambda p: p.stat().st_mtime)
    if not files:
        print(f"в {published} нет опубликованного отчёта по nt-run-2291")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    text = files[-1].read_text(encoding="utf-8")
    target.write_text(text, encoding="utf-8")
    print(f"отчёт: {shown(target)} ({len(text)} символов)")


if __name__ == "__main__":
    main()
