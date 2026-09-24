"""Скриншоты портала Orbita на прогоне графа nt_run по стенду nt-testbed.

Снимки для docs/NT_RUN.md делаются с того же интерфейса, в котором работает
оператор: подготовка сценария, разрешение запуска с готовым k6-скриптом, живые
метрики идущего прогона, журнал и итоговый отчёт. Скрипт проводит настоящее НТ —
настоящий k6 по настоящей цели стенда, — поэтому картинки всегда соответствуют
текущему коду, а не старому прогону.

Перед запуском нужны четыре вещи:

    cd ../nt-testbed && docker compose up -d      # стенд
    python scripts/serve_nt_runner.py --config config/nt-runner.json
    python scripts/serve_nt_testbed.py            # backend на координатах стенда
    npm --prefix web run dev                      # портал

Затем:

    python scripts/shoot_nt_run_portal.py
    python scripts/shoot_nt_run_portal.py --headed --out docs/image/NT_RUN

Прогон стоит несколько вызовов модели и выполняет реальную нагрузку на стенд:
`NT_RUN_AUTO_APPROVE` должен быть 0, иначе разрешение запуска не покажется.
"""

import argparse
from pathlib import Path

from portal_shots import (
    LIVE_APPROVE,
    ROOT,
    SCROLL_CONSOLE_TO_END,
    SCROLL_DIALOG_TO_TEXT,
    SCROLL_TO_TEXT,
    approve_all,
    console_tab,
    expand,
    launch,
    open_portal,
    playwright,
    reload,
    run_task,
    shoot,
    shoot_box,
    shown,
    view,
    zoom_out,
)

TASK = """Проведи нагрузочное тестирование стенда checkout.
Сценарий: POST /api/checkout с телом {"cart_id": "<из набора данных>", "items": 2}, ожидается HTTP 200.
Цель: убедиться, что checkout-api держит 20 запросов в секунду.
Разгон 10 секунд, плато 40 секунд, до 10 виртуальных пользователей.
SLA: p95 <= 800 мс, доля ошибок <= 1%.
Остановить прогон при p95 > 2500 мс или доле ошибок > 15%.
Авторизация не нужна, используй синтетические cart_id."""

# Нагрузка идёт две-три минуты по часам, но ходы модели на демо-шлюзе могут
# ждать окна квоты.
STEP_MS = 3_600_000

# Живые метрики надо снять, пока нагрузка идёт. Окно короткое — плато сценария,
# — поэтому ожидание построено на появлении виджета, а не на фиксированной паузе.
LIVE_WIDGET = "Статус и метрики"

# Через сколько после появления метрик снимать идущую нагрузку: пробный прогон
# занимает секунды, дальше идёт разгон и плато сценария из задачи.
LIVE_SHOT_MS = 30_000


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:5173")
    parser.add_argument("--out", default=str(ROOT / "docs" / "image" / "NT_RUN"))
    parser.add_argument("--report", default=str(ROOT / "docs" / "examples" / "nt-run-checkout.md"))
    parser.add_argument("--thread", help="доснять с разрешения запуска по уже запущенному "
                        "треду, без нового планирования")
    parser.add_argument("--headed", action="store_true", help="показать браузер")
    args = parser.parse_args()

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    with playwright() as driver:
        browser = launch(driver, headed=args.headed)
        context, page = open_portal(browser, args.url, "nt_run", console_height=250,
                                    thread=args.thread)
        if not args.thread:
            # Граф до запуска: видно топологию конвейера и выбранный сценарий.
            zoom_out(page, 2)
            shoot(page, out, "portal-graph.png")
            run_task(page, TASK)

        # Первая остановка — разрешение запуска. До неё модель читает материалы
        # и собирает план, и именно этот кадр показывает, что оператор видит
        # перед тем, как на стенд уйдёт нагрузка.
        approve = page.locator(LIVE_APPROVE).first
        approve.wait_for(state="visible", timeout=STEP_MS)
        page.wait_for_timeout(1200)
        # Планировщик может так и не дойти до плана: исчерпав шаги, граф сразу
        # просит опубликовать отчёт «подтверждённых прогонов нет». Такую
        # карточку под именем разрешения запуска снимать нельзя.
        if page.locator(".approve").first.get_by_text("Подготовлено объектов").count():
            raise SystemExit("первой остановкой стала публикация, а не разрешение запуска: "
                             "планировщик не дошёл до плана, кадры не сняты")
        shoot(page, out, "portal-launch-approval.png")
        # Окно показывает план свёрнутым. Подтверждается конкретный сценарий
        # и конкретный код, поэтому для документации он раскрывается: второй
        # кадр — то, что оператор обязан прочитать до запуска нагрузки. Сводка
        # начинается набором данных, поэтому кадр подводится к k6-скрипту.
        summary = page.locator(".approve summary", has_text="Сводка конвейера").first
        if expand(summary):
            page.wait_for_timeout(600)
            page.evaluate(SCROLL_DIALOG_TO_TEXT, "k6/http")
            page.wait_for_timeout(400)
            shoot(page, out, "portal-launch-plan.png")
            # Развёрнутая сводка выше окна и уводит кнопку за край: нажатие
            # тогда не доходит, и прогон так и стоит на разрешении.
            expand(summary)
            page.wait_for_timeout(400)
        approve.click()
        try:
            page.locator(".approve").first.wait_for(state="detached", timeout=60_000)
        except Exception:
            reload(page)

        # Пробный прогон, затем основной. Метрики живут во вкладке «Результат»;
        # снимок нужен, пока нагрузка идёт. Тред, открытый заново, живой поток
        # не получает — его состояние перечитывается перезагрузкой.
        view(page, "Результат")
        if args.thread:
            page.wait_for_timeout(LIVE_SHOT_MS)
            reload(page)
            view(page, "Результат")
        page.get_by_text(LIVE_WIDGET).first.wait_for(timeout=STEP_MS)
        if not args.thread:
            page.wait_for_timeout(LIVE_SHOT_MS)
        shoot(page, out, "portal-live.png")

        # Дальше остановки только на публикации: сначала документ вложенного
        # анализа, потом отчёт кампании. Снимается первая встреченная.
        published: list[bool] = []

        def card(p) -> None:
            if not published:
                shoot(p, out, "portal-publish-approval.png")
                published.append(True)

        approve_all(page, card, timeout_ms=STEP_MS, thread=args.thread)

        # Итоги кампании: план, test_id, измерения прогонов, анализ.
        view(page, "Результат")
        page.wait_for_timeout(800)
        shoot(page, out, "portal-state.png")

        # Отчёт кампании публикуется последним, а список идёт свежими вверх.
        document_button = page.locator(
            "details.engine-outline .resource-list button",
            has_text="Проведи нагрузочное тестирование").first
        document_button.wait_for(state="visible", timeout=STEP_MS)
        document_button.click(timeout=60_000)
        document = page.locator(".document-view .engine-document").first
        document.wait_for(timeout=60_000)
        page.wait_for_timeout(800)
        shoot(page, out, "portal-report.png")

        if page.evaluate(SCROLL_TO_TEXT, "Исторический анализ"):
            page.wait_for_timeout(500)
            shoot(page, out, "portal-report-analysis.png")

        console_tab(page, "События")
        page.evaluate(SCROLL_CONSOLE_TO_END)
        page.wait_for_timeout(500)
        shoot_box(page, page.locator(".console").first, out, "portal-log.png", pad=0)

        report = Path(args.report)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(document.inner_text(), encoding="utf-8")
        print(f"отчёт: {shown(report)}", flush=True)
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
