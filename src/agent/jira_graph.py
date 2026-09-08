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

from agent import config as cfg
from agent import confluence, drafts, inputs, jira_plan, jira_roles, jira_writer, sources
from agent import graph as common_graph

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
            + f", но в папке задачи {'их' if many else 'его'} нет или "
            + ("они не читаются" if many else "он не читается")
            + ". Выберите другой файл или снимите выбор, чтобы конвейер прочитал "
            "папку целиком. Прогон остановлен до первого вызова модели — деньги "
            "не потрачены."
        )

    question = sources.question_of(state)
    if confluence.find_page_ids(question):
        absent = confluence.missing_vars()
        if not absent:
            return ""
        return (
            "В запросе есть ссылка на страницу Confluence, но читать её нечем: не "
            "заданы " + ", ".join(absent) + ". Заполните переменные в .env и "
            "перезапустите сервер — или приложите документ файлом в папку задачи. "
            "Прогон остановлен до первого вызова модели — деньги не потрачены."
        )

    if len(question) >= MIN_ANALYSIS_CHARS:
        return ""

    task = sources.task_dir(config)
    if inputs.readable_files(task):
        return ""

    return (
        f"Раскладывать нечего: в сообщении {len(question)} символов, ссылки на "
        "страницу Confluence в нём нет, и в папке задачи нет ни одного текстового "
        "файла. Этот конвейер разбирает готовую аналитику, а не пишет её: дайте "
        "ссылку на страницу, положите документ файлом в папку задачи или вставьте "
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
        parts.append("Источник: файлы папки задачи — " + ", ".join(picked["names"]))
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
        else "файлы папки задачи: " + ", ".join(picked["names"])
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
# `{"decision": "approved", "project": "ORB"}` — решение и проект одним ходом,
# чтобы не спрашивать дважды об одном и том же.
# --------------------------------------------------------------------------
def _project(config: RunnableConfig) -> str:
    """Проект хода: выбранный в интерфейсе, иначе — из настроек."""
    chosen = str(common_graph.options(config).get("jira_project") or "").strip()
    return (chosen or cfg.jira_project_key()).upper()


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


def _ask(plan: jira_plan.Plan, project: str, source: str = "") -> dict:
    """Остановка перед заведением: показать черновики, спросить решение и проект."""
    cards, warnings = drafts.issues(plan, project, source=source)
    answer = interrupt(
        {
            "action": "jira",
            "project": project,
            # Справочник спрашивается только тогда, когда выбирать и правда
            # надо: при известном проекте это лишний поход в сеть на каждом
            # прогоне ради списка, который никто не откроет.
            "projects": _known_projects() if not project else [],
            "count": len(plan.items),
            "summary": plan.summary(),
            "title": f"Завести в Jira: {plan.summary()}",
            "format": "markdown",
            # Черновик каждой карточки: заголовок, тип из схемы проекта и
            # описание ровно в том виде, в каком оно уедет в запросе. Список
            # строк рядом остаётся сводкой на один взгляд (см. drafts.py).
            "drafts": cards,
            # Отброшенное разбором и подменённые типы: то, чего в карточках
            # уже не видно, потому что их там нет.
            "warnings": warnings + plan.warnings,
            "document": plan.table(),
            "hint": (
                'ответьте {"decision": "approved", "project": "ABC"}, чтобы завести '
                "задачи в проекте ABC, или {\"decision\": \"rejected\", \"reason\": ...}"
            ),
        }
    )
    decision = common_graph.approval_of(answer)
    if isinstance(answer, dict) and answer.get("decision") == "drafts":
        decision["decision"] = "drafts"
    if isinstance(answer, dict):
        picked = str(answer.get("project") or "").strip()
        if picked:
            decision["project"] = picked.upper()
    return decision


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
        return skip("skipped", "не заданы в .env: " + ", ".join(absent))

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
    # Спрашиваем в двух случаях: так настроено или спрашивать всё равно
    # придётся — без проекта заводить некуда, и это единственный вопрос,
    # который конвейер имеет право задать.
    if cfg.jira_create_require_approval() or not project:
        decision = _ask(plan, project, source)
        project = decision.get("project") or project
        if decision["decision"] not in {"approved", "drafts"}:
            return skip("rejected", decision.get("reason") or "оператор отменил заведение")
        if decision["decision"] == "drafts" and project:
            from agent import jira_forms

            try:
                result = jira_forms.prepare(plan, project, source=source)
            except jira_writer.JiraError as exc:
                return skip("failed", str(exc))
            return {"issues": result, "stage": "create", "messages": [AIMessage(
                content="Подготовлены формы Jira: откройте их в разделе задач, проверьте "
                        "и сохраните вручную. Задачи автоматически не создавались."
            )]}
    if not project:
        return skip(
            "skipped",
            "не указан проект: выберите его в интерфейсе или задайте JIRA_PROJECT_KEY",
        )

    try:
        result = jira_writer.create_issues(plan, project, source=source)
    except jira_writer.JiraError as exc:
        return skip("failed", str(exc))

    return {
        "issues": result,
        "messages": [AIMessage(content=_created_note(result))],
        "stage": "create",
    }


def _created_note(result: dict) -> str:
    """Ключи и ссылки — то, ради чего конвейер и запускали."""
    created = result.get("created") or []
    head = (
        f"Заведено задач в проекте {result['project']}: {len(created)}"
        if created
        else f"В проекте {result['project']} не заведено ни одной задачи"
    )
    failed = result.get("failed") or []
    if failed:
        head += f", отказов {len(failed)}"
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
graph = build_graph().compile()
