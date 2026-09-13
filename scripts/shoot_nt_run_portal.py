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
import json
import sys
import time
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
TESTBED_ENV = ROOT / ".env.nt-testbed"

TASK = """Проведи нагрузочное тестирование стенда checkout.
Сценарий: POST /api/checkout с телом {"cart_id": "<из набора данных>", "items": 2}, ожидается HTTP 200.
Цель: убедиться, что checkout-api держит 20 запросов в секунду.
Разгон 10 секунд, плато 40 секунд, до 10 виртуальных пользователей.
SLA: p95 <= 800 мс, доля ошибок <= 1%.
Остановить прогон при p95 > 2500 мс или доле ошибок > 15%.
Авторизация не нужна, используй синтетические cart_id."""

RUN_BUTTON = "[запустить]"
DETAILS_BUTTON = "[подробности]"
APPROVE_BUTTON = "[подтвердить]"
# Отвеченная карточка подтверждения остаётся в разметке с погашенной кнопкой:
# без отбора по «не disabled» локатор цепляется за неё и ждёт, пока она оживёт.
LIVE_APPROVE = "button:not([disabled])"

# Прогон занимает две-три минуты: разгон и плато идут по часам, а не по модели.
STEP_MS = 420_000

# Живые метрики надо снять, пока нагрузка идёт. Окно короткое — плато сценария,
# — поэтому ожидание построено на появлении виджета, а не на фиксированной паузе.
LIVE_WIDGET = "Статус и метрики"

# Через сколько после разрешения запуска снимать идущую нагрузку: пробный прогон
# занимает секунды, дальше идёт разгон и плато сценария из задачи.
LIVE_SHOT_MS = 30_000

SCROLL_TO_TEXT = """(needle) => {
  const node = [...document.querySelectorAll('.engine-document :is(h1, h2, h3, p, li)')]
    .find((item) => item.textContent.includes(needle));
  if (!node) return false;
  let box = node.parentElement;
  while (box && box.scrollHeight <= box.clientHeight + 2) box = box.parentElement;
  const target = box ?? document.scrollingElement;
  target.scrollTop += node.getBoundingClientRect().top - target.getBoundingClientRect().top - 48;
  return true;
}"""

SCROLL_JOURNAL_TO_END = """() => {
  const panel = document.querySelector('.timeline-panel');
  if (!panel) return false;
  const box = [panel, ...panel.querySelectorAll('*')]
    .find((node) => node.scrollHeight > node.clientHeight + 2);
  if (box) box.scrollTop = box.scrollHeight;
  return !!box;
}"""


def shown(path: Path) -> str:
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def token() -> str:
    value = dotenv_values(TESTBED_ENV).get("API_ADMIN_TOKEN") if TESTBED_ENV.exists() else None
    if not value:
        sys.exit("в .env.nt-testbed нет API_ADMIN_TOKEN: портал без него получает 401")
    return value


def shoot(target, out: Path, name: str) -> None:
    """Снимок кадра или панели. Chromium изредка не отдаёт первый."""
    path = out / name
    for attempt in range(3):
        try:
            target.screenshot(path=str(path), animations="disabled")
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1.5)
            continue
        print(f"снимок: {shown(path)}", flush=True)
        return


def expand(locator) -> bool:
    """Раскрыть свёрнутый блок, если он есть.

    Кадр важнее раскрытия: неудачный клик по виджету не должен ронять съёмку.
    Упавший скрипт закрывает поток, LangGraph отменяет run, а граф — честно
    гасит идущий прогон k6. Один снимок не стоит остановленного НТ.
    """
    try:
        locator.click(timeout=5000)
        return True
    except Exception:
        return False


def shoot_box(page, locator, out: Path, name: str) -> None:
    box = locator.bounding_box()
    pad = 14
    clip = {"x": max(box["x"] - pad, 0), "y": max(box["y"] - pad, 0),
            "width": box["width"] + 2 * pad, "height": box["height"] + 2 * pad}
    page.screenshot(path=str(out / name), clip=clip, animations="disabled")
    print(f"снимок: {shown(out / name)}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:5173")
    parser.add_argument("--out", default=str(ROOT / "docs" / "image" / "NT_RUN"))
    parser.add_argument("--report", default=str(ROOT / "docs" / "examples" / "nt-run-checkout.md"))
    parser.add_argument("--headed", action="store_true", help="показать браузер")
    args = parser.parse_args()

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("нужен playwright: pip install -e .[docs] && playwright install chromium")

    with sync_playwright() as driver:
        browser = driver.chromium.launch(headless=not args.headed, args=["--disable-gpu"])
        context = browser.new_context(viewport={"width": 1440, "height": 900},
                                      device_scale_factor=2, color_scheme="dark")
        context.add_init_script(
            f"sessionStorage.setItem('orbita.adminApiToken', {json.dumps(token())});"
            "localStorage.setItem('orbita.graph', 'nt_run');"
            "localStorage.setItem('orbita.published.open', '1');"
            "Object.keys(localStorage).filter(k => k.startsWith('orbita.thread'))"
            ".forEach(k => localStorage.removeItem(k));")
        page = context.new_page()
        task = page.get_by_placeholder("Опишите задачу…")
        for attempt in range(6):
            page.goto(args.url)
            try:
                task.wait_for(timeout=15_000)
                break
            except Exception:
                if attempt == 5:
                    raise
                page.wait_for_timeout(3000)

        # Пустой граф до запуска: видно топологию конвейера и выбранный конвейер.
        page.wait_for_timeout(1500)
        shoot(page, out, "portal-graph.png")

        task.fill(TASK)
        page.get_by_role("button", name=RUN_BUTTON).first.click()

        # Первая остановка — разрешение запуска. До неё модель читает материалы
        # и собирает план, и именно этот кадр показывает, что оператор видит
        # перед тем, как на стенд уйдёт нагрузка.
        approve = page.locator(LIVE_APPROVE, has_text=APPROVE_BUTTON).first
        approve.wait_for(state="visible", timeout=STEP_MS)
        page.wait_for_timeout(1200)
        shoot(page, out, "portal-launch-approval.png")
        # Окно показывает документ свёрнутым. Подтверждается конкретный сценарий
        # и конкретный код, поэтому для документации он раскрывается: второй кадр
        # — то, что оператор обязан прочитать до запуска нагрузки.
        if expand(page.locator("summary", has_text="Сводка конвейера").first):
            page.wait_for_timeout(600)
            shoot(page, out, "portal-launch-plan.png")
        approve.click()

        # Пробный прогон, затем основной. Снимок нужен, пока нагрузка идёт:
        # опора на текст состояния, а не на классы вёрстки — они меняются чаще.
        page.get_by_text(LIVE_WIDGET).first.wait_for(timeout=STEP_MS)
        # Виджеты состояния свёрнуты в «JSON»: пока их не раскрыть, ни метрик,
        # ни test_id на снимке не будет — и ждать их в тексте страницы бесполезно.
        page.wait_for_timeout(LIVE_SHOT_MS)

        # Дальше остановки только на публикации: сначала документ вложенного
        # анализа, потом отчёт кампании. Снимается первая встреченная. Признак
        # конца — что подтверждать больше нечего: список готовых документов не
        # годится, в нём лежат отчёты прошлых прогонов с тем же началом имени.
        publish_shot = False
        for _ in range(4):
            approve = page.locator(LIVE_APPROVE, has_text=APPROVE_BUTTON).first
            try:
                approve.wait_for(state="visible", timeout=120_000)
            except Exception:
                break
            page.wait_for_timeout(600)
            if not publish_shot:
                shoot(page, out, "portal-publish-approval.png")
                publish_shot = True
            # Нажатая кнопка гаснет, пока идёт запрос, и остаётся видимой:
            # без ожидания исчезновения следующий проход цепляется за неё же и
            # ждёт, пока неактивная кнопка снова станет кликабельной.
            try:
                approve.click(timeout=60_000)
            except Exception:
                break
            try:
                approve.wait_for(state="detached", timeout=60_000)
            except Exception:
                page.wait_for_timeout(3000)

        document_button = page.locator("button", has_text="Проведи нагрузочное тестирование").last
        document_button.wait_for(state="visible", timeout=STEP_MS)
        document_button.click(timeout=60_000)
        document = page.locator(".engine-document").first
        document.wait_for(timeout=60_000)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(500)
        shoot(page, out, "portal-report.png")

        if page.evaluate(SCROLL_TO_TEXT, "Исторический анализ"):
            page.wait_for_timeout(500)
            shoot(page, out, "portal-report-analysis.png")

        # Панель состояния целиком: план, test_id, измерения прогонов, анализ и
        # журнал. Снимать её кадром страницы бессмысленно — карточка последнего
        # подтверждения остаётся в ленте прогона и занимает середину экрана.
        for title in (LIVE_WIDGET, "Текущий test_id", "Выполненные прогоны"):
            expand(page.get_by_text(title).first.locator("xpath=following::summary[1]"))
        state = page.locator(".state-panel, aside").last
        if state.count():
            page.wait_for_timeout(400)
            shoot_box(page, state, out, "portal-state.png")

        page.get_by_role("button", name=DETAILS_BUTTON).first.click()
        journal = page.locator(".timeline-panel").first
        journal.wait_for(timeout=30_000)
        journal.scroll_into_view_if_needed()
        page.evaluate(SCROLL_JOURNAL_TO_END)
        page.wait_for_timeout(500)
        shoot_box(page, journal, out, "portal-log.png")

        report = Path(args.report)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(document.inner_text(), encoding="utf-8")
        print(f"отчёт: {shown(report)}", flush=True)
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
