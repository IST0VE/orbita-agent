"""Роли конвейера: задача в Jira -> план, черновик задач и первичная документация."""

from __future__ import annotations

from agent import config as cfg
from agent import jira, prep_prompts, tools
from agent import ledger as registry
from agent.pipeline import Pipeline, Role, Stage

# Ключи в state["artifacts"], которые пишет не роль, а код. Лежат там же, где
# документы этапов, потому что подставляются ровно так же, — и потому что
# `Pipeline.done()` перебирает роли, а не ключи, и отдельными разделами на
# страницу публикации они не попадают.
#
# TICKET, LINKED, PAGES и FILES пишет нода чтения задачи
# (`prep_graph.ticket_node`) до первой роли, SOURCES — узел роли поиска, когда
# она выпустила документ (`Pipeline.ledger`).
TICKET = "ticket"
LINKED = "linked"
PAGES = "pages"
FILES = "files"
SOURCES = "sources"

# Всё, что код прочитал до ролей, получает каждая роль. 27 сентября 2026 его
# видел только разбор: стенограмму встречи, выбранную оператором, прочитала
# одна роль поиска, и разбор объявил «главной находкой» расхождение заголовка
# с описанием, которое стенограмма объясняла в первых же репликах. Пробелы, план
# и документация тикета не видели вовсе и тянули за собой любую ошибку разбора.
READ_BY_CODE = (TICKET, LINKED, PAGES, FILES)

ROLES: tuple[Role, ...] = (
    Role(
        key="intake",
        number="01",
        title="Разбор задачи",
        summary="Что задача значит по тикету, связанным задачам и материалам оператора",
        # Инструментов у разбора нет намеренно. Задачу читает код: её адрес
        # известен из запроса оператора, аргумент у вызова ровно один, и модель
        # в этом решении ничего не выбирала — она повторяла найденное
        # регуляркой и брала за это деньги как за отдельный вызов.
        needs=READ_BY_CODE,
    ),
    Role(
        key="gaps",
        number="02",
        title="Что нужно выяснить",
        summary="Пробелы, запросы для поиска и вопросы к людям",
        needs=(*READ_BY_CODE, "intake"),
    ),
    Role(
        key="research",
        number="03",
        title="Что уже есть",
        summary="Найденная документация, выжимки со ссылками и оставшиеся пробелы",
        # Единственная роль с инструментами, и в отличие от разбора — по делу:
        # что именно искать, заранее неизвестно, запросы к Confluence сочиняет
        # предыдущий этап, а сколько их понадобится, решается по ходу.
        #
        # Вход ей собирается тем же `brief()`, что и остальным (`briefed`):
        # переписка хода, которую она получала раньше, не содержала ни тикета,
        # ни связанных задач, ни материалов оператора — код читает их мимо
        # переписки.
        needs=(*READ_BY_CODE, "intake", "gaps"),
        reads_files=True,
        briefed=True,
    ),
    Role(
        key="plan",
        number="04",
        title="План работ и примерные задачи",
        summary="Порядок работ и черновик Jira-декомпозиции с критериями приёмки",
        needs=(*READ_BY_CODE, "intake", "gaps", "research", SOURCES),
    ),
    Role(
        key="draft",
        number="05",
        title="Первичная документация",
        summary="Черновик страницы: решение в первом приближении и открытые вопросы",
        needs=(*READ_BY_CODE, "intake", "gaps", "research", "plan", SOURCES),
    ),
)

BY_KEY = {role.key: role for role in ROLES}
KEYS = tuple(role.key for role in ROLES)
FIRST = ROLES[0]
LAST = ROLES[-1]


def prompt_for(key: str) -> str:
    return prep_prompts.for_role(key)


def ticket_block(issue: dict, picked: dict, question: str) -> str:
    """
    Прочитанная задача в том виде, в каком её увидит разбор.

    К самому тикету добавляется то, чего в нём нет: как он был выбран и не
    ведёт ли ссылка оператора в другой трекер. И то и другое — предупреждения
    о том, что прочитана может быть не та задача, а заметить это способен
    только человек, читающий документ.
    """
    parts = [jira.format_issue(issue)]

    if picked.get("chosen") == "search":
        parts.append(
            "ВНИМАНИЕ: ключ задачи в запросе не назван. Эта задача выбрана поиском по "
            "тексту запроса как первое совпадение. Проверь, та ли это задача, и скажи "
            "об этом в первой строке документа."
        )
    if picked.get("extra"):
        parts.append(
            "В запросе упомянуты и другие задачи: "
            + ", ".join(picked["extra"])
            + ". Прочитана только первая; остальные упомяни как связанные."
        )

    foreign = jira.foreign_hosts(question)
    if foreign:
        parts.append(
            "ВНИМАНИЕ: ссылка в запросе ведёт на " + ", ".join(foreign) + ", а задача "
            "прочитана из настроенного трекера. Это может быть совсем другая задача с "
            "тем же ключом. Сверь заголовок с тем, что ожидал оператор, и скажи о "
            "расхождении в документе."
        )
    return "\n\n".join(parts)


def recognised(task: str) -> str:
    """
    Что распознано во входе до первого вызова модели: ключи задач и чужие хосты.

    Разбор ссылки — работа регулярки, а не модели. Обычный вход конвейера это
    «напиши документацию по задаче <ссылка>», и просить модель вычленить ключ
    из адреса значит платить за это токенами и иногда получать `BROWSE-1`.
    Распознанное уезжает готовой строкой, а модель занимается тем, ради чего
    её звали.

    Блок ставится рядом с запросом оператора, то есть в КОНЕЦ сообщения: он
    меняется от треда к треду, и в префиксе ему делать нечего.
    """
    lines = []
    keys = jira.find_keys(task)
    if keys:
        lines.append("- ключи задач Jira: " + ", ".join(keys))
    foreign = jira.foreign_hosts(task)
    if foreign:
        lines.append(
            "- внимание: ссылка ведёт на " + ", ".join(foreign) + ", а настроен другой "
            "адрес Jira. Ключ будет прочитан из настроенного трекера — это может "
            "оказаться совсем другая задача. Проверь, что прочитанное совпадает с "
            "запросом, и скажи об этом расхождении в документе."
        )
    return "\n\nРаспознано во входе:\n" + "\n".join(lines) if lines else ""


def linked_block(main: str, linked: list[dict]) -> str:
    """
    Связанные задачи, прочитанные кодом, — для брифа каждой роли.

    Связь пишется целиком, с ключом задачи прогона: «ORB-1 blocks ORB-7»
    понятно без справки о том, в какую сторону смотрит связь.
    """
    if not linked:
        return ""
    parts: list[str] = []
    skipped: list[str] = []
    for item in linked:
        relation = f"Связь: {main} {item['relation']} {item['key']}."
        if item.get("skipped"):
            skipped.append(f"{item['key']} ({item['relation']})")
        elif item.get("error"):
            parts.append(f"## {item['key']}\n\n{relation}\nНе прочитана: {item['error']}")
        else:
            parts.append(f"## {item['key']}\n\n{relation}\n\n{item['text']}")
    if skipped:
        parts.append(
            "Не читались — достигнут потолок PREP_LINKED_ISSUES: "
            + ", ".join(skipped)
            + ". Их можно открыть на этапе поиска инструментом jira_issue."
        )
    return "\n\n".join(parts)


def pages_block(pages: list[dict]) -> str:
    """Страницы Confluence по ссылкам из запроса и задачи, прочитанные кодом."""
    parts: list[str] = []
    skipped: list[str] = []
    for item in pages:
        if item.get("skipped"):
            skipped.append(item["id"])
        elif item.get("error"):
            parts.append(f"## [WIKI {item['id']}]\n\nНе прочитана: {item['error']}")
        else:
            parts.append(f"## [WIKI {item['id']}] {item['title']}\n\n{item['text']}")
    if skipped:
        parts.append(
            "Не читались — достигнут потолок PREP_LINKED_PAGES: "
            + ", ".join(skipped)
            + ". Их можно открыть на этапе поиска инструментом confluence_page."
        )
    return "\n\n".join(parts)


def files_block(found: dict | None, others: list[str]) -> str:
    """
    Материалы оператора: выбранные файлы в пределах лимита и список остальных.

    Читаются только выбранные оператором или названные в его запросе: выбирать
    вход за оператора конвейер не должен. Остальные файлы чата названы, и роль
    поиска может открыть их инструментом, если выбранного не хватит.
    """
    parts: list[str] = []
    if found and found.get("error"):
        parts.append(f"ВНИМАНИЕ: материалы оператора не прочитаны — {found['error']}.")
    elif found:
        parts.append(
            "Оператор выбрал для задачи: "
            + ", ".join(found["names"])
            + ". Файлы прочитаны кодом и приведены ниже; ссылайся на них тегом `[ФАЙЛ имя]`."
        )
        parts += [f"## Файл {name}\n\n{text}" for name, text in found["each"].items()]
        if found.get("skipped"):
            parts.append("Не прочитаны: " + ", ".join(found["skipped"]) + ".")
    if others:
        parts.append(
            "Другие файлы чата, оператором не выбранные: "
            + ", ".join(others)
            + ". Их можно прочитать на этапе поиска инструментом read_task_file, "
            "если выбранного не хватает."
        )
    return "\n\n".join(parts)


# Блоки, которые пишет код, а не роль: заголовок в брифе и что сказать,
# если блока нет. Пустота называется словами — модель, не увидевшая раздела,
# читает это как «не загрузили», а не как «нет».
_CODE_BLOCKS = {
    TICKET: (
        "Задача из Jira",
        "Задача не прочитана. Начни документ с этого и работай только с запросом "
        "оператора и его материалами. Не выдавай запрос оператора за содержание тикета.",
    ),
    LINKED: (
        "Связанные задачи из Jira",
        "Связанных задач у тикета нет, или их не читали (PREP_LINKED_ISSUES=0).",
    ),
    PAGES: (
        "Страницы Confluence по ссылкам",
        "Ссылок на страницы Confluence в запросе и задаче нет.",
    ),
    FILES: (
        "Материалы оператора",
        "Оператор не приложил к задаче материалов: всё известное — в задаче и в запросе.",
    ),
    SOURCES: (
        "Реестр источников прогона",
        "Реестр не собран: этап поиска не выпустил документ. Что прочитано, видно "
        "только из документа этапа 03.",
    ),
}


def brief(role: Role, task: str, artifacts: dict | None) -> str:
    """
    Запрос оператора, прочитанное кодом и неизменённые результаты предыдущих этапов.

    Неизменённые — принципиально. Роль поиска отрабатывает список запросов,
    который составила роль пробелов; роль плана опирается на выжимки со
    ссылками. Пересказ любого из этих документов ломает связь с источником:
    ссылку на страницу wiki нельзя «примерно передать».
    """
    artifacts = artifacts or {}
    parts = [f"# Запрос оператора\n\n{task.strip()}{recognised(task)}"]
    if role.reads_files:
        limit = cfg.tool_turns_per_run()
        parts.append(
            "# Инструменты\n\n"
            + (
                f"На этот этап — {limit} ходов с инструментами (TOOL_TURNS_PER_RUN). "
                if limit > 0
                else "Потолка ходов с инструментами нет. "
            )
            + "В одном ходе можно сделать несколько вызовов сразу."
        )
    for key in role.needs:
        # Прочитанное кодом стоит перед документами этапов: внутри треда оно
        # неизменно, а документы копятся от этапа к этапу — стабильное ближе
        # к началу, растущее в хвост.
        if key in _CODE_BLOCKS:
            title, missing = _CODE_BLOCKS[key]
            found = (artifacts.get(key) or "").strip()
            parts.append(f"# {title}\n\n{found or missing}")
            continue
        source = BY_KEY[key]
        text = (artifacts.get(key) or "").strip()
        if text:
            parts.append(f"# Результат этапа {source.number}. {source.title}\n\n{text}")
        else:
            parts.append(
                f"# Результат этапа {source.number}. {source.title}\n\n"
                "Этап не выполнен. Не маскируй отсутствие данных: перечисли, "
                "что невозможно подтвердить, и пометь связанные поля TBD."
            )
    return "\n\n".join(parts)


def subject(state: dict) -> str:
    """Что прочитано — строкой под задачей на общей странице."""
    ticket = state.get("ticket") or {}
    lines = []
    if ticket.get("key") and not ticket.get("error"):
        lines.append(
            f"Задача Jira: {ticket['key']} «{ticket.get('summary') or ''}» — {ticket.get('url') or ''}"
        )
    linked = [
        item["key"]
        for item in ticket.get("linked") or []
        if not item.get("error") and not item.get("skipped")
    ]
    if linked:
        lines.append("Связанные задачи прочитаны: " + ", ".join(linked))
    pages = [
        item["id"]
        for item in ticket.get("pages") or []
        if not item.get("error") and not item.get("skipped")
    ]
    if pages:
        lines.append("Страницы Confluence по ссылкам прочитаны: " + ", ".join(pages))
    if ticket.get("files"):
        lines.append("Материалы оператора: " + ", ".join(ticket["files"]))
    return "\n\n".join(lines)


def ledger(state: dict, messages: list) -> dict:
    """
    Реестр источников прогона для `artifacts`: прочитанное кодом и ролью поиска.

    Прочитанное кодом берётся из сводки ноды чтения (`state["ticket"]`), а не
    из текста блоков: текст — для модели, сводка — для учёта. Следы инструментов
    уже сохраняются в сообщениях треда, поэтому учитываем и прошлые ходы:
    правка формулировок без новых запросов не должна удалять источники.
    """
    ticket = state.get("ticket") or {}
    prefetched: list[dict] = []
    if ticket.get("key"):
        prefetched.append(
            {
                "system": "jira",
                "key": ticket["key"],
                "title": ticket.get("summary") or "",
                "url": ticket.get("url") or "",
                "how": "задача прогона, прочитана кодом",
                "error": ticket.get("error") or "",
            }
        )
    for item in ticket.get("linked") or []:
        prefetched.append(
            {
                "system": "jira",
                "key": item["key"],
                "title": item.get("summary") or "",
                "url": item.get("url") or "",
                "how": f"связанная задача ({item['relation']}), прочитана кодом",
                "error": "потолок PREP_LINKED_ISSUES" if item.get("skipped")
                else item.get("error") or "",
            }
        )
    for item in ticket.get("pages") or []:
        prefetched.append(
            {
                "system": "confluence",
                "id": item["id"],
                "title": item.get("title") or "",
                "url": item.get("url") or "",
                "how": "страница по ссылке из запроса или задачи, прочитана кодом"
                + ("; текст обрезан по CONFLUENCE_READ_MAX_CHARS" if item.get("truncated") else ""),
                "error": "потолок PREP_LINKED_PAGES" if item.get("skipped")
                else item.get("error") or "",
            }
        )
    for name in ticket.get("files") or []:
        prefetched.append(
            {
                "system": "file",
                "name": name,
                "title": "материал оператора",
                "how": "выбран оператором, прочитан кодом",
            }
        )
    history = state.get("messages") or messages
    return {SOURCES: registry.render(registry.collect(history), prefetched)}


PIPELINE = Pipeline(
    key="prep",
    title="Подготовка задачи",
    summary=(
        "Задача из Jira превращается в план работ, черновик задач и первичную документацию."
    ),
    byline="конвейером подготовки задачи",
    roles=ROLES,
    prompt_for=prompt_for,
    brief=brief,
    # Единственный конвейер со своим набором инструментов: остальные читают
    # только папку задачи, этот — ещё Jira и Confluence. Набор один на все пять
    # ролей, включая те, что в цикл с инструментами не уходят: он уезжает в
    # кешируемый префикс, и роль без привязки послала бы запрос другой формы.
    tools=tuple(tools.RESEARCH_TOOLS),
    # Пять документов этого конвейера — один разговор от тикета до черновика
    # документации, и читают его подряд. Постранично их получал человек,
    # который просил «страницу по задаче», и закрывал четыре из пяти не читая.
    one_page=True,
    # Без доступа к трекеру конвейер выпустит пять документов из `[TBD]`
    # и возьмёт за них деньги. Поэтому вход проверяется до первого вызова.
    admission=True,
    prelude=Stage(
        key="ticket",
        title="Чтение задачи",
        summary=(
            "Без вызова модели читает задачу в Jira по ссылке или ключу из запроса, "
            "её связанные задачи и файлы, выбранные оператором."
        ),
    ),
    tools_hint="Поиск и чтение страниц Confluence, связанные задачи Jira, файлы задачи.",
    # На странице читают итог: первичную документацию с её «Коротко», потом
    # план. Разбор, пробелы и выжимки нужны, чтобы проверить вывод, и лежат
    # за ними свёрнутыми.
    lead=("draft", "plan"),
    collapsed=("intake", "gaps", "research"),
    appendix=((SOURCES, "Источники прогона"),),
    subject=subject,
    ledger=ledger,
    tidy=True,
    russian=True,
)
