"""Общее для скриптов съёмки портала: вход, подписи интерфейса, снимки.

Три скрипта снимают один и тот же интерфейс — `shoot_portal.py` сценарии
аналитики, `shoot_nt_portal.py` и `shoot_nt_run_portal.py` графы НТ. Подписи
кнопок и классы вёрстки собраны здесь потому, что интерфейс меняется целиком,
а не по кнопке: после переоформления 18 сентября 2026 все скрипты искали
«[запустить]», которой больше нет.

Backend для съёмки — `scripts/serve_nt_testbed.py`: он подменяет Jira и
Confluence моками стенда и публикует в файл, так что прогон ради картинки не
создаёт ни страниц, ни задач в настоящих системах.
"""

import json
import sys
import time
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
TESTBED_ENV = ROOT / ".env.nt-testbed"

# Кадр документации: снимок ужимается по ширине колонки, поэтому окно берётся
# с запасом по высоте — иначе композер и колонки отнимают у полотна графа
# слишком много, — а масштаб двойной, чтобы подписи читались после уменьшения.
VIEWPORT = {"width": 1600, "height": 1000}
SCALE = 2

# Интерфейс подписан по-русски, поэтому и опора — на подписи, а не на классы:
# вёрстка меняется чаще, чем текст кнопки.
TASK_PLACEHOLDER = "Опишите задачу для ORBITA"
RUN_BUTTON = "Запустить"
APPROVE_BUTTON = "Подтвердить"
# Отвеченная карточка подтверждения остаётся в разметке с погашенной кнопкой:
# без отбора по «не disabled» локатор цепляется за неё и ждёт, пока она оживёт.
LIVE_APPROVE = ".approve button.btn-yes:not([disabled])"
RUN_STATUS = ".context-bar"

# Панели растут под содержимое: едет ближайший прокручиваемый предок. Поэтому
# он и ищется, а не задаётся селектором.
SCROLL_TO_TEXT = """(needle) => {
  const node = [...document.querySelectorAll('.document-view .engine-document :is(h1, h2, h3, p, li)')]
    .find((item) => item.textContent.includes(needle));
  if (!node) return false;
  let box = node.parentElement;
  while (box && box.scrollHeight <= box.clientHeight + 2) box = box.parentElement;
  const target = box ?? document.scrollingElement;
  target.scrollTop += node.getBoundingClientRect().top - target.getBoundingClientRect().top - 48;
  return true;
}"""

# Подвести окно подтверждения к строке внутри длинного блока: сводка плана —
# один <pre>, и прокрутка к элементу привела бы к его началу, а не к строке.
SCROLL_DIALOG_TO_TEXT = """(needle) => {
  const dialog = document.querySelector('.approve');
  if (!dialog) return false;
  const walker = document.createTreeWalker(dialog, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode()) && !node.textContent.includes(needle));
  if (!node) return false;
  const range = document.createRange();
  const at = node.textContent.indexOf(needle);
  range.setStart(node, at);
  range.setEnd(node, at + needle.length);
  let box = node.parentElement;
  while (box && box.scrollHeight <= box.clientHeight + 2) box = box.parentElement;
  const target = box ?? document.scrollingElement;
  target.scrollTop += range.getBoundingClientRect().top - target.getBoundingClientRect().top - 120;
  return true;
}"""

def shown(path: Path) -> str:
    """Путь для вывода: короткий внутри проекта, полный — снаружи."""
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def token(env_file: Path = TESTBED_ENV) -> str:
    """Токен служебного API из того же файла, с которым поднят backend."""
    value = dotenv_values(env_file).get("API_ADMIN_TOKEN") if env_file.exists() else None
    if not value:
        sys.exit(f"в {shown(env_file)} нет API_ADMIN_TOKEN: портал без него получает 401")
    return value


def playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("нужен playwright: pip install -e .[docs] && playwright install chromium")
    return sync_playwright()


def launch(driver, *, headed: bool = False):
    return driver.chromium.launch(headless=not headed, args=["--disable-gpu"])


def open_portal(browser, url: str, graph: str, *, env_file: Path = TESTBED_ENV,
                published_open: bool = True, thread: str | None = None):
    """Контекст и страница портала с выбранным графом и новым чатом.

    Контекст свой на каждую сцену: браузер запоминает последний открытый чат,
    и без чистого хранилища следующий кадр продолжил бы прошлый прогон. `thread`
    открывает уже существующий чат — досъёмка без нового прогона.
    """
    context = browser.new_context(viewport=VIEWPORT, device_scale_factor=SCALE,
                                  color_scheme="light", locale="ru-RU")
    # Токен и выбранный граф кладутся до загрузки приложения: иначе первый
    # же запрос получает 401 и портал показывает окно ввода токена.
    script = (
        f"sessionStorage.setItem('orbita.adminApiToken', {json.dumps(token(env_file))});"
        f"localStorage.setItem('orbita.graph', {json.dumps(graph)});"
        "Object.keys(localStorage).filter(k => /^orbita[.](chat|thread)/.test(k))"
        ".forEach(k => localStorage.removeItem(k));")
    if thread:
        # Последний открытый чат — один на пользователя, вместе со сценарием.
        chat = json.dumps({"thread": thread, "graph": graph})
        script += f"localStorage.setItem('orbita.chat', {json.dumps(chat)});"
    if published_open:
        # Список опубликованных документов свёрнут по умолчанию, и кнопки
        # внутри него скрыты: клик по невидимому элементу не проходит.
        script += "localStorage.setItem('orbita.published.open', '1');"
    # Скрипт выполняется при каждой загрузке, а не один раз: перезагрузка
    # страницы посреди сцены не должна терять тред, поэтому чистка — только
    # на первой.
    context.add_init_script(
        "if (!sessionStorage.getItem('orbita.shots')) {"
        f"{script} sessionStorage.setItem('orbita.shots', '1'); }}")
    page = context.new_page()
    # Портал не перезапрашивает манифест, если backend ещё поднимался в
    # момент загрузки: он остаётся с «нет сервера». Поэтому страница
    # перезагружается, пока поле ввода не появится.
    task = task_field(page)
    for attempt in range(6):
        page.goto(url)
        try:
            task.wait_for(timeout=15_000)
            break
        except Exception:
            if attempt == 5:
                raise
            page.wait_for_timeout(3000)
    # Полотно раскладывается после загрузки топологии и отдельным воркером;
    # кадр до этого ловит пустую панель с «Загрузка схемы…».
    page.locator(".graph-node").first.wait_for(timeout=30_000)
    page.wait_for_timeout(800)
    return context, page


def task_field(page):
    return page.get_by_placeholder(TASK_PLACEHOLDER)


def run_task(page, text: str) -> None:
    task_field(page).fill(text)
    page.get_by_role("button", name=RUN_BUTTON).first.click()


def run_status(page) -> str:
    """Строка контекста: «Выполняется», «Ждёт решения», «Завершён», «Ошибка»…"""
    return page.locator(RUN_STATUS).first.inner_text()


def wait_status(page, *labels: str, timeout_ms: int, refresh_ms: int | None = None,
                tick=None) -> str:
    """Дождаться одного из статусов прогона в строке контекста.

    `refresh_ms` — перечитывать страницу с такой частотой. Нужно треду,
    открытому заново: живой поток продолжения он не получает, и строка
    статуса без перезагрузки так и осталась бы прежней. `tick(page)` зовётся
    на каждом проходе ожидания: там снимают то, что видно только пока прогон
    идёт, например живые метрики нагрузки.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    refreshed = time.monotonic()
    while time.monotonic() < deadline:
        status = run_status(page)
        for label in labels:
            if label in status:
                return label
        if tick:
            tick(page)
        if refresh_ms and (time.monotonic() - refreshed) * 1000 >= refresh_ms:
            reload(page)
            refreshed = time.monotonic()
        page.wait_for_timeout(1000)
    raise TimeoutError(f"за {timeout_ms // 1000} с статус не стал ни одним из {labels}")


def reload(page) -> None:
    """Перечитать страницу и дождаться полотна: состояние треда — с сервера."""
    page.reload()
    page.locator(".graph-node").first.wait_for(timeout=30_000)
    page.wait_for_timeout(800)


THREAD_STATUS = """async (thread) => {
  const token = sessionStorage.getItem('orbita.adminApiToken');
  const res = await fetch(`/threads/${encodeURIComponent(thread)}`,
    {headers: {authorization: `Bearer ${token}`}, cache: 'no-store'});
  return res.ok ? (await res.json()).status : `HTTP ${res.status}`;
}"""


def thread_status(page, thread: str) -> str:
    """Статус треда на сервере LangGraph: idle, busy, interrupted или error."""
    return page.evaluate(THREAD_STATUS, thread)


def approve_all(page, on_card=None, *, timeout_ms: int, thread: str | None = None,
                tick=None) -> None:
    """Подтверждать остановки, пока прогон не закончится.

    `on_card(page)` вызывается на каждой карточке до нажатия: там и снимают.
    Остановок бывает несколько — запрос к метрикам, заведение задач,
    публикация, — и порядок у графов разный. Строка статуса отстаёт от
    потока на секунды: после нажатия она ещё показывает «Ждёт решения»,
    хотя карточки уже нет, — такой проход просто повторяется.

    `thread` — досъёмка по треду, открытому заново. Поток продолжения такой
    тред не получает, и «Готов к работе» он показывает не только законченным,
    но и тогда, когда сервер ещё работает между остановками. Поэтому эта
    строка сверяется со статусом треда на сервере: пока он занят, прогон
    не кончился, и следующее подтверждение ещё впереди.
    """
    resumed = thread is not None
    done = ("Завершён", "Готов к работе") if resumed else ("Завершён",)
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        left = int((deadline - time.monotonic()) * 1000)
        if left <= 0:
            raise TimeoutError("прогон не закончился за отведённое время")
        state = wait_status(page, "Ждёт решения", *done, "Ошибка", "Остановлен",
                            timeout_ms=left, refresh_ms=20_000 if resumed else None,
                            tick=tick)
        if state == "Готов к работе":
            server = thread_status(page, thread)
            if server in ("busy", "interrupted"):
                page.wait_for_timeout(3000)
                reload(page)
                continue
            if server != "idle":
                raise SystemExit(f"тред {thread} на сервере в статусе «{server}»")
            return
        if state != "Ждёт решения":
            if state not in done:
                raise SystemExit(f"прогон закончился статусом «{state}»")
            return
        button = page.locator(LIVE_APPROVE).first
        try:
            button.wait_for(state="visible", timeout=15_000)
        except Exception:
            continue
        page.wait_for_timeout(800)
        if on_card:
            on_card(page)
        button.click()
        settle_after_click(page, timeout_ms=left, resumed=resumed)


def settle_after_click(page, *, timeout_ms: int, resumed: bool = False) -> None:
    """Дождаться, пока решение по карточке подтверждения примется.

    Нажатая кнопка гаснет, пока идёт запрос, и остаётся в разметке. Карточка
    после ответа не обязана исчезать: пока граф работает дальше, она стоит
    погашенной, а следующая остановка приходит в тот же элемент — это и есть
    «новая карточка». Поэтому ждём не «карточка ушла», а «карточка ушла или
    кнопка ожила снова», и делаем это долго: продолжение может идти минуты.
    Перезагрузка страницы тут вредна — закрытый поток отменяет прогон, и
    снимать будет нечего.
    """
    try:
        # Сначала дождаться, что нажатие дошло: до перерисовки кнопка ещё
        # жива, и без этого ожидание ниже вернулось бы сразу.
        page.wait_for_function(
            "(selector) => !document.querySelector(selector)",
            arg=LIVE_APPROVE, timeout=15_000)
    except Exception:
        return
    try:
        page.wait_for_function(
            "(selector) => !document.querySelector('.approve')"
            " || document.querySelector(selector)",
            arg=LIVE_APPROVE, timeout=timeout_ms)
    except Exception:
        if not resumed:
            raise
        # Карточка чата, открытого заново, после ответа остаётся на экране,
        # хотя сервер решение принял: поток продолжения интерфейс не
        # подхватывает. Состояние перечитывается с сервера.
        reload(page)


def view(page, name: str) -> None:
    """Вид рабочей области: «Схема», «Результат» или «Документ»."""
    page.locator(".workspace-bar .tab", has_text=name).first.click(timeout=10_000)
    page.wait_for_timeout(500)


def open_events(page) -> None:
    """Раскрыть журнал событий прогона в правой колонке.

    Отдельной консоли больше нет: события живут во второй вкладке правой
    колонки — «Сценарий» до прогона и «Прогон» после него, — в свёрнутом
    блоке, а колокольчик в шапке подводит взгляд к нему.
    """
    page.locator(".inspector-tab").nth(1).click()
    events = page.locator("details.run-events").first
    events.wait_for(timeout=15_000)
    if events.get_attribute("open") is None:
        events.locator("summary").first.click()
    # Блок стоит внизу вкладки, под расходом и публикацией: без прокрутки на
    # кадре видна только его шапка.
    events.evaluate("(node) => node.scrollIntoView({block: 'start'})")
    page.wait_for_timeout(500)


def show_chat(page) -> None:
    """Вернуть правую колонку на вкладку «Чат»."""
    page.locator(".inspector-tab", has_text="Чат").first.click()
    page.wait_for_timeout(400)


def library(page):
    """Открыть окно «Добавить в чат» и вернуть его."""
    page.get_by_label("Добавить из библиотеки").click()
    dialog = page.locator(".chat-library").first
    dialog.wait_for(timeout=15_000)
    # Список примеров едет отдельным запросом: до него в окне только «Загрузка…».
    dialog.locator(".chat-library-body").wait_for(timeout=15_000)
    page.wait_for_timeout(400)
    return dialog


def open_example(dialog, name: str) -> None:
    """Раскрыть папку примеров в окне библиотеки (имя точное: `partial-refund` не `-package`)."""
    dialog.locator(f".chat-library-toggle:has(.truncate:text-is('{name}'))").first.click()
    dialog.page.wait_for_timeout(400)


def add_examples(page, name: str, files: tuple[str, ...] | None = None) -> None:
    """Скопировать в чат примеры из библиотеки: всю папку или выбранные файлы.

    Файлы попадают в чат копией, как и при загрузке с компьютера: первая же
    копия заводит чат из черновика, поэтому после неё список слева уже с ним.
    """
    dialog = library(page)
    open_example(dialog, name)
    rows = dialog.locator(".chat-library-folder", has=page.locator(
        f".chat-library-toggle:has(.truncate:text-is('{name}'))")).locator("li")
    total = rows.count()
    for index in range(total):
        row = rows.nth(index)
        label = row.locator(".text").first.inner_text()
        if files is not None and label not in files:
            continue
        row.get_by_role("button", name="В чат").click()
        row.get_by_role("button", name="В чате").wait_for(timeout=30_000)
    dialog.get_by_role("button", name="Готово").click()
    dialog.wait_for(state="detached", timeout=15_000)
    page.wait_for_timeout(800)


def upload_files(page, paths) -> None:
    """Загрузить файлы с диска в чат — кнопкой «Загрузить», как оператор.

    Нужна там, где библиотека недоступна: при длинном списке «Моих документов»
    окно библиотеки сжимает тело, и папки примеров оказываются под футером.
    """
    names = [Path(path).name for path in paths]
    page.locator(".chat-files input[type=file]").first.set_input_files([str(path) for path in paths])
    for name in names:
        page.locator(".chat-files-list .chat-file-name", has_text=name).first.wait_for(timeout=30_000)
    page.wait_for_timeout(800)


def pick(page, name: str) -> None:
    """Отметить файл чата отметкой «в прогон» (у сценариев с одной отметкой)."""
    page.get_by_role("checkbox", name=f"отметить {name}").first.click()
    page.wait_for_timeout(400)


def role(page, name: str, label: str) -> None:
    """Отметить файл чата ролью: «основной» или «материал» у обновления документа."""
    row = page.locator(".chat-files-list li", has=page.locator(f".text:text-is('{name}')")).first
    row.get_by_role("button", name=label).first.click()
    page.wait_for_timeout(400)


# Узел, на который смотрит камера: из подходящих — средний по горизонтали.
# Идущий этап обычно один, а у пройденного графа средний узел держит в кадре
# и начало, и конец.
FOCUS = """(selector) => {
  const pane = document.querySelector('.workspace-pane:not([hidden]) .graph-flow');
  if (!pane) return null;
  const nodes = [...pane.querySelectorAll(selector)]
    .map((node) => node.getBoundingClientRect())
    .sort((a, b) => a.x - b.x);
  if (!nodes.length) return null;
  const node = nodes[Math.floor((nodes.length - 1) / 2)];
  const box = pane.getBoundingClientRect();
  const x = box.x + box.width / 2, y = box.y + box.height * 0.42;
  return {x, y, dx: node.x + node.width / 2 - x, dy: node.y + node.height / 2 - y};
}"""


def center_on(page, selector: str) -> bool:
    """Подвести камеру полотна к узлу.

    Длинный конвейер открывается у старта, а камера за активным узлом не
    ездит: без этого на кадре прогона видны только первые два узла. Холст
    двигает обычное колесо — так же, как у оператора; точка чуть выше
    середины, потому что низ полотна закрывает подсказка по жестам.
    """
    for _ in range(4):
        focus = page.evaluate(FOCUS, selector)
        if not focus:
            return False
        if abs(focus["dx"]) < 12 and abs(focus["dy"]) < 12:
            return True
        page.mouse.move(focus["x"], focus["y"])
        page.mouse.wheel(focus["dx"], focus["dy"])
        page.wait_for_timeout(500)
    return True


def zoom_out(page, steps: int) -> None:
    for _ in range(steps):
        page.get_by_role("button", name="Отдалить").click()
        page.wait_for_timeout(150)
    page.wait_for_timeout(500)


def shoot(page, out: Path, name: str, *, height: int | None = None) -> Path:
    """Кадр страницы. Chromium изредка не отдаёт первый.

    `height` — временно вытянуть окно: карточка подтверждения с несколькими
    документами не влезает в обычное, прокручивается внутри, и кнопок решения
    на кадре не видно. Ширина остаётся прежней, так что кадр ложится в ту же
    колонку документации.
    """
    path = out / name
    if height:
        page.set_viewport_size({"width": VIEWPORT["width"], "height": height})
        page.wait_for_timeout(500)
    try:
        return _shoot(page, path)
    finally:
        if height:
            page.set_viewport_size(VIEWPORT)
            page.wait_for_timeout(300)


def _shoot(page, path: Path) -> Path:
    rest(page)
    for attempt in range(3):
        try:
            page.screenshot(path=str(path), animations="disabled")
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1.5)
            continue
        print(f"снимок: {shown(path)}", flush=True)
        return path
    return path


def rest(page) -> None:
    """Увести курсор с последней нажатой кнопки.

    Иначе на кадре остаётся состояние наведения: подсвеченная строка дерева
    с глазком поверх имени файла, всплывающая подсказка.
    """
    page.mouse.move(VIEWPORT["width"] - 4, VIEWPORT["height"] - 4)
    page.wait_for_timeout(300)


def shoot_box(page, locator, out: Path, name: str, pad: int = 14) -> Path:
    """Снимок одной панели с полями вокруг рамки."""
    rest(page)
    box = locator.bounding_box()
    clip = {"x": max(box["x"] - pad, 0), "y": max(box["y"] - pad, 0),
            "width": box["width"] + 2 * pad, "height": box["height"] + 2 * pad}
    path = out / name
    page.screenshot(path=str(path), clip=clip, animations="disabled")
    print(f"снимок: {shown(path)}", flush=True)
    return path


def expand(locator) -> bool:
    """Раскрыть свёрнутый блок, если он есть.

    Кадр важнее раскрытия: неудачный клик не должен ронять съёмку. Упавший
    скрипт закрывает поток, LangGraph отменяет run, а граф НТ честно гасит
    идущий прогон k6. Один снимок не стоит остановленного прогона.
    """
    try:
        locator.click(timeout=5000)
        return True
    except Exception:
        return False
