"""
Проверка связи: доходит ли агент до провайдера модели, до Confluence и до Jira.

Смысл скрипта — отделить «неверно заполнен .env» от «сломан код». Графы падают
на этом невнятно: prep отказывается стартовать без Jira, publish молча
пропускает этап без Confluence, — и по логу прогона не видно, дело в опечатке
в адресе, в правах токена или в том, что учётка ходит не тем способом.

Поэтому каждая проверка — один дешёвый запрос, и на отказе скрипт пробует
второй способ аутентификации. Basic (e-mail плюс токен) живёт в Cloud, Bearer
(персональный токен) — в Server / Data Center; перепутать их легко, а ошибка
выглядит как «нет доступа к пространству», хотя доступ есть.

По умолчанию скрипт ничего не создаёт. Страница на wiki заводится только с
явным --create-page: это запись в общее корпоративное пространство, и делать
её заодно, «раз уж проверяем», нельзя.

    python scripts/smoke_check.py                # только чтение
    python scripts/smoke_check.py --create-page  # плюс тестовая страница
    python scripts/smoke_check.py --skip-llm     # без обращения к модели
    python scripts/smoke_check.py --burst 12     # сколько запросов подряд разрешено
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import replace

from agent import config as cfg
from agent import confluence, jira

OK, FAIL, SKIP = "  OK  ", " FAIL ", " SKIP "


def line(mark: str, title: str, detail: str = "") -> None:
    print(f"[{mark}] {title}" + (f"\n         {detail}" if detail else ""))


def secret(value: str) -> str:
    """Токен в отчёт не попадает: видна только длина и хвост."""
    return f"<{len(value)} символов, ...{value[-4:]}>" if value else "<пусто>"


# --------------------------------------------------------------------------
# Настройки
# --------------------------------------------------------------------------
def show_config() -> None:
    print("=== Что прочитано из .env ===")
    print(f"  LLM              {cfg.env_str('LLM_PROVIDER')} / {cfg.env_str('LLM_MODEL')}")
    print(f"  LLM_API_BASE     {cfg.env_str('LLM_API_BASE') or '<дефолт провайдера>'}")
    print(f"  LLM_API_KEY      {secret(cfg.env_str('LLM_API_KEY'))}")
    print(
        f"  Confluence       {cfg.env_str('CONFLUENCE_BASE_URL') or '<не задан>'}"
        f"  space={cfg.env_str('CONFLUENCE_SPACE_KEY') or '—'}"
        f"  api={cfg.env_str('CONFLUENCE_API_VERSION')}"
    )
    print(
        f"  CONFLUENCE_TOKEN {secret(cfg.env_str('CONFLUENCE_TOKEN'))}"
        f"  email={cfg.env_str('CONFLUENCE_EMAIL') or '<пусто, значит Bearer>'}"
    )
    print(
        f"  Jira             {cfg.env_str('JIRA_BASE_URL') or '<не задан>'}"
        f"  project={cfg.env_str('JIRA_PROJECT_KEY') or '—'}"
        f"  api={cfg.env_str('JIRA_API_PATH')}"
    )
    print(
        f"  JIRA_TOKEN       {secret(cfg.env_str('JIRA_TOKEN'))}"
        f"  email={cfg.env_str('JIRA_EMAIL') or '<пусто, значит Bearer>'}"
    )
    print()


# --------------------------------------------------------------------------
# Модель
# --------------------------------------------------------------------------
def check_llm() -> None:
    from agent.providers import build_llm

    try:
        answer = build_llm().invoke("Ответь одним словом: ок")
    except Exception as exc:  # класс ошибки у каждого клиента свой
        line(FAIL, "LLM: запрос не прошёл", f"{type(exc).__name__}: {exc}")
        return
    text = str(getattr(answer, "content", answer)).strip()
    line(OK, "LLM отвечает", f"ответ: {text[:80]!r}")


# --------------------------------------------------------------------------
# Confluence
#
# Один GET по тому же пути, которым пишет публикация: он разом доказывает и
# аутентификацию, и что пространство видно этой учётке.
# --------------------------------------------------------------------------
def _probe(s: confluence.Settings) -> dict:
    return confluence._call(
        "GET",
        s.api_path,
        s,
        params={"type": "page", "spaceKey": s.space_key, "limit": 1},
    )


def check_confluence() -> confluence.Settings | None:
    if not confluence.is_configured():
        line(SKIP, "Confluence не настроен", "не заданы: " + ", ".join(confluence.missing_vars()))
        return None
    try:
        s = confluence.load_settings()
    except confluence.ConfluenceError as exc:
        line(FAIL, "Confluence: настройки не приняты", str(exc))
        return None

    mode = "Basic (e-mail и токен)" if s.email else "Bearer (персональный токен)"
    try:
        data = _probe(s)
    except confluence.ConfluenceError as exc:
        line(FAIL, f"Confluence недоступен, вход: {mode}", str(exc))
        # Второй способ. Сработает он — дело в CONFLUENCE_EMAIL, а не в правах.
        other = replace(s, email=None if s.email else cfg.env_str("CONFLUENCE_EMAIL") or None)
        if other.email == s.email:
            return None
        try:
            _probe(other)
        except confluence.ConfluenceError:
            return None
        hint = (
            "уберите CONFLUENCE_EMAIL: на этом инстансе токен нужен как Bearer"
            if s.email
            else "задайте CONFLUENCE_EMAIL: здесь нужен Basic"
        )
        line(OK, "...но проходит другой способ входа", hint)
        return other

    found = len(data.get("results") or [])
    line(
        OK,
        f"Confluence отвечает, вход: {mode}",
        f"пространство {s.space_key} видно, страниц в пробной выборке: {found}",
    )
    return s


def create_test_page(s: confluence.Settings) -> None:
    """Заголовок фиксированный: второй прогон обновит страницу, а не размножит."""
    title = f"{cfg.env_str('AGENT_NAME') or 'Orbita'} — проверка связи"
    body = confluence.text_to_storage(
        "Страница создана скриптом scripts/smoke_check.py.\n\n"
        "Она подтверждает, что у токена есть право записи в это пространство. "
        "Удалять можно без последствий: конвейер на неё не ссылается."
    )
    try:
        result = confluence.publish_page(title, body, s)
    except confluence.ConfluenceError as exc:
        line(FAIL, "Страница не создана", str(exc))
        return
    verb = "создана" if result["status"] == "created" else "обновлена"
    line(OK, f"Страница {verb}, версия {result['version']}", result["url"])


# --------------------------------------------------------------------------
# Чтение Confluence инструментами роли
#
# Проверки выше доказывают, что до wiki дотягивается requests. Это не то же
# самое, что «агент туда ходит»: между ними лежат сборка CQL с ограничением по
# пространству, разбор ответа и превращение страницы в плоский текст. Ломается
# обычно как раз здесь — токен принят, а роль получает пустую строку.
#
# Поэтому дальше вызываются те же самые объекты инструментов, которые
# привязаны к ролям в RESEARCH_TOOLS. Ничего похожего, никакой копии логики:
# `.invoke` с теми же аргументами, что положит модель.
# --------------------------------------------------------------------------
def _first_page_id(found: str) -> str | None:
    match = re.search(r"^- \[(\d+)\]", found, re.MULTILINE)
    return match.group(1) if match else None


def head(text: str, limit: int = 400) -> str:
    body = " ".join(text.split())
    return body[:limit] + (" ..." if len(body) > limit else "")


def check_confluence_read(query: str, s: confluence.Settings) -> None:
    from agent.tools import confluence_page, confluence_search

    found = confluence_search.invoke({"query": query})
    line(OK, f"confluence_search({query!r}) вернул {len(found)} символов", head(found, 300))

    page_id = _first_page_id(found)
    if not page_id:
        # Поиск ничего не нашёл — до wiki это всё равно дошло. Чтобы доказать и
        # чтение, берём любую страницу пространства тем же пробным запросом.
        line(
            SKIP, "Поиск не дал страниц", "беру любую страницу пространства, чтобы проверить чтение"
        )
        results = _probe(s).get("results") or []
        if not results:
            line(FAIL, "В пространстве не видно ни одной страницы", "читать нечего")
            return
        page_id = str(results[0].get("id"))

    page = confluence_page.invoke({"page_id": page_id})
    if page.startswith(("страница", "Confluence не настроен")):
        line(FAIL, f"confluence_page({page_id}) не прочитал страницу", page)
        return
    title = page.splitlines()[0] if page else ""
    line(OK, f"confluence_page({page_id}) прочитал страницу: {title}", head(page))


def check_model_calls_tool(query: str) -> None:
    """
    Решающая проверка: умеет ли эндпоинт вызывать инструменты.

    Инструмент можно вызвать из скрипта и получить текст, а роль всё равно не
    сходит никуда — если провайдер не поддерживает tool calling. И это не
    «Confluence не прочитается»: набор инструментов привязывается ко ВСЕМ
    ролям (`nodes.model_for`), поэтому отказ приходит на каждый ход любого
    конвейера, включая те, которым инструменты не нужны.
    """
    from agent.providers import build_llm
    from agent.tools import RESEARCH_TOOLS

    try:
        answer = (
            build_llm()
            .bind_tools(RESEARCH_TOOLS)
            .invoke(f"Найди в Confluence страницы по теме: {query}. Ответ ищи только инструментом.")
        )
    except Exception as exc:
        line(FAIL, "Модель с инструментами: запрос не прошёл", f"{type(exc).__name__}: {exc}")
        return

    calls = getattr(answer, "tool_calls", None) or []
    if not calls:
        line(
            FAIL,
            "Модель не вызвала инструмент",
            f"ответила текстом: {head(str(answer.content), 200)!r}",
        )
        return
    names = ", ".join(f"{c['name']}({c.get('args')})" for c in calls)
    line(OK, "Модель сама вызвала инструмент", names)


# --------------------------------------------------------------------------
# Jira
# --------------------------------------------------------------------------
def check_jira() -> None:
    if not jira.is_configured():
        line(SKIP, "Jira не настроена", "не заданы: " + ", ".join(jira.missing_vars()))
        return
    try:
        s = jira.load_settings()
    except jira.JiraError as exc:
        line(FAIL, "Jira: настройки не приняты", str(exc))
        return

    mode = "Basic (e-mail и токен)" if s.email else "Bearer (персональный токен)"
    variants = [(s, mode)]
    # /rest/api/3 существует только в Cloud: на Data Center тот же токен даёт
    # 404 на каждом запросе при полностью рабочем доступе.
    if s.api_path.endswith("/3"):
        variants.append((replace(s, api_path="/rest/api/2"), f"{mode}, /rest/api/2"))
    if s.email:
        variants.append((replace(s, email=None), "Bearer (персональный токен)"))
        variants.append((replace(s, email=None, api_path="/rest/api/2"), "Bearer, /rest/api/2"))

    for candidate, label in variants:
        try:
            me = jira.call("GET", f"{candidate.api_path}/myself", candidate)
        except jira.JiraError as exc:
            line(FAIL, f"Jira: {label}", str(exc))
            continue
        who = me.get("displayName") or me.get("name") or me.get("emailAddress") or "?"
        line(OK, f"Jira отвечает, вход: {label}", f"учётка: {who}")
        _check_project(candidate)
        return


def _check_project(s: jira.Settings) -> None:
    key = cfg.env_str("JIRA_PROJECT_KEY")
    if not key:
        line(SKIP, "Проект не проверен", "JIRA_PROJECT_KEY пуст")
        return
    try:
        project = jira.call("GET", f"{s.api_path}/project/{key}", s)
    except jira.JiraError as exc:
        line(FAIL, f"Проект {key} недоступен", str(exc))
        return
    line(OK, f"Проект {key} виден", project.get("name", ""))


# --------------------------------------------------------------------------
# Сколько запросов подряд выдерживает контур
#
# Отдельная проверка, потому что вопрос отдельный. «Доходит ли запрос» и
# «сколько таких запросов подряд разрешено» — разные вещи, и вторая всплывает
# только на прогоне: конвейер делает подряд десяток обращений, и защитный шлюз
# отбивает не первое, а четвёртое. Выяснять это прогоном конвейера дорого —
# он платный, — поэтому здесь то же самое одними дешёвыми чтениями.
#
# Ходит проверка тем же транспортом, что и конвейер: та же очередь, та же
# сессия, та же пауза. Иначе она доказывала бы что-то про себя, а не про него.
# --------------------------------------------------------------------------
def burst(count: int) -> None:
    print(f"=== Пачка из {count} чтений подряд ===")
    targets = []
    if confluence.is_configured():
        s = confluence.load_settings()
        targets.append(("Confluence", s.interval_s, lambda s=s: _probe(s)))
    if jira.is_configured():
        s = jira.load_settings()
        targets.append(
            ("Jira", s.interval_s, lambda s=s: jira.call("GET", f"{s.api_path}/myself", s))
        )
    if not targets:
        line(SKIP, "Пачка не отправлена", "ни Confluence, ни Jira не настроены")
        return

    for name, interval, request in targets:
        line(SKIP, f"{name}: пауза между запросами {interval:g} с", "как в конвейере")
        for number in range(1, count + 1):
            started = time.monotonic()
            try:
                request()
            except (confluence.ConfluenceError, jira.JiraError) as exc:
                spent = time.monotonic() - started
                blocked = isinstance(exc, confluence.ConfluenceBlocked | jira.JiraBlocked)
                line(
                    FAIL,
                    f"{name}: запрос {number} из {count} отбит через {spent:.1f} с"
                    + (" (защитный шлюз)" if blocked else ""),
                    str(exc),
                )
                print(f"         предел этого хоста — {number - 1} запросов подряд с такой паузой")
                break
            line(OK, f"{name}: запрос {number} из {count}", f"{time.monotonic() - started:.1f} с")
        else:
            line(OK, f"{name}: все {count} запросов прошли", "паузы достаточно")


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка связи с моделью, Confluence и Jira")
    parser.add_argument(
        "--create-page",
        action="store_true",
        help="создать тестовую страницу в Confluence (это запись на wiki)",
    )
    parser.add_argument("--skip-llm", action="store_true", help="не обращаться к модели")
    parser.add_argument(
        "--read",
        nargs="?",
        const="архитектура",
        metavar="ЗАПРОС",
        help="сходить в Confluence инструментами роли: поиск и чтение страницы",
    )
    parser.add_argument(
        "--burst",
        nargs="?",
        const=12,
        type=int,
        metavar="N",
        help="сколько чтений подряд отправить в каждую систему, чтобы найти предел шлюза",
    )
    args = parser.parse_args()

    show_config()
    print("=== Проверки ===")
    if args.skip_llm:
        line(SKIP, "LLM не проверялся", "--skip-llm")
    else:
        check_llm()
        check_model_calls_tool(args.read or "архитектура")

    settings = check_confluence()
    if settings and args.read:
        check_confluence_read(args.read, settings)
    if args.create_page:
        if settings:
            create_test_page(settings)
        else:
            line(SKIP, "Страница не создавалась", "Confluence не отвечает")
    elif settings:
        print("         пробную страницу создаст --create-page")

    check_jira()
    if args.burst:
        burst(args.burst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
