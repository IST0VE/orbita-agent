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
import json
import sys
import time
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
TESTBED_ENV = ROOT / ".env.nt-testbed"

TASK = "Проведи анализ НТ по NT-123.\ntest_id=nt-run-2291\nprevious_test_id=nt-run-2187"

# Интерфейс подписан по-русски, поэтому и опора — на подписи, а не на классы:
# вёрстка меняется чаще, чем текст кнопки.
RUN_BUTTON = "[запустить]"
DETAILS_BUTTON = "[подробности]"
APPROVE_BUTTON = "[подтвердить]"

# Прогон по стенду занимает одну-три минуты: ходов исследования до шести, и на
# каждом модель отвечает неравномерно. Ожидание с запасом дешевле, чем
# оборванный на середине прогон — обрыв потока отменяет и сам run.
STEP_MS = 420_000

# Панели растут под содержимое, своей полосы прокрутки у документа нет: едет
# ближайший прокручиваемый предок. Поэтому он и ищется, а не задаётся селектором.
SCROLL_TO_HEADING = """(title) => {
  const head = [...document.querySelectorAll('.engine-document :is(h1, h2, h3)')]
    .find((node) => node.textContent.includes(title));
  if (!head) return false;
  let box = head.parentElement;
  while (box && box.scrollHeight <= box.clientHeight + 2) box = box.parentElement;
  const target = box ?? document.scrollingElement;
  target.scrollTop += head.getBoundingClientRect().top - target.getBoundingClientRect().top - 48;
  return true;
}"""

# У журнала своя высота и своя полоса прокрутки: последние события интереснее
# первых, но какой именно элемент внутри панели едет — зависит от вёрстки.
SCROLL_JOURNAL_TO_END = """() => {
  const panel = document.querySelector('.timeline-panel');
  if (!panel) return false;
  const box = [panel, ...panel.querySelectorAll('*')]
    .find((node) => node.scrollHeight > node.clientHeight + 2);
  if (box) box.scrollTop = box.scrollHeight;
  return !!box;
}"""



def shown(path: Path) -> str:
    """Путь для вывода: короткий внутри проекта, полный — снаружи."""
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def token() -> str:
    value = dotenv_values(TESTBED_ENV).get("API_ADMIN_TOKEN") if TESTBED_ENV.exists() else None
    if not value:
        sys.exit("в .env.nt-testbed нет API_ADMIN_TOKEN: портал без него получает 401")
    return value


def shoot(target, out: Path, name: str) -> None:
    """Снимок кадра или отдельной панели. Chromium изредка не отдаёт первый."""
    path = out / name
    for attempt in range(3):
        try:
            target.screenshot(path=str(path), animations="disabled")
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1.5)
            continue
        print(f"снимок: {shown(path)}")
        return


def shoot_box(page, locator, out: Path, name: str) -> None:
    """Снимок одной панели с полями: заголовок лежит на её рамке."""
    box = locator.bounding_box()
    pad = 14
    clip = {"x": max(box["x"] - pad, 0), "y": max(box["y"] - pad, 0),
            "width": box["width"] + 2 * pad, "height": box["height"] + 2 * pad}
    path = out / name
    page.screenshot(path=str(path), clip=clip, animations="disabled")
    print(f"снимок: {shown(path)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:5173")
    parser.add_argument("--out", default=str(ROOT / "docs" / "image" / "NT"))
    parser.add_argument("--report", default=str(ROOT / "docs" / "examples" / "nt-run-2291.md"))
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
        # Ретина-масштаб: в документации снимок ужимается по ширине колонки, и
        # при обычном масштабе терминальный шрифт превращается в кашу.
        context = browser.new_context(viewport={"width": 1280, "height": 720},
                                      device_scale_factor=2, color_scheme="dark")
        # Токен и выбранный граф кладутся до загрузки приложения: иначе первый
        # же запрос получает 401 и портал показывает окно ввода токена.
        context.add_init_script(
            f"sessionStorage.setItem('orbita.adminApiToken', {json.dumps(token())});"
            "localStorage.setItem('orbita.graph', 'nt');"
            # Список опубликованных документов свёрнут по умолчанию, и кнопки
            # внутри него скрыты: клик по невидимому элементу не проходит.
            "localStorage.setItem('orbita.published.open', '1');"
            "Object.keys(localStorage).filter(k => k.startsWith('orbita.thread'))"
            ".forEach(k => localStorage.removeItem(k));")
        page = context.new_page()
        # Портал не перезапрашивает манифест, если backend ещё поднимался в
        # момент загрузки: он остаётся с «нет сервера». Поэтому страница
        # перезагружается, пока поле ввода не появится.
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
        task.fill(TASK)
        page.get_by_role("button", name=RUN_BUTTON).first.click()

        # Первый снимок — когда код уже посчитал вердикт и ranking, а модель
        # ещё разбирает причины: на нём видно, что чем занято. Ждать надо не
        # заголовок виджета (он есть сразу, со значением по умолчанию), а
        # строку ranking: до неё показывать нечего.
        page.get_by_role("cell", name="order-service").first.wait_for(timeout=STEP_MS)
        page.wait_for_timeout(1000)
        shoot(page, out, "portal-run.png")

        # Остановок может быть две: сначала запрос, который составила модель
        # (если она за ним пошла), потом публикация. Ориентир — видимая кнопка
        # подтверждения: заголовок окна встречается и в скрытом тексте журнала,
        # туда локатор попадает первым.
        composed_shot = False
        while True:
            approve = page.get_by_role("button", name=APPROVE_BUTTON).first
            approve.wait_for(state="visible", timeout=STEP_MS)
            page.wait_for_timeout(500)
            if not page.locator(".approve-queries").count():
                shoot(page, out, "portal-approval.png")
                approve.click()
                break
            if not composed_shot:
                shoot(page, out, "portal-query-approval.png")
                composed_shot = True
            approve.click()
            page.locator(".approve-queries").first.wait_for(state="detached", timeout=STEP_MS)
        if not composed_shot:
            print("модель не составляла запрос: portal-query-approval.png не обновлён")
        # Ждать надо не статус публикации, а её результат: строку документа
        # в списке. Статус лежит в общем тексте состояния, где «CREATED»
        # встречается и внутри JSON.
        document_button = page.locator("button", has_text="Проведи анализ НТ").first
        document_button.wait_for(state="visible", timeout=STEP_MS)

        # Опубликованный документ открывается на месте схемы: это и есть то,
        # что читает человек после прогона.
        document_button.click()
        document = page.locator(".engine-document").first
        document.wait_for(timeout=60_000)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(500)
        shoot(page, out, "portal-report.png")

        # Гипотезы лежат в середине отчёта, и прокручивается вся страница:
        # панели растут под содержимое, своей полосы у документа нет. Поэтому
        # второй снимок — текст без шапки, каким его и видит читающий.
        page.evaluate(SCROLL_TO_HEADING, "Root cause analysis")
        page.wait_for_timeout(500)
        shoot(page, out, "portal-hypotheses.png")

        # Журнал снимается последним: к концу прогона в нём есть и вызовы
        # инструментов, и остановка на подтверждении, а не первые восемь строк.
        page.get_by_role("button", name=DETAILS_BUTTON).first.click()
        journal = page.locator(".timeline-panel").first
        journal.wait_for(timeout=30_000)
        journal.scroll_into_view_if_needed()
        page.evaluate(SCROLL_JOURNAL_TO_END)
        page.wait_for_timeout(500)
        shoot_box(page, journal, out, "portal-log.png")
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
