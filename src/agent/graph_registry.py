"""
Реестр графов: имя → сборка, что граф пишет наружу и где останавливается.

До реестра это знание было размазано по восьми модулям и трём спискам:
`langgraph.json` знал имена и пути, манифесты интерфейса — формы остановок,
а что граф пишет наружу, можно было узнать, только прочитав его целиком.
Оркестратору, который вызывает графы сам, нужно всё это сразу и в одном
месте: какой граф собрать, какие остановки у него будут и какие внешние
действия он совершит. От последнего зависит главное — что изменится, если
позвать граф вложенным (`runtime.nested`): остановки и записи наружу там
не исчезают молча, а превращаются в предложения (`proposals.py`).

Реестр — описание, а не код графов. Модули импортируются лениво: список
читается без модели, ключей и сети. Совпадение описания с настоящими графами
проверяют тесты: имена и пути — с `langgraph.json`, остановки — с формами
манифестов интерфейса, узлы — со скомпилированной топологией.

Что происходит с остановкой или действием во вложенном прогоне:

  kept      остаётся как есть. Только пауза: её просит человек, а не граф;
  skipped   не делается: ворота этапов, запись в долгую память;
  proposed  не делается и не спрашивается, а уезжает предложением;
  refused   отклоняется без вопроса: запросы к источникам, составленные
            моделью, — выполнить их молча значило бы решить за оператора.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

KEPT = "kept"
SKIPPED = "skipped"
PROPOSED = "proposed"
REFUSED = "refused"
NESTED = (KEPT, SKIPPED, PROPOSED, REFUSED)


@dataclass(frozen=True)
class Stop:
    """Место, где граф встаёт и ждёт человека (`interrupt()`)."""

    # Поле `action` в payload остановки. По нему интерфейс выбирает форму,
    # а вложенный прогон называет вид предложения.
    action: str
    # Узлы, в которых остановка случается.
    nodes: tuple[str, ...]
    # Когда: настройка или условие.
    when: str
    nested: str


@dataclass(frozen=True)
class Effect:
    """Запись за пределы треда: чужая система, диск, общая память."""

    # Вид действия. У тех, что становятся предложениями, совпадает с видом
    # предложения (`proposals.py`).
    kind: str
    nodes: tuple[str, ...]
    # Куда и что именно.
    target: str
    nested: str


@dataclass(frozen=True)
class GraphSpec:
    """Граф: как его собрать, где он останавливается и что пишет наружу."""

    name: str
    # Модуль с `build_graph()` и скомпилированным `graph`.
    module: str
    stops: tuple[Stop, ...]
    effects: tuple[Effect, ...]
    # Графы, которые этот вызывает внутри своих узлов.
    nests: tuple[str, ...] = ()

    def build(self, **kwargs: Any):
        """`build_graph()` модуля: несобранный `StateGraph` с теми же параметрами."""
        return importlib.import_module(self.module).build_graph(**kwargs)

    @property
    def graph(self):
        """Скомпилированный граф, тот же, что отдаёт сервер."""
        return importlib.import_module(self.module).graph

    @property
    def reference(self) -> str:
        """Путь графа в записи `langgraph.json`."""
        return "./src/" + self.module.replace(".", "/") + ".py:graph"

    def proposals(self) -> tuple[str, ...]:
        """Виды предложений, которые граф может выпустить вложенным."""
        found = [s.action for s in self.stops if s.nested == PROPOSED]
        found += [e.kind for e in self.effects if e.nested == PROPOSED]
        return tuple(dict.fromkeys(found))


# --------------------------------------------------------------------------
# Общее у конвейеров из ролей (`builder.py`)
# --------------------------------------------------------------------------
PAUSE_WHEN = "оператор нажал «Пауза»; берётся перед ближайшим вызовом модели"
PUBLISH_WHEN = "PUBLISH_REQUIRE_APPROVAL=1 (по умолчанию), если публикация не пропущена"
PUBLISH_TARGET = (
    "страницы в PUBLISH_TARGET: Confluence или файлы PUBLISH_DIR; "
    "по решению «drafts» — черновики Confluence; журнал действий"
)
FALLBACK = "; при отказе или без реквизитов Confluence — файл в PUBLISH_DIR"
MEMORY = Effect(
    "memory", ("remember",), "факт хода в долгую память store LangGraph (MEMORY_ENABLED)", SKIPPED
)


def _pipeline(
    name: str,
    module: str,
    roles: tuple[str, ...],
    *,
    stops: tuple[Stop, ...] = (),
    effects: tuple[Effect, ...] = (),
) -> GraphSpec:
    """
    Граф общей сборки: пауза в ролях, ворота перед каждой ролью после первой.

    У конвейера из одной роли ворот нет — и остановки «stage» тоже: её негде
    сделать, а объявленная остановка без узла обещала бы интерфейсу форму,
    которую он никогда не покажет.
    """
    gates = tuple(f"gate_{role}" for role in roles[1:])
    stage = (
        Stop(
            "stage",
            gates,
            "PIPELINE_REQUIRE_APPROVAL=1; при PIPELINE_APPROVAL_STAGES=first — "
            "только после первого этапа",
            SKIPPED,
        ),
    ) if gates else ()
    return GraphSpec(
        name=name,
        module=module,
        stops=(
            Stop("pause", roles, PAUSE_WHEN, KEPT),
            *stage,
            Stop("publish", ("approve",), PUBLISH_WHEN, PROPOSED),
            *stops,
        ),
        effects=(
            *effects,
            Effect("publish", ("publish",), PUBLISH_TARGET, PROPOSED),
            MEMORY,
        ),
    )


GRAPHS: tuple[GraphSpec, ...] = (
    _pipeline(
        "agent",
        "agent.graph",
        ("requirements", "api", "data", "architecture", "review"),
    ),
    _pipeline(
        "prep",
        "agent.prep_graph",
        ("intake", "gaps", "research", "plan", "draft"),
        effects=(
            # Запись в свою базу, а не в чужую систему, но за пределы треда:
            # вложенный прогон её не делает — изменение ведёт вызывающий граф.
            Effect(
                "change",
                ("change",),
                "изменение, требования с номерами и метаданные Evidence в Postgres "
                "(POSTGRES_URI)",
                SKIPPED,
            ),
        ),
    ),
    _pipeline("drawio", "agent.drawio_graph", ("survey", "components", "page", "review")),
    _pipeline("audit", "agent.audit_graph", ("trace", "conflicts", "verdict")),
    _pipeline(
        "metrics",
        "agent.flow_graph",
        ("summary",),
        effects=(
            # Запись в свою базу, как у изменений `prep`: вложенный прогон её не
            # делает и считает показатели по свежему чтению Jira.
            Effect(
                "flow",
                ("flow",),
                "задачи и спринты доски Jira в Postgres (POSTGRES_URI), прочитанные "
                "токеном пользователя прогона",
                SKIPPED,
            ),
        ),
    ),
    _pipeline(
        "pm",
        "agent.pm_graph",
        ("status", "priorities", "plan"),
        effects=(
            # Jira граф только читает: план — предложение, состав спринта в
            # трекере не меняется. Наружу уходит лишь то же, что у `metrics`:
            # история доски, которую сбор метрик потока копит для скорости.
            Effect(
                "flow",
                ("board",),
                "задачи и спринты доски Jira в Postgres (POSTGRES_URI), прочитанные "
                "токеном пользователя прогона",
                SKIPPED,
            ),
        ),
    ),
    _pipeline(
        "jira",
        "agent.jira_graph",
        ("scope", "backlog", "review", "issues"),
        stops=(
            Stop(
                "jira",
                ("create",),
                "JIRA_CREATE_REQUIRE_APPROVAL=1 (по умолчанию) или проект не известен",
                PROPOSED,
            ),
        ),
        effects=(
            Effect(
                "jira",
                ("create",),
                "задачи в проекте Jira: новые заводятся, исправленные правятся на месте; "
                "журнал действий (Postgres или JIRA_JOURNAL_PATH); "
                "по решению «drafts» — формы Jira",
                PROPOSED,
            ),
        ),
    ),
    GraphSpec(
        name="update",
        module="agent.update_graph",
        stops=(
            Stop("pause", ("changes",), PAUSE_WHEN, KEPT),
            Stop("publish", ("approve",), "всегда, независимо от PUBLISH_REQUIRE_APPROVAL", PROPOSED),
        ),
        effects=(
            Effect(
                "publish",
                ("publish",),
                "новая версия документа отдельной страницей или файлом в PUBLISH_TARGET; "
                "исходный документ не меняется",
                PROPOSED,
            ),
        ),
    ),
    GraphSpec(
        name="nt",
        module="agent.nt_graph",
        stops=(
            Stop("pause", ("understand_task", "investigate"), PAUSE_WHEN, KEPT),
            Stop(
                "query",
                ("approve_tools",),
                "NT_TOOL_APPROVAL: generated — запрос составила модель (по умолчанию), "
                "all — любой вызов, off — никогда",
                REFUSED,
            ),
            Stop("publish", ("approve",), PUBLISH_WHEN, PROPOSED),
        ),
        effects=(
            Effect("publish", ("publish",), "отчёт НТ в PUBLISH_TARGET" + FALLBACK, PROPOSED),
            MEMORY,
        ),
    ),
    GraphSpec(
        name="nt_run",
        module="agent.nt_run_graph",
        stops=(
            Stop("pause", ("plan_next", "analyze"), PAUSE_WHEN, KEPT),
            Stop("nt_clarify", ("clarify",), "модель просит уточнить задачу", PROPOSED),
            Stop(
                "nt_launch",
                ("approve_run",),
                "перед пробным и основным прогоном, если не NT_RUN_AUTO_APPROVE=1",
                PROPOSED,
            ),
            Stop("query", ("analyze",), "запросы вложенного nt по NT_TOOL_APPROVAL", REFUSED),
            Stop("publish", ("approve",), PUBLISH_WHEN, PROPOSED),
        ),
        effects=(
            Effect(
                "nt_launch",
                ("prepare", "smoke", "start_load", "stop_test"),
                "runner НТ: сценарий k6 и нагрузка на стенд из его capabilities, "
                "остановка прогона",
                PROPOSED,
            ),
            Effect("publish", ("publish",), "отчёт кампании в PUBLISH_TARGET" + FALLBACK, PROPOSED),
            Effect(
                "memory",
                ("analyze",),
                "вложенный nt пишет факт хода в долгую память (MEMORY_ENABLED)",
                SKIPPED,
            ),
        ),
        nests=("nt",),
    ),
)

_BY_NAME = {spec.name: spec for spec in GRAPHS}


def names() -> tuple[str, ...]:
    return tuple(_BY_NAME)


def get(name: str) -> GraphSpec:
    """Описание графа по имени; неизвестное имя — KeyError с перечнем известных."""
    try:
        return _BY_NAME[name]
    except KeyError:
        raise KeyError(f"граф {name!r} не зарегистрирован; есть: {', '.join(_BY_NAME)}") from None
