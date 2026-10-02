"""
Конвейер декомпозиции: из документа аналитики — задачи в Jira.

Третий граф проекта и единственный, который что-то создаёт в чужой системе.
Первый (`graph.py`) читает материалы задачи и пишет по ним проектную аналитику,
второй (`drawio_graph.py`) описывает нарисованную систему по схеме, четвёртый
(`prep_graph.py`) читает задачу в трекере и пишет по ней план. Все они
заканчиваются документом. Этот заканчивается ключами заведённых задач: документ
для него — не результат, а заготовка.

Вход — документ, а не просьба: файл в папке задачи или ссылка на страницу
Confluence. Аналитику мог написать другой человек и в другом инструменте,
поэтому это отдельный граф, а не шестая роль первого: его вход не обязан быть
нашим выходом.

Спрашивают у оператора ровно одно — проект, в котором заводить задачи. Всё
остальное выводится из документа, и спрашивать это значит перекладывать на
человека работу, ради которой конвейер и написан.

Граф:

  START -> context -> source -> scope -> gate_backlog -> backlog
        -> gate_review -> review -> gate_issues -> issues
        -> create -> remember -> approve -> publish -> END

  START -> no_input -> remember      (читать нечего)

Что делает каждая нода:

  source     читает источник: страницу Confluence по ссылке из запроса или
             текстовые файлы папки задачи. Без модели и без денег — адрес
             разбирается кодом, а материал нужен всем этапам одинаковый;
  scope      карта реализации: границы, сервисы, компоненты, слои;
  backlog    иерархия Epic/Story/Task с критериями приёмки и зависимостями;
  review     проверка декомпозиции против карты и финальная её версия;
  issues     тот же финальный backlog машинной формой — блок JSON, который
             разбирает `jira_plan.py`. Отдельный этап, потому что между
             текстом для людей и запросом в трекер обязан стоять разбор кодом;
  create     заводит задачи и возвращает ключи и ссылки. Единственный узел
             проекта с внешним побочным эффектом сильнее публикации страницы,
             и поэтому единственный, который по умолчанию спрашивает человека;
  gate_*     ворота перед этапом: бюджет и, если включено
             PIPELINE_REQUIRE_APPROVAL, подтверждение документа оператором;
  no_input   отказ вместо прогона: раскладывать нечего, платить не за что;
  remember   запись в долгую память;
  approve    необязательная остановка перед публикацией;
  publish    документы этапов уезжают в цель из `publishers.py`.

Почему `create` стоит ДО публикации: заведённые ключи должны попасть в
документ, который уедет на wiki, и в память хода. Иначе о них знал бы только
тот, кто смотрел в экран во время прогона.

Почему проверка входа тут есть, а у конвейера аналитики её нет: там материалом
может быть само сообщение оператора, и пустого входа не бывает. Здесь вход —
чужой документ, и его отсутствие — самый обычный исход: выбрали не ту папку,
забыли приложить файл. Без проверки граф отвечает на это четырьмя вызовами
модели и backlog'ом из `TBD`, то есть оплаченным отказом.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph
from langgraph.types import interrupt

from agent import (
    actions,
    confluence,
    credentials,
    drafts,
    inputs,
    jira,
    jira_journal,
    jira_plan,
    jira_roles,
    jira_writer,
    metrics,
    proposals,
    sources,
)
from agent import config as cfg
from agent import graph as common_graph
from agent.runtime import nested

PIPELINE = jira_roles.PIPELINE

# Ниже этой длины сообщение оператора не может быть аналитикой: столько занимает
# просьба разложить её, а не она сама. Порог намеренно щедрый — короткая, но
# настоящая аналитика через него проходит, а «сделай задачи по вчерашней
# встрече» не проходит, и это единственное, что от него требуется.
MIN_ANALYSIS_CHARS = 400


class State(common_graph.State, total=False):
    """Состояние треда плюс то, что известно об источнике и о заведённых задачах."""

    # Что прочитано на входе: вид источника, имя или адрес, объём, причина
    # отказа. Полный текст лежит не здесь, а в `artifacts` — сюда попадает
    # только сводка: по ней интерфейс показывает источник, а нода понимает,
    # что на этом ходе читать заново уже не нужно.
    source: dict
    # Чем кончилось заведение задач: статус, проект, созданные ключи со
    # ссылками, отказы по отдельным карточкам и предупреждения разбора.
    # Перезаписывается целиком: на каждом ходе заводится своя пачка.
    issues: dict


class Options(common_graph.Options, total=False):
    """Переопределения на тред: те же, что у конвейера аналитики, плюс проект."""

    # Ключ проекта Jira, в котором заводить задачи. Свойство хода, а не
    # процесса: у соседнего треда своя аналитика и свой проект. Пусто —
    # берётся JIRA_PROJECT_KEY, а если пусто и там, проект спрашивается
    # у оператора остановкой в ноде `create`.
    jira_project: str


def missing_analysis(state: State, config: RunnableConfig) -> str:
    """
    Причина не начинать прогон, или пустая строка.

    Источников три — ссылка на страницу Confluence, файл в папке задачи и сам
    текст сообщения, — и достаточно любого. Четвёртый случай: следующий ход уже
    начатого треда, где документ разобран на прошлом ходе; там короткое
    «перепиши задачу 3» — нормальное сообщение, а не пустой вход.

    Файл, выбранный оператором в интерфейсе, отвечает на вопрос раньше всех
    остальных проверок — и раньше ссылки на Confluence. Иначе прогон с готовым
    документом в папке заворачивало бы сообщение «не заданы CONFLUENCE_*»
    только потому, что в тексте оператора попалась ссылка на wiki.
    """
    if state.get("artifacts"):
        return ""

    wanted = sources.picked_files(config)
    if wanted:
        task = sources.task_dir(config)
        names = inputs.readable_files(task)
        lost = [name for name in wanted if name not in names]
        if not lost:
            return ""
        # Пропал один файл из комплекта — отказ такой же, как если бы пропали
        # все: прочитать три документа из четырёх и разложить их как весь вход
        # значит выдать неполный backlog за полный.
        many = len(lost) > 1
        return (
            f"{'Выбраны файлы' if many else 'Выбран файл'} "
            + ", ".join(lost)
            + f", но в файлах чата {'их' if many else 'его'} нет или "
            + ("они не читаются" if many else "он не читается")
            + ". Выберите другой файл или снимите выбор, чтобы конвейер прочитал "
            "все файлы чата. Прогон остановлен до первого вызова модели — деньги "
            "не потрачены."
        )

    question = sources.question_of(state)
    if confluence.find_page_ids(question):
        absent = confluence.missing_vars()
        if not absent:
            return ""
        return (
            "В запросе есть ссылка на страницу Confluence, но читать её нечем: "
            + credentials.missing_message(absent)
            + ". Или загрузите документ файлом в чат. "
            "Прогон остановлен до первого вызова модели — деньги не потрачены."
        )

    if len(question) >= MIN_ANALYSIS_CHARS:
        return ""

    task = sources.task_dir(config)
    if inputs.readable_files(task):
        return ""

    return (
        f"Раскладывать нечего: в сообщении {len(question)} символов, ссылки на "
        "страницу Confluence в нём нет, и в файлах чата нет ни одного текстового "
        "файла. Этот конвейер разбирает готовую аналитику, а не пишет её: дайте "
        "ссылку на страницу, загрузите документ файлом в чат или вставьте "
        "его в сообщение. Прогон остановлен до первого вызова модели — деньги "
        "не потрачены."
    )


# --------------------------------------------------------------------------
# Чтение источника
#
# Единственная нода конвейера, которая добывает материал, и делает она это без
# модели — почему, сказано в `sources.py`. Сама добыча живёт там же: страница
# по ссылке и файлы папки нужны не одному этому графу. Здесь остаётся то,
# чего в общем модуле быть не может, — как о прочитанном сказать именно ролям
# декомпозиции. И довод, которого у общего модуля нет: все четыре этапа
# обязаны видеть ОДИН И ТОТ ЖЕ документ, а не каждый свою выборку из папки.
# --------------------------------------------------------------------------
def source_block(picked: dict) -> str:
    """
    Прочитанный документ в том виде, в каком его увидят все этапы.

    К самому тексту добавляется то, чего в нём нет: откуда он взят и чего в
    нём может не хватать. Обрезанный по потолку документ, о котором этапы не
    предупреждены, — это выводы по половине аналитики, поданные как полные.
    """
    parts = []
    if picked["kind"] == "confluence":
        parts.append(f"Источник: страница Confluence «{picked['title']}» — {picked['url']}")
    else:
        parts.append("Источник: файлы чата — " + ", ".join(picked["names"]))
        if picked.get("chosen"):
            parts.append(
                "Эти файлы выбраны оператором в интерфейсе как единственный источник. "
                "Раскладывай их, а не то, что могло бы лежать в соседних файлах."
                if len(picked["names"]) > 1
                else "Этот файл выбран оператором в интерфейсе как единственный "
                "источник. Раскладывай его, а не то, что могло бы лежать в соседних "
                "файлах."
            )
        if not picked.get("named") and len(picked["names"]) > 1:
            parts.append(
                "В запросе не назван конкретный файл, поэтому прочитаны все текстовые. "
                "Если часть из них к задаче не относится, скажи об этом в первом же "
                "документе и не раскладывай лишнее."
            )
    if picked.get("skipped"):
        parts.append("Не прочитаны: " + ", ".join(picked["skipped"]))
    if picked.get("truncated"):
        parts.append(
            "ВНИМАНИЕ: документ обрезан по потолку чтения. Не выдавай выводы по "
            "обрезанному тексту за полные — скажи, где обрыв."
        )
    if picked.get("extra"):
        parts.append(
            "В запросе есть ссылки и на другие страницы: "
            + ", ".join(picked["extra"])
            + ". Прочитана только первая; остальные упомяни как связанные."
        )
    if picked.get("foreign"):
        parts.append(
            "ВНИМАНИЕ: ссылка в запросе ведёт на "
            + ", ".join(picked["foreign"])
            + ", а страница прочитана из настроенного пространства. Это может быть "
            "совсем другой документ. Сверь заголовок с тем, что ожидал оператор, и "
            "скажи о расхождении."
        )
    return "\n\n".join(parts) + "\n\n---\n\n" + picked["text"]


def source_node(state: State, config: RunnableConfig) -> dict:
    """
    Прочитать источник и положить его текст в `artifacts` — без вызова модели.

    Повторный ход треда источник не перечитывает: документ у треда один, он уже
    прочитан, и ходить за ним в Confluence на каждое «перепиши задачу 3» значит
    платить задержкой за данные, которые не менялись. За свежестью страницы
    следит оператор, начиная новый тред.

    Отказ не останавливает конвейер: с непрочитанным документом остаётся запрос
    оператора, и роли обязаны начать с того, что документа нет, — так им и
    сказано в `jira_roles.brief`.
    """
    known = state.get("source") or {}
    if known.get("read"):
        return {}

    question = sources.question_of(state)
    task = sources.task_dir(config)
    wanted = sources.picked_files(config)

    # Выбранный в интерфейсе файл сильнее ссылки в тексте. Ссылка в запросе
    # бывает и попутной — «см. также», — а выбор файла оператор делает под этот
    # прогон и руками. Не всё живёт в Confluence и Jira; когда документ лежит
    # файлом в папке, конвейер обязан раскладывать именно его.
    picked = (
        sources.from_files(question, task, wanted)
        if wanted
        else (sources.from_confluence(question) or sources.from_files(question, task))
    )

    if picked is None:
        # Аналитику вставили прямо в сообщение — это законный вход, и отдельного
        # документа у него нет: он уже стоит в запросе оператора.
        return {"source": {"kind": "message", "read": True}, "stage": "source"}

    if picked.get("error"):
        note = f"Источник не прочитан: {picked['error']}."
        return {
            "source": {**picked, "read": False},
            "artifacts": {jira_roles.SOURCE: note},
            "messages": [AIMessage(content=note)],
            "stage": "source",
        }

    return {
        "source": {
            "kind": picked["kind"],
            "read": True,
            "title": picked.get("title", ""),
            "url": picked.get("url", ""),
            "names": picked.get("names", []),
            "chars": len(picked["text"]),
            "truncated": bool(picked.get("truncated")),
        },
        "artifacts": {jira_roles.SOURCE: source_block(picked)},
        "messages": [AIMessage(content=_source_note(picked))],
        "stage": "source",
    }


def _source_note(picked: dict) -> str:
    """Что прочитано — оператору в тред. Ход по графу должен быть виден."""
    where = (
        f"страница Confluence «{picked['title']}» {picked['url']}"
        if picked["kind"] == "confluence"
        else "файлы чата: " + ", ".join(picked["names"])
    )
    tail = " (текст обрезан по потолку чтения)" if picked.get("truncated") else ""
    return f"Прочитан источник: {where} — {len(picked['text'])} символов{tail}."


# --------------------------------------------------------------------------
# Заведение задач
#
# Единственный узел проекта, который создаёт объекты в чужой системе. Публикация
# страницы рядом с ним выглядит безобидно: страницу перезапишет следующий
# прогон, а тридцать задач, заведённых не в тот проект, кто-то закрывает руками
# по одной, и уведомления о них уже ушли всей команде.
#
# Поэтому здесь стоит остановка, и по умолчанию она включена — в отличие от
# подтверждения публикации. Ею же спрашивается единственное, чего конвейер не
# может вывести из аналитики: проект. Ответ оператора приезжает объектом
# `{"decision": "approved", "project": "ORB", "digest": "…"}` — решение и
# проект одним ходом, чтобы не спрашивать дважды об одном и том же.
#
# Узел идёт общим порядком внешних действий (`actions.py`):
#
#   предложение  карточки плана с судьбой каждой из журнала области «тред и
#                проект»: завести, поправить заведённую, не трогать;
#   правила      рубильник, реквизиты, разбор плана, проект, «отправлять нечего»;
#   согласие     остановка с отпечатком предложения; после ответа предложение
#                собирается заново, и другое содержимое спрашивается ещё раз;
#   выполнение   `jira_writer.create_issues` через журнал операций;
#   сверка       `jira_writer.verify_issues`: что трекер хранит под ключами.
# --------------------------------------------------------------------------
#: Сколько раз остановка переспрашивает, если предложение меняется между
#: показом и ответом. Больше — значит, журнал меняет кто-то ещё, и разумнее
#: остановиться, чем гоняться за ним.
ASK_LIMIT = 3


def _thread(config: RunnableConfig) -> str:
    return str((config.get("configurable") or {}).get("thread_id") or "no-thread")


def _scope(thread: str, project: str) -> str:
    """
    Область журнала: тред и проект, без отпечатка плана.

    С отпечатком каждая правка плана открывала новую область, и следующий ход
    заводил все карточки заново, включая неизменённые. Теперь область одна на
    тред и проект, и карточку узнают по её локальному ключу.
    """
    return jira_journal.run_key(thread, project)


def _base_url() -> str:
    try:
        return jira.load_settings().base_url
    except jira.JiraError:
        return ""


def _proposal(plan: jira_plan.Plan, project: str, thread: str, source: str,
              journal: Any) -> dict:
    """
    Предложение хода: цель и судьба каждой карточки.

    Без проекта журнал не спрашивается — области ещё нет, и все карточки
    показываются новыми; когда оператор назовёт проект, предложение соберётся
    заново уже с журналом (`_ask`).
    """
    scope = _scope(thread, project) if project else ""
    if project and journal is not None and hasattr(journal, "import_legacy"):
        # Старый журнал жил по ключу «тред + проект + состав плана». При
        # обновлении кода тот же план должен найти уже созданные задачи.
        journal.import_legacy(
            scope, actions.legacy_rows(thread, project, plan.fingerprint()),
            {item.local: item.digest(source) for item in plan.items},
        )
    operations = jira_writer.operations(
        plan, run=scope, journal=journal if project else None, source=source
    )
    return actions.seal(
        "jira",
        graph=PIPELINE.key,
        target={"system": "jira", "project": project, "base_url": _base_url()},
        operations=operations,
        thread=thread,
        owner=actions.actor(),
        scope=scope,
    )


def _migrate_legacy_history(messages: list, project: str, thread: str, source: str,
                            journal: Any) -> None:
    """Найти старые области по планам этого треда, даже если нынешний план изменён."""
    if not project or not hasattr(journal, "import_legacy"):
        return
    scope = _scope(thread, project)
    seen: set[str] = set()
    # Самый свежий план первым: прежний код заводил отдельные задачи для
    # каждой редакции, а новый журнал должен узнавать последнюю из них.
    for message in reversed(messages):
        if getattr(message, "type", "") != "ai":
            continue
        content = getattr(message, "content", "")
        if not isinstance(content, str) or '"issues"' not in content:
            continue
        try:
            previous = jira_plan.parse(content)
        except jira_plan.PlanError:
            continue
        fingerprint = previous.fingerprint()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        journal.import_legacy(
            scope, actions.legacy_rows(thread, project, fingerprint),
            {item.local: item.digest(source) for item in previous.items},
        )


def _project(config: RunnableConfig) -> str:
    """Проект хода: выбранный в интерфейсе, иначе — проект по умолчанию пользователя."""
    chosen = str(common_graph.options(config).get("jira_project") or "").strip()
    return chosen.upper() or jira.default_project()


def _known_projects() -> list[dict]:
    """
    Проекты трекера для окна выбора. Пустой список — не удалось спросить.

    Отказ здесь ничего не ломает: оператор впишет ключ руками, а прогон из-за
    недоступного справочника падать не должен.
    """
    try:
        return jira_writer.projects()
    except jira_writer.JiraError:
        return []


def _title(plan: jira_plan.Plan, operations: list[dict]) -> str:
    """Заголовок остановки: что уедет, а не сколько карточек в плане."""
    counts = {kind: sum(op.get("action") == kind for op in operations)
              for kind in (jira_writer.UPDATE, jira_writer.UNCHANGED)}
    if not any(counts.values()):
        return f"Завести в Jira: {plan.summary()}"
    fresh = len(operations) - sum(counts.values())
    parts = [f"новых {fresh}"] if fresh else []
    if counts[jira_writer.UPDATE]:
        parts.append(f"правок {counts[jira_writer.UPDATE]}")
    if counts[jira_writer.UNCHANGED]:
        parts.append(f"без изменений {counts[jira_writer.UNCHANGED]}")
    return "Jira: " + ", ".join(parts)


def _prompt(plan: jira_plan.Plan, proposal: dict, source: str = "", note: str = "") -> dict:
    """Что оператор видит перед заведением: черновики, проект и сводку."""
    project = str(proposal["target"].get("project") or "")
    operations = proposal["operations"]
    cards, warnings = drafts.issues(plan, project, source=source, operations=operations)
    payload = {
        "action": "jira",
        "project": project,
        # Справочник спрашивается только тогда, когда выбирать и правда
        # надо: при известном проекте это лишний поход в сеть на каждом
        # прогоне ради списка, который никто не откроет.
        "projects": _known_projects() if not project else [],
        "count": len(actions.pending(proposal)),
        "summary": plan.summary(),
        "title": _title(plan, operations),
        "format": "markdown",
        # Черновик каждой карточки: заголовок, тип из схемы проекта и
        # описание ровно в том виде, в каком оно уедет в запросе. Список
        # строк рядом остаётся сводкой на один взгляд (см. drafts.py).
        "drafts": cards,
        # Отброшенное разбором и подменённые типы: то, чего в карточках
        # уже не видно, потому что их там нет.
        "warnings": ([note] if note else []) + warnings + plan.warnings,
        "document": plan.table(),
        "hint": (
            'ответьте {"decision": "approved", "project": "ABC"}, чтобы завести '
            "задачи в проекте ABC, или {\"decision\": \"rejected\", \"reason\": ...}"
        ),
    }
    return actions.shown(payload, proposal)


def _continues(shown: list[dict], actual: dict, journal: Any) -> bool:
    """
    Отличается ли пересобранное предложение от показанного только уже сделанным.

    Показано «завести», а теперь карточка «без изменений» или «найти по метке»
    — и запись о ней в журнале сделало одобренное действие этого же треда.
    Так выглядит продолжение после падения посреди пачки и проект, в котором
    те же карточки завёл прошлый одобренный ход. Правка (`update`) продолжением
    не бывает никогда: на неё согласия не спрашивали.
    """
    before = {op["key"]: op for op in shown}
    if set(before) != {op["key"] for op in actual["operations"]}:
        return False
    for op in actual["operations"]:
        was = before[op["key"]]
        if was == op:
            continue
        if was.get("action") != jira_writer.CREATE or op.get("action") not in (
            jira_writer.UNCHANGED, jira_writer.RECOVER
        ):
            return False
        record = journal.record(actual["scope"], "issue", op["key"]) or {}
        if not journal.approved_action(record.get("action_id", "")):
            return False
    return True


def _ask(plan: jira_plan.Plan, proposal: dict, source: str, journal: Any,
         history: list | None = None) -> tuple[dict, dict]:
    """
    Остановка перед заведением: решение, проект и предложение, к которому оно относится.

    После ответа предложение собирается заново — уже с проектом, который
    назвал оператор, и с журналом на этот момент. Совпали операции —
    согласие относится к нему. Не совпали (проект оказался тем, где часть
    карточек уже заведена, или журнал успел измениться) — спрашиваем ещё раз,
    показывая новое: согласие на «завести всё» не разрешает «поправить три».
    """
    thread = proposal["thread"]
    note = ""
    for _ in range(ASK_LIMIT):
        actions.Recorder(proposal, journal=journal).propose()
        answer = interrupt(_prompt(plan, proposal, source, note))
        picked = str(answer.get("project") or "").strip().upper() if isinstance(answer, dict) else ""
        project = picked or str(proposal["target"].get("project") or "")
        _migrate_legacy_history(history or [], project, thread, source, journal)
        actual = _proposal(plan, project, thread, source, journal)
        echoed = str(answer.get("digest") or "") if isinstance(answer, dict) else ""
        if echoed and echoed != proposal["digest"]:
            earlier = journal.approved_operations(thread, "jira", echoed)
            if earlier is not None and _continues(earlier, actual, journal):
                # Согласие дано на это же предложение, и расходится оно только
                # продвижением его собственного выполнения до падения.
                answer = {**answer, "digest": proposal["digest"]}
        decision = actions.bind(answer, proposal["digest"])
        if decision["decision"] == actions.STALE:
            note = decision["reason"]
            proposal = _proposal(plan, str(proposal["target"].get("project") or ""),
                                 thread, source, journal)
            continue
        if picked:
            decision["project"] = picked
        if decision["decision"] not in {"approved", "drafts"}:
            return decision, proposal
        if (actual["operations"] == proposal["operations"]
                or _continues(proposal["operations"], actual, journal)):
            # Проект назван в том же ответе — это часть решения, а не расхождение.
            decision["digest"] = actual["digest"]
            return decision, actual
        note = (
            f"В проекте {project} часть карточек уже заведена в этом чате или журнал изменился "
            "после показа: ниже — что уедет на самом деле. Подтвердите ещё раз."
        )
        proposal = actual
    return (
        {"decision": actions.STALE, "digest": proposal["digest"],
         "reason": "предложение менялось между показом и ответом; запустите заведение заново"},
        proposal,
    )


def create_node(state: State, config: RunnableConfig) -> dict:
    """
    Завести задачи по разобранному плану и вернуть ключи со ссылками.

    Отказ на любом шаге — не падение треда: документы к этому моменту написаны
    и оплачены, и терять их из-за недоступного трекера нельзя. Поэтому всякая
    причина оседает в `state["issues"]` и уходит сообщением оператору, а граф
    идёт дальше — в память и в публикацию.
    """
    document = ((state.get("artifacts") or {}).get(jira_roles.LAST.key) or "").strip()

    def skip(status: str, reason: str) -> dict:
        return {
            "issues": {"status": status, "reason": reason},
            "messages": [AIMessage(content=f"Задачи в Jira не заведены: {reason}.")],
            "stage": "create",
        }

    if not document:
        return skip("skipped", "этап карточек не выполнен, заводить нечего")
    if not jira_writer.is_enabled():
        return skip("disabled", "заведение выключено через JIRA_CREATE_ISSUES")
    absent = jira_writer.missing_vars()
    if absent:
        return skip("skipped", credentials.missing_message(absent))

    try:
        plan = jira_plan.parse(document)
    except jira_plan.PlanError as exc:
        return skip("failed", f"план не разобран ({exc})")
    if not plan.items:
        return skip("skipped", "в плане нет ни одной карточки")

    project = _project(config)
    # Ссылка на источник нужна уже здесь: она уходит в описание задачи, а
    # черновик обязан показывать описание целиком, включая её.
    source = (state.get("source") or {}).get("url", "")
    thread = _thread(config)
    if nested(config):
        # Вложенный прогон задач не заводит и не спрашивает: план уезжает
        # вызывающему предложением, и заведёт его `create_proposed` после
        # решения оператора — одного на все предложения хода. Журнал здесь
        # не спрашивается: заводить будут в области треда вызывающего, а не
        # этого, и показывать чужую судьбу карточек значило бы обещать не то.
        shown = _proposal(plan, project, thread, source, None)
        offered = {
            "kind": "jira",
            "graph": PIPELINE.key,
            "approval_required": cfg.jira_create_require_approval() or not project,
            "prompt": _prompt(plan, shown, source),
            "effect": {
                "project": project,
                "document": document,
                "fingerprint": plan.fingerprint(),
                "digest": plan.digest(source),
                "source": source,
            },
        }
        reason = "вложенный прогон: заведение передано вызывающему графу"
        return {
            "issues": {"status": "proposed", "project": project, "reason": reason},
            "proposals": proposals.offer(state, offered),
            "messages": [AIMessage(content=f"Задачи в Jira не заведены: {reason}.")],
            "stage": "create",
        }

    try:
        journal = actions.store()
        _migrate_legacy_history(state.get("messages") or [], project, thread, source, journal)
        proposal = _proposal(plan, project, thread, source, journal)
    except actions.JournalUnavailable as exc:
        # Журнал недоступен — заводить вслепую нельзя: именно он и отличает
        # повтор от первого раза и правку от новой задачи.
        return skip("failed", f"журнал операций недоступен ({exc})")
    if project and not actions.pending(proposal):
        return _unchanged(proposal)

    # Спрашиваем в двух случаях: так настроено или спрашивать всё равно
    # придётся — без проекта заводить некуда, и это единственный вопрос,
    # который конвейер имеет право задать.
    approval: dict = {}
    if cfg.jira_create_require_approval() or not project:
        try:
            approval, proposal = _ask(plan, proposal, source, journal,
                                     state.get("messages") or [])
        except actions.JournalUnavailable as exc:
            return skip("failed", f"журнал операций недоступен ({exc})")
        project = str(proposal["target"].get("project") or "")
        recorder = actions.Recorder(proposal, journal=journal)
        recorder.decide(approval)
        if approval["decision"] == actions.STALE:
            return skip("stale", approval["reason"])
        if approval["decision"] not in {"approved", "drafts"}:
            return skip("rejected", approval.get("reason") or "оператор отменил заведение")
        if approval["decision"] == "drafts" and project:
            from agent import jira_forms

            try:
                result = jira_forms.prepare(plan, project, source=source)
            except jira_writer.JiraError as exc:
                return skip("failed", str(exc))
            recorder.done("forms", {"status": "forms", "project": project})
            return {"issues": result, "stage": "create", "messages": [AIMessage(
                content="Подготовлены формы Jira: откройте их в разделе задач, проверьте "
                        "и сохраните вручную. Задачи автоматически не создавались."
            )]}
    if not project:
        return skip(
            "skipped",
            "не указан проект: выберите его в интерфейсе или задайте проект по умолчанию "
            "в «Настройки» → «Мои подключения»",
        )
    if not actions.pending(proposal):
        return _unchanged(proposal)
    try:
        result = _execute(plan, proposal, approval, source=source, journal=journal)
    except jira_writer.JiraError as exc:
        return skip("failed", str(exc))
    except actions.JournalUnavailable as exc:
        return skip("failed", f"журнал операций недоступен ({exc})")
    if result.get("status") == "stale":
        return skip("stale", result["reason"])

    return {
        "issues": result,
        "messages": [AIMessage(content=_created_note(result))],
        "stage": "create",
    }


def _unchanged(proposal: dict) -> dict:
    """Все карточки уже заведены этим чатом и не менялись: спрашивать не о чем."""
    project = proposal["target"]["project"]
    keys = [op.get("remote") or op["key"] for op in proposal["operations"]]
    reason = (
        f"все карточки плана уже заведены в проекте {project} этим чатом и не менялись "
        f"({', '.join(keys)})"
    )
    return {
        "issues": {"status": "unchanged", "project": project, "reason": reason},
        "messages": [AIMessage(content=f"Задачи в Jira не отправлялись: {reason}.")],
        "stage": "create",
    }


def _execute(plan: jira_plan.Plan, proposal: dict, approval: dict, *, source: str,
             journal: Any) -> dict:
    """
    Выполнение и сверка по согласованному предложению.

    Перед записью предложение собирается ещё раз и сравнивается с тем, на
    которое дано согласие: между ответом и этой строкой журнал мог измениться
    параллельным прогоном того же треда. Разошлось — ничего не пишем.
    """
    project = proposal["target"]["project"]
    recorder = actions.Recorder(proposal, strict=True, journal=journal)
    if approval:
        fresh = _proposal(plan, project, proposal["thread"], source, journal)
        stale = actions.stale(approval, fresh["digest"])
        if stale:
            result = {"status": "stale", "project": project, "reason": stale}
            recorder.done("stale", result)
            return result
    else:
        # Политика не спрашивала (JIRA_CREATE_REQUIRE_APPROVAL=0): согласия
        # нет, но предложение в журнале есть — видно, что и почему уехало.
        recorder.propose()
    book = recorder.operations()
    result = jira_writer.create_issues(
        plan, project, source=source, run=proposal["scope"], journal=book
    )
    result = jira_writer.verify_issues(result, run=proposal["scope"], journal=book)
    recorder.done(result.get("status", "failed"), result)
    return result


def create_proposed(effect: dict, *, thread: str, project: str = "",
                    approval: dict | None = None) -> dict:
    """
    Завести задачи по предложению вложенного прогона.

    План разбирается заново из документа карточек и сверяется отпечатком
    содержимого: заводится ровно то, что предлагали, а не то, что разбор дал
    бы сегодня. Проект — из решения оператора, иначе предложенный. Область
    журнала строится так же, как в `create_node`, но по треду вызывающего:
    повтор применения находит свои операции и не заводит задачи второй раз.

    Предложение показывало заведение, а не правку: вложенный граф не знает
    журнала вызывающего. Поэтому если в области вызывающего карточки этого
    плана уже заведены с другим содержимым, по предложению ничего не пишется
    — правку заведённых задач согласуют отдельно, в самом треде.

    Тред вызывающего обязателен. Без него область у всех предложений с теми
    же карточками и проектом одна, и независимое предложение вернуло бы
    задачи, заведённые по чужому, вместо своих.
    """
    thread = str(thread or "").strip()
    if not thread:
        raise ValueError("заведение задач по предложению требует треда вызывающего графа")
    source = str(effect.get("source") or "")
    try:
        plan = jira_plan.parse(str(effect.get("document") or ""))
    except jira_plan.PlanError as exc:
        return {"status": "failed", "reason": f"план не разобран ({exc})"}
    # Предложения, выпущенные до отпечатка содержимого, сверяются по составу.
    same = (plan.digest(source) == effect["digest"] if effect.get("digest")
            else plan.fingerprint() == effect.get("fingerprint"))
    if not same:
        return {"status": "stale", "reason": "план карточек изменился после предложения"}

    project = (project or str(effect.get("project") or "")).strip().upper()
    if not project:
        return {"status": "skipped", "reason": "не указан проект"}
    if not jira_writer.is_enabled():
        return {"status": "disabled", "reason": "заведение выключено через JIRA_CREATE_ISSUES"}
    absent = jira_writer.missing_vars()
    if absent:
        return {"status": "skipped", "reason": credentials.missing_message(absent)}

    try:
        journal = actions.store()
        proposal = _proposal(plan, project, thread, source, journal)
        edits = [op["key"] for op in proposal["operations"] if op["action"] == jira_writer.UPDATE]
        if edits:
            return {
                "status": "stale",
                "reason": "в этом чате карточки " + ", ".join(edits) + " уже заведены с другим "
                "содержимым; правку заведённых задач согласуйте в самом треде jira",
            }
        if not actions.pending(proposal):
            return {**_unchanged(proposal)["issues"], "created": []}
        # Согласие на предложение проверил `proposals.apply` по его id; здесь
        # оно привязывается к содержимому, которое уедет в этой области.
        bound = {**actions.approval_of(approval), "digest": proposal["digest"]} if approval else {}
        if bound:
            actions.Recorder(proposal, journal=journal).decide(bound)
        return _execute(plan, proposal, bound, source=source, journal=journal)
    except jira_writer.JiraError as exc:
        return {"status": "failed", "reason": str(exc)}
    except actions.JournalUnavailable as exc:
        return {"status": "failed", "reason": f"журнал операций недоступен ({exc})"}


def _created_note(result: dict) -> str:
    """Ключи и ссылки — то, ради чего конвейер и запускали."""
    entries = result.get("created") or []
    created = [item for item in entries
               if not item.get("unchanged") and not item.get("updated") and not item.get("recovered")]
    head = (
        f"Заведено задач в проекте {result['project']}: {len(created)}"
        if created
        else f"В проекте {result['project']} не заведено новых задач"
    )
    updated = [item for item in entries if item.get("updated")]
    if updated:
        head += f", обновлено {len(updated)}"
    unchanged = [item for item in entries if item.get("unchanged")]
    if unchanged:
        head += f", без изменений {len(unchanged)}"
    failed = result.get("failed") or []
    if failed:
        head += f", отказов {len(failed)}"
    unresolved = result.get("unresolved") or []
    if unresolved:
        # Неопределённость называется отдельно от отказа: «не заведено» и
        # «неизвестно, заведено ли» требуют от человека разных действий.
        head += f", с неизвестным результатом {len(unresolved)}"
    recovered = [item for item in entries if item.get("recovered")]
    if recovered:
        head += f", восстановлено из журнала {len(recovered)}"
    check = result.get("verification") or {}
    if check:
        # Сверка — отдельной фразой: «заведено» говорит трекер, «лежит то, что
        # отправили» — чтение после записи.
        head += (
            f". Сверка после записи: совпало {len(check.get('verified') or [])}"
            f", расхождений {len(check.get('differs') or [])}"
            f", не прочитано {len(check.get('unverified') or [])}"
        )
    body = jira_writer.format_created(result)
    return f"{head}.\n{body}" if body else f"{head}."


def build_graph(llm: Any = None) -> StateGraph:
    """
    Собрать конвейер по описанию из `jira_roles.py`.

    Узлы, ворота и маршруты — те же фабрики, что у конвейера аналитики; им
    передаётся другое описание ролей, своя проверка входа, узел чтения источника
    перед первой ролью и узел заведения задач после последней.

    llm — готовая модель вместо собранной из окружения. Нужна тестам, чтобы
    прогнать граф целиком на подделке, без ключа и без сети.
    """
    return common_graph.build_graph(
        llm=llm,
        pipeline=PIPELINE,
        admission=missing_analysis,
        prelude=source_node,
        postlude=create_node,
        state_schema=State,
        options_schema=Options,
    )


# Для Studio / langgraph dev: компилируем БЕЗ чекпоинтера, как и остальные графы.
graph = metrics.observe(build_graph().compile(), "jira")
