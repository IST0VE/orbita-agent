"""
Конвейер подготовки задачи: из тикета в Jira — план, задачи и документация.

Четвёртый граф проекта и первый, который сам ходит за данными в чужие системы.
Остальные три работают с тем, что им дали: `graph.py` — с материалами в папке
задачи, `drawio_graph.py` — с выбранной схемой, `jira_graph.py` — с готовым
документом аналитики. Этот начинает с одной строки «надо сделать вот это»,
читает задачу в Jira, решает, чего не знает, ищет это в Confluence и только
потом пишет.

Обычный вход — не ключ задачи, а ссылка на неё: «напиши документацию по задаче
<адрес из адресной строки>».

Отсюда три особенности, которых у остальных нет.

Первая: задачу читает код, а не модель. Узел `ticket` разбирает ссылку
регуляркой, ходит в трекер и кладёт прочитанное в `artifacts` — без единого
вызова модели. Раньше это делала первая роль инструментом, и стоило это лишнего
вызова на каждый прогон: адрес известен из запроса, аргумент у вызова ровно
один, и модель в этом решении ничего не выбирала — она озвучивала найденное
регуляркой. Тот же довод записан в `diagram_roles`, где схему разбирает код:
материал ровно один, он нужен целиком, и читать его моделью значит платить за
вызов, который ничего не решает.

Ключа в запросе может и не быть — тогда узел берёт первое совпадение поиска
и обязательно пишет в документ, что выбор сделал он. Ссылку на ЧУЖОЙ инстанс
Jira он замечает отдельно: с ключом из чужого трекера конвейер прочитал бы из
настроенного совсем другую задачу и не заметил бы этого.

Вторая: публикуется одна страница, а не пять. Остальные конвейеры выпускают по
странице на этап — их этапы читают разные люди и в разное время. Этот читают
подряд и целиком.

Третья: конвейер только читает. Ни задача в Jira, ни страница в Confluence
им не заводятся — примерные задачи остаются черновиком в документе, а сам
документ уезжает через ту же ноду публикации и то же подтверждение оператора,
что у остальных графов. Между черновиком и заведёнными задачами обязан стоять
человек — и стоит: заводит их по проверенному backlog'у другой граф
(`jira_graph.py`), спросив перед этим оператора.

Граф:

  START -> context -> ticket -> intake
        -> gate_gaps -> gaps
        -> gate_research -> research -> (tools -> research)*
        -> gate_plan -> plan -> gate_draft -> draft
        -> remember -> approve -> publish -> END

  START -> no_input -> remember      (читать нечего или нечем)

Что делает каждая нода:

  ticket     читает задачу в Jira по ссылке или ключу из запроса, а без них —
             поиском по тексту; её связанные задачи (PREP_LINKED_ISSUES),
             страницы Confluence по ссылкам из запроса и задач
             (PREP_LINKED_PAGES) и файлы, выбранные оператором. Без модели и
             без денег: адрес
             разбирается кодом. Повторный ход треда в трекер не ходит — задача
             у треда одна и уже прочитана. Прочитанное получает каждая роль;
  intake     разбор прочитанного: что записано, что следует, чего не хватает.
             Инструментов у него нет — читать уже нечего;
  gaps       превращает разбор в список пробелов и в запросы, по которым их
             искать. Стоит между чтением задачи и поиском намеренно: слитые в
             одну роль, они выродились бы в поиск по заголовку тикета;
  research   отрабатывает эти запросы по Confluence и Jira, читает найденное
             и отделяет закрытые пробелы от оставшихся открытыми. Единственная
             роль с инструментами: что искать и сколько на это уйдёт запросов,
             заранее не знает никто, и вот здесь модель решает по-настоящему.
             Выпустив документ, оставляет реестр источников, собранный кодом
             по следам инструментов (`ledger.py`);
  plan       план работ и черновик Jira-задач с критериями приёмки;
  draft      первичная документация: решение в первом приближении;
  gate_*     ворота перед этапом: бюджет и, если включено
             PIPELINE_REQUIRE_APPROVAL, подтверждение документа оператором;
  no_input   отказ вместо прогона;
  remember   запись в долгую память;
  approve    необязательная остановка перед публикацией;
  publish    ОДНА страница со всеми пятью этапами разделами уезжает в цель
             из `publishers.py`. Остальные конвейеры публикуются постранично,
             этот — нет: его пять документов — один разговор от тикета до
             черновика документации (`Pipeline.one_page`). Первой на ней идёт
             первичная документация, за ней план; рабочие этапы и реестр
             источников — ниже, свёрнутыми.

Проверка входа тут строже, чем у остальных: Jira — обязательное условие, а не
украшение. Без неё пять ролей честно отработают по одной строке оператора и
выпустят пять документов из `[TBD]` — оплаченный отказ, который выглядит как
результат. Confluence, наоборот, необязателен: без него инструмент поиска
скажет, что источник недоступен, роль поиска это запишет, а документы соберутся
из того, что есть в задаче. Разница в том, что первое обесценивает весь прогон,
а второе — только один его этап.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph

from agent import (
    changes,
    confluence,
    credentials,
    db,
    evidence,
    inputs,
    jira,
    metrics,
    prep_roles,
    sources,
)
from agent import config as cfg
from agent import graph as common_graph
from agent.runtime import nested


class State(common_graph.State, total=False):
    """Состояние треда плюс то, что известно о прочитанной задаче."""

    # Какой тикет прочитан, откуда взят его ключ и что помешало, если помешало.
    # Полный текст лежит не здесь, а в `artifacts` — сюда попадает только
    # сводка: по ней интерфейс показывает источник, а нода понимает, что на
    # этом ходе ходить в трекер уже не нужно.
    ticket: dict
    # Записи Evidence прочитанного кодом до ролей: `{id: запись с текстом}`
    # (`evidence.py`). Прочитанное инструментами сюда не копируется — оно
    # лежит в ответах инструментов треда, и `evidence.gather` собирает оба.
    evidence: dict
    # Индекс первого сообщения о текущей задаче: ответы инструментов прежней
    # задачи остаются в истории чата, но не подтверждают новый документ.
    evidence_start: int
    # Проверка ссылок каждого документа: `{ключ роли: Report.to_dict()}`.
    citations: dict
    # Изменение, к которому относится тред: задача, id в базе, требования с
    # устойчивыми номерами и то, с чем их сверять на этом ходе (`base`).
    change: dict


PIPELINE = prep_roles.PIPELINE

# Ниже этой длины сообщение без ключа задачи нечем искать. «Сделай экспорт»
# не находит ничего ни в Jira, ни в Confluence, а пять ролей за него платятся.
# Порог намеренно низкий: одной внятной фразы с именами из предметной области
# для поиска достаточно, и отказывать ей не за что.
MIN_TASK_CHARS = 60


def missing_source(state: State, config: RunnableConfig) -> str:
    """
    Причина не начинать прогон, или пустая строка.

    Условий два, и оба про то, что читать. Первое — настроена ли Jira: это
    единственная обязательная интеграция конвейера. Второе — есть ли что ей
    сказать: ключ задачи, достаточно предметная фраза для поиска или файлы,
    приложенные к задаче.

    Следующий ход уже начатого треда проверку проходит: там короткое «перепиши
    план с учётом ответа» — нормальное сообщение, а задача разобрана на прошлом
    ходе и лежит в артефактах.
    """
    absent = jira.missing_vars()
    if absent:
        return (
            "Читать задачу нечем: " + credentials.missing_message(absent) + ". Этот "
            "конвейер начинается с чтения тикета в Jira — без доступа он выпустит "
            "пять документов из `TBD` и возьмёт за них деньги. Прогон остановлен до "
            "первого вызова модели — деньги не потрачены."
        )

    if state.get("artifacts"):
        return ""

    question = sources.question_of(state)
    if jira.find_keys(question) or len(question) >= MIN_TASK_CHARS:
        return ""

    task = sources.task_dir(config)
    if inputs.readable_files(task):
        return ""

    return (
        f"Искать нечего: в сообщении {len(question)} символов, ключа задачи "
        "(например ORB-123) в нём нет, и к задаче не приложено текстовых файлов. "
        "Назовите ключ тикета или опишите задачу так, чтобы её можно было найти "
        "поиском. Прогон остановлен до первого вызова модели — деньги не потрачены."
    )


# --------------------------------------------------------------------------
# Чтение задачи
#
# Единственная нода конвейера, которая ходит в чужую систему без модели, и
# единственное место, где решается, о какой задаче идёт речь. Раньше это делала
# первая роль инструментом, и стоило это лишнего вызова: адрес задачи известен
# из сообщения оператора, аргумент у вызова ровно один, и модель в этом решении
# ничего не решала — она озвучивала то, что уже нашла регулярка.
#
# Тот же довод записан в `diagram_roles`: материал ровно один, он нужен целиком,
# и читать его моделью значит платить за вызов, который ничего не выбирает.
# --------------------------------------------------------------------------
def _fetch(question: str) -> dict:
    """
    Задача по ключу из запроса или по поиску, плюс то, как она была выбрана.

    Ключ назван — читаем его и ничего не выбираем. Ключа нет — берём первое
    совпадение поиска (он отсортирован по свежести) и обязательно записываем,
    что выбор сделан нами: документ, в котором не видно, о какой задаче речь,
    хуже отсутствия документа.
    """
    keys = jira.find_keys(question)
    if keys:
        return {"key": keys[0], "chosen": "key", "extra": keys[1:]}

    found = jira.search(question)
    if not found:
        return {"error": "по тексту запроса не нашлось ни одной задачи", "chosen": "search"}
    return {"key": found[0]["key"], "chosen": "search", "matched": found[0]["summary"]}


def _materials(question: str, config: RunnableConfig, remembered: Sequence[str] = ()) -> dict:
    """
    Файлы, которые оператор дал к задаче: выбранные в интерфейсе или названные
    в запросе. Возвращает имена прочитанных и готовый блок для ролей.

    Файлы читает код, а не роль поиска, по тому же доводу, что и тикет: какие
    это файлы, оператор уже сказал, и модели здесь выбирать нечего. Выбирать
    ЗА оператора код тоже не должен: невыбранные файлы только перечисляются.
    remembered — файлы прошлого хода треда: они остаются материалом задачи,
    если в новом запросе ничего не выбрано и не названо (`sources.materials`).
    """
    found, others = sources.materials(question, config, remembered)
    read = list((found or {}).get("names") or [])
    return {
        "names": read,
        "block": prep_roles.files_block(found, others),
        "evidence": dict((found or {}).get("evidence") or {}),
    }


def _linked(issue: dict) -> list[dict]:
    """
    Связанные задачи — прочитанные кодом, с потолком PREP_LINKED_ISSUES.

    Порядок — от ближайшего: родитель, связи, подзадачи. Задачи сверх потолка
    остаются в списке с пометкой: их назовёт блок, а открыть их сможет роль
    поиска, если понадобятся.
    """
    limit = cfg.prep_linked_issues()
    if limit <= 0:
        return []
    wanted = [(issue.get("parent") or "", "родитель")]
    wanted += [(link["key"], link["relation"]) for link in issue.get("links") or []]
    wanted += [(item["key"], "подзадача") for item in issue.get("subtasks") or []]

    seen = {issue["key"]}
    linked: list[dict] = []
    for key, relation in wanted:
        if not key or key in seen:
            continue
        seen.add(key)
        if sum(not item.get("skipped") for item in linked) >= limit:
            linked.append({"key": key, "relation": relation, "skipped": True})
            continue
        try:
            other, item = sources.read_issue(key)
        except jira.JiraError as exc:
            linked.append({"key": key, "relation": relation, "error": str(exc)})
            continue
        linked.append(
            {
                "key": other["key"],
                "relation": relation,
                "summary": other["summary"],
                "url": other["url"],
                "text": item.text,
                "evidence": item.to_dict(),
            }
        )
    return linked


_URL = re.compile(r"https?://[^\s<>\"'`)\]]+")


def _pages(texts: list[str]) -> list[dict]:
    """
    Страницы Confluence по ссылкам из этих текстов — с потолком PREP_LINKED_PAGES.

    Ссылка на чужой хост не читается: идентификатор 12345 есть в любой вики, и
    по ссылке на чужую конвейер прочитал бы из своей совсем другую страницу.
    """
    limit = cfg.prep_linked_pages()
    if limit <= 0 or confluence.missing_vars():
        return []
    ours = urlsplit(cfg.confluence_base_url()).hostname
    wanted: list[str] = []
    for text in texts:
        for url in _URL.findall(text or ""):
            if urlsplit(url).hostname == ours:
                wanted += [page for page in confluence.find_page_ids(url) if page not in wanted]

    pages: list[dict] = []
    for page_id in wanted:
        if sum(not item.get("skipped") for item in pages) >= limit:
            pages.append({"id": page_id, "skipped": True})
            continue
        try:
            page, item = sources.read_page(page_id)
        except confluence.ConfluenceError as exc:
            pages.append({"id": page_id, "error": str(exc)})
            continue
        pages.append(
            {
                "id": str(page.get("id") or page_id),
                "title": page.get("title") or "",
                "url": page.get("url") or "",
                "truncated": bool(page.get("truncated")),
                "own": item.own,
                "text": item.text,
                "evidence": item.to_dict(),
            }
        )
    return pages


def _texts(issue: dict) -> list[str]:
    """Где в задаче бывают ссылки: описание и комментарии."""
    comments = [item.get("text") or "" for item in issue.get("comments") or []]
    return [issue.get("description") or "", *comments]


def _summary(items: list[dict]) -> list[dict]:
    """Сводка прочитанного без текста: для страницы и реестра, не для модели."""
    return [
        {key: value for key, value in item.items() if key not in ("text", "evidence")}
        for item in items
    ]


def _records(*groups: list[dict]) -> dict:
    """Записи Evidence прочитанного: `{id: запись}` для поля состояния `evidence`."""
    found: dict = {}
    for items in groups:
        for item in items:
            record = item.get("evidence")
            if record:
                found[record["id"]] = record
    return found


def _change(issue: dict) -> dict:
    """
    Изменение по задаче: из базы, если оно там есть, и пустое, если нет.

    Здесь изменение только читается. Заводит и пишет его узел `change` в
    конце хода: прогон, остановленный на середине, не должен оставлять в базе
    изменение без единого документа.

    `base` — с чем сверится план этого треда, `committed` — снимок базы, от
    которого тред пишет (`changes.merge`). Сначала они совпадают; расходятся,
    когда база не приняла итог хода и номера живут в треде.
    """
    base = {"key": issue["key"], "title": issue.get("summary") or "", "id": "",
            "requirements": [], "base": [], "committed": [], "stored": False, "error": ""}
    try:
        found = changes.load(changes.owner(), issue["key"])
    except db.DatabaseUnavailable as exc:
        return {**base, "error": str(exc)}
    if not found:
        return base
    requirements = list(found.get("requirements") or [])
    return {**base, **found, "requirements": requirements, "base": requirements,
            "committed": requirements, "stored": True}


def _unread(picked: dict, change: dict | None) -> dict:
    """
    Изменение треда, когда задача не прочитана: без ключа, номера живут в треде.

    Изменение прошлой задачи здесь не остаётся: иначе план по непрочитанной
    ORB-2 записался бы в изменение ORB-1, прочитанной на прошлом ходе.
    Остаётся только изменение без ключа, начатое для той же непрочитанной
    задачи, — его номера нужны следующему плану этого треда.
    """
    wanted = str(picked.get("key") or "")
    change = change or {}
    if change and not change.get("key") and change.get("wanted", "") == wanted:
        return change
    return {"key": "", "wanted": wanted, "title": "", "id": "", "requirements": [],
            "base": [], "committed": [], "stored": False, "error": ""}


def ticket_node(state: State, config: RunnableConfig) -> dict:
    """
    Прочитать задачу, её связанные задачи и материалы оператора — без вызова модели.

    Узел открывает каждый ход треда, поэтому здесь же снимается пометка
    `settled`: требования, сверенные планом прошлого хода, — итог того хода.
    Ход, не дошедший до узла `change` (остановка, бюджет, брошенная пауза),
    оставил бы их «сверенными», и узел `change` этого хода записал бы в
    базу старый список, даже если новый план о требованиях молчит.
    """
    update = _read_ticket(state, config)
    old_ticket = state.get("ticket") or {}
    new_ticket = update.get("ticket") or {}
    old_source = str(old_ticket.get("key") or old_ticket.get("wanted") or "")
    new_source = str(new_ticket.get("key") or new_ticket.get("wanted") or old_source)
    if new_ticket and new_source != old_source:
        update = {**update, "evidence_start": len(state.get("messages") or []),
                  "citations": {}}
    change = update.get("change", state.get("change"))
    if change and change.get("settled"):
        update = {**update, "change": {**change, "settled": False}}
    return update


def _read_ticket(state: State, config: RunnableConfig) -> dict:
    """
    Чтение задачи для `ticket_node`.

    Повторный ход треда в трекер не ходит: задача у треда одна, она уже
    прочитана, и переспрашивать её на каждое «перепиши план» значит платить
    задержкой за данные, которые не менялись. Кеш тут ровно такой — ключ и
    текст в состоянии треда; за свежестью тикета следит оператор, начиная
    новый тред. Локальные файлы читаются заново на каждом ходе: содержимое
    и папка могут измениться при прежнем имени файла.

    Отказ не останавливает конвейер: с непрочитанной задачей остаётся запрос
    оператора и приложенные файлы, а разбор обязан начать с того, что задача
    не прочитана, и не выдавать за неё сообщение оператора.
    """
    question = sources.question_of(state)
    known = state.get("ticket") or {}
    materials = _materials(question, config, known.get("files") or [])
    files = {prep_roles.FILES: materials["block"]}
    if known.get("key") and known["key"] in (jira.find_keys(question) or [known["key"]]):
        previous = (state.get("artifacts") or {}).get(prep_roles.FILES) or ""
        if materials["names"] == (known.get("files") or []) and materials["block"] == previous:
            return {}
        return {
            "ticket": {**known, "files": materials["names"]},
            "artifacts": files,
            "evidence": {
                **{key: value for key, value in (state.get("evidence") or {}).items()
                   if value.get("system") != "file"},
                **materials["evidence"],
            },
        }

    picked = _fetch(question)
    if picked.get("error") is None:
        try:
            issue, record = sources.read_issue(picked["key"])
        except jira.JiraError as exc:
            picked = {**picked, "error": str(exc), "reason": f"{picked['key']} не прочитана: {exc}"}
    if picked.get("error"):
        # Без задачи остаются запрос оператора, его файлы и страницы по ссылкам
        # из запроса: разбор начнёт с того, что задача не прочитана. Изменения
        # без задачи нет: заводить его не по чему, и номера требований живут
        # в треде (`changes.describe`).
        pages = _pages([question])
        reason = picked.pop("reason", picked["error"])
        change = _unread(picked, state.get("change"))
        return {
            "ticket": {**picked, "files": materials["names"], "pages": _summary(pages)},
            "artifacts": {
                prep_roles.TICKET: f"Задача не прочитана: {reason}.",
                prep_roles.PAGES: prep_roles.pages_block(pages),
                prep_roles.CHANGE: changes.block(change),
                **files,
            },
            "evidence": {**_records(pages), **materials["evidence"]},
            "change": change,
            "messages": [AIMessage(content=f"Задача не прочитана: {reason}.")],
            "stage": "ticket",
        }

    linked = _linked(issue)
    pages = _pages([question, *_texts(issue), *(item.get("text") or "" for item in linked)])
    # Изменение треда — по задаче. Тот же ключ, что на прошлом ходе, — то же
    # изменение: его номера требований уже в состоянии, и база не нужна.
    change = state.get("change") or {}
    if change.get("key") != issue["key"]:
        change = _change(issue)
    return {
        "ticket": {
            "key": issue["key"],
            "url": issue["url"],
            "summary": issue["summary"],
            "chosen": picked["chosen"],
            # Id записи Evidence самой задачи: критик относит к ней утверждения
            # о комментариях, в которых задача не названа (`critic.py`).
            "evidence": record.id,
            # Сводка без текста: по ней страница называет прочитанное, а реестр
            # источников считает его (`prep_roles.subject`, `prep_roles.ledger`).
            "linked": _summary(linked),
            "pages": _summary(pages),
            "files": materials["names"],
        },
        "artifacts": {
            prep_roles.TICKET: prep_roles.ticket_block(issue, picked, question, record.to_dict()),
            prep_roles.LINKED: prep_roles.linked_block(issue["key"], linked),
            prep_roles.PAGES: prep_roles.pages_block(pages),
            prep_roles.CHANGE: changes.block(change),
            **files,
        },
        "evidence": {
            record.id: record.to_dict(),
            **_records(linked, pages),
            **materials["evidence"],
        },
        "change": change,
        "messages": [
            AIMessage(content=_note(issue, picked, linked, materials["names"], pages))
        ],
        "stage": "ticket",
    }


def _plain(items: list[dict]) -> list[dict]:
    """Требования без пометки хода (`change`): то, что лежит у изменения."""
    return [{key: value for key, value in item.items() if key != "change"} for item in items]


def change_node(state: State, config: RunnableConfig) -> dict:
    """
    Записать изменение: требования с номерами, прочитанное и тред — без модели.

    Стоит после последней роли и до памяти и публикации (`Pipeline.postlude`):
    если база, сверив номера под блокировкой, выдала другие — соседний тред
    успел сохранить своё, — план и документация переписываются, и на страницу
    уезжают окончательные номера. Остановленный на середине прогон сюда не
    доходит: изменения без документов в базе быть не должно.

    Отказ базы прогон не останавливает. Изменение остаётся в треде, номера
    устойчивы в его пределах, а строка под задачей говорит, что не сохранено.
    Снимок базы (`committed`) тогда не меняется: номера, выданные в треде,
    так и остаются номерами треда, пока их не подтвердит база (`changes.merge`).
    Во вложенном прогоне не пишет вовсе: запись — дело вызывающего графа
    (`graph_registry`, эффект `change`).

    Пометка `settled` снимается здесь же: требования сверены планом этого
    хода, и следующий ход пишет только то, что сверит его собственный план.
    """
    change = dict(state.get("change") or {})
    settled = change.pop("settled", False)
    requirements = change.get("requirements") if settled else None
    # Без записи в базу итог хода становится тем, с чем сверится следующий
    # план: иначе он начал бы нумерацию с R-1.
    kept = _plain(requirements if requirements is not None else change.get("base") or [])
    if not change.get("key"):
        if requirements is None:
            return {}
        change["base"] = kept
        return {"change": change, "artifacts": {prep_roles.CHANGE: changes.block(change)}}
    if nested(config):
        change.update(
            {"base": kept, "stored": False,
             "error": "вложенный прогон — изменение записывает вызывающий граф"}
        )
        return {"change": change}
    items = evidence.gather(
        state.get("evidence"),
        (state.get("messages") or [])[state.get("evidence_start", 0):],
    )
    try:
        saved = changes.save(
            owner=changes.owner(),
            key=change["key"],
            title=change.get("title") or "",
            thread_id=str(((config or {}).get("configurable") or {}).get("thread_id") or ""),
            graph=PIPELINE.key,
            requirements=requirements,
            evidence=[item.to_dict(text=False) for item in items.values()],
            committed=change.get("committed") or [],
        )
    except db.DatabaseUnavailable as exc:
        change.update({"base": kept, "stored": False, "error": str(exc)})
        return {"change": change, "artifacts": {prep_roles.CHANGE: changes.block(change)}}

    change.update(
        {
            "id": saved["id"],
            "requirements": saved["requirements"],
            "base": _plain(saved["requirements"]),
            "committed": _plain(saved["requirements"]),
            "stored": True,
            "error": "",
            "notes": [*(change.get("notes") or []), *saved.get("notes", [])],
        }
    )
    artifacts = {prep_roles.CHANGE: changes.block(change)}
    remap = saved.get("remap") or {}
    if remap:
        current = state.get("artifacts") or {}
        for key in ("plan", "draft"):
            if current.get(key):
                artifacts[key] = changes.renumber(current[key], remap)
    return {"change": change, "artifacts": artifacts}


def _note(
    issue: dict,
    picked: dict,
    linked: list[dict] = (),
    files: list[str] = (),
    pages: list[dict] = (),
) -> str:
    """Что прочитано — оператору в тред. Ход по графу должен быть виден."""
    how = (
        "ключ назван в запросе"
        if picked["chosen"] == "key"
        else f"ключ не назван, выбрана поиском по совпадению «{picked.get('matched', '')}»"
    )
    note = (
        f"Прочитана задача {issue['key']} «{issue['summary']}» "
        f"({issue.get('type') or 'без типа'}, {issue.get('status') or 'без статуса'}), "
        f"комментариев {len(issue.get('comments') or [])} — {how}. "
        f"{issue['url']}"
    )
    read = [item["key"] for item in linked if not item.get("error") and not item.get("skipped")]
    if read:
        note += " Связанные задачи прочитаны: " + ", ".join(read) + "."
    failed = [item["key"] for item in linked if item.get("error")]
    if failed:
        note += " Не открылись: " + ", ".join(failed) + "."
    read = [item["id"] for item in pages if not item.get("error") and not item.get("skipped")]
    if read:
        note += " Страницы Confluence по ссылкам прочитаны: " + ", ".join(read) + "."
    if files:
        note += " Материалы оператора: " + ", ".join(files) + "."
    return note


def build_graph(llm: Any = None) -> StateGraph:
    """
    Собрать конвейер по описанию из `prep_roles.py`.

    Узлы, ворота и маршруты — те же фабрики, что у остальных конвейеров; им
    передаётся другое описание ролей, свой набор инструментов и своя проверка
    входа, и этого достаточно.

    llm — готовая модель вместо собранной из окружения. Нужна тестам, чтобы
    прогнать граф целиком на подделке, без ключей и без сети.
    """
    return common_graph.build_graph(
        llm=llm,
        pipeline=PIPELINE,
        admission=missing_source,
        prelude=ticket_node,
        postlude=change_node,
        # Без этого поле `ticket` в состояние не попадает: LangGraph
        # выбрасывает из обновления ключи, которых нет в схеме, и узел чтения
        # ходил бы в трекер на каждом ходе треда заново.
        state_schema=State,
    )


# Для Studio / langgraph dev: компилируем БЕЗ чекпоинтера, как и остальные графы.
graph = metrics.observe(build_graph().compile(), "prep")
