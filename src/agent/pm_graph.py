"""
Десятый конвейер: доска Jira -> где мы -> приоритеты -> план спринта.

Граф:

  START -> context -> board -> status -> gate_priorities -> priorities
        -> gate_plan -> plan -> remember -> approve -> publish -> END

  START -> no_input -> remember      (доска не названа или Jira не настроена)

Что делает каждая нода:

  board       без модели: читает доску (`pm_jira.collect`) — идущий и будущие
              спринты, бэклог по рангу, эпики, версии, историю спринтов из
              сбора метрик потока — и считает всё, что считается
              (`pm.build`): этап спринта, скорость и ёмкость, план до черты,
              сигналы приоритета, этапы эпиков, прогноз релизов. Таблицы
              кладутся в артефакты: их читают роли, и они же уходят
              приложением на страницу;
  status      «где мы сейчас»: спринт, эпики, релизы, риски;
  priorities  «приоритеты»: что важнее и где ранг спорит со сроками;
  plan        «план спринта»: цель, ёмкость, состав, вопросы к планированию.

Вход — номер доски в запросе: «доска 42», адрес доски или `rapidView=42`.
Не названа — отказ до первого вызова модели, даже если в FLOW_BOARDS ровно
одна доска: выбирать доску за оператора нельзя. Ёмкость и доступность можно
назвать словами — «ёмкость 30», «20% команды в отпуске»; без них ёмкость —
медиана скорости последних спринтов.

Следующий ход треда без новой доски Jira не перечитывает. «А если ёмкость 25?»
пересчитывает план кодом по прочитанному снимку, «перепиши короче» — правит
документы по тем же таблицам. Названа другая доска или просьба «обнови» —
доска читается заново.

Граф только читает Jira. Состав спринта в трекере он не меняет: план — это
предложение к планированию, а решают команда и владелец продукта.
Во вложенном прогоне (`runtime.nested`) сбор метрик потока в базу не пишет.
"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from agent import config as cfg
from agent import credentials, db, flow, flow_roles, jira, metrics, pm, pm_jira, pm_roles, sources
from agent import graph as common_graph
from agent.runtime import nested

PIPELINE = pm_roles.PIPELINE


class State(common_graph.State, total=False):
    """Состояние треда плюс прочитанная доска и то, как по ней посчитан план."""

    # Что прочитано и с какой ёмкостью посчитано: доска, когда, названная
    # оператором ёмкость или доступность. По этому полю нода решает, читать
    # ли доску заново и пересчитывать ли план.
    pm: dict
    # Снимок доски (`pm_jira.collect`): задачи, спринты, эпики, история. Нужен
    # следующему ходу — «а если ёмкость 25?» пересчитывается по нему без Jira.
    pm_data: dict
    # Главное посчитанное для человека — подписи вместо ключей.
    pm_view: dict


#: Просьба перечитать ту же доску. «Пересчитай» сюда не входит: «пересчитай
#: план с ёмкостью 25» — это пересчёт по прочитанному, а не новое чтение Jira.
_REFRESH = re.compile(r"\b(?:обнов|перечита|свеж)", re.IGNORECASE)


def missing_board(state: State, config: RunnableConfig) -> str:
    """
    Причина не начинать прогон, или пустая строка.

    О прежней доске проверка узнаёт по артефакту, а не по полю `pm`: её
    вызывают узлы общей сборки, чья схема состояния полей конвейера не знает
    (`routes.make_entry_router`).
    """
    boards = flow_roles.boards_in(sources.question_of(state))
    if len(boards) > 1:
        return (
            "В запросе названо несколько досок: " + ", ".join(map(str, boards))
            + ". Проджект-менеджер ведёт одну доску — назовите её. Прогон остановлен до "
            "первого вызова модели — деньги не потрачены."
        )
    if not boards and not (state.get("artifacts") or {}).get(pm_roles.BOARD):
        known = cfg.flow_boards()
        hint = " Сервер собирает доски: " + ", ".join(map(str, known)) + "." if known else ""
        return (
            "Не названа доска Jira. Напишите её номер: «доска 42: где мы и что брать в "
            "следующий спринт» — или вставьте адрес доски (номер стоит в нём после "
            "rapidView= или /boards/)." + hint
            + " Прогон остановлен до первого вызова модели — деньги не потрачены."
        )
    absent = jira.missing_vars()
    if absent:
        return (
            "Читать доску нечем: " + credentials.missing_message(absent)
            + ". Прогон остановлен до первого вызова модели — деньги не потрачены."
        )
    return ""


def _link(key: str) -> str:
    base = cfg.jira_base_url()
    return f"[{key}]({base}/browse/{key})" if base else key


def _stamp(value: Any) -> str:
    moment = flow.parse_time(value)
    return f"{moment:%d.%m.%Y %H:%M} UTC" if moment else ""


def _overrides(question: str, known: dict) -> tuple[float | None, float | None]:
    """
    Ёмкость и доступность этого хода: названные сейчас, иначе прежние треда.

    Названная ёмкость отменяет доступность и наоборот: одно — решение «берём
    столько», другое — поправка к скорости, и вместе они противоречили бы.
    """
    if pm_roles.capacity_reset(question):
        return None, None
    capacity = pm_roles.capacity_in(question)
    availability = pm_roles.availability_in(question)
    if capacity is not None:
        return capacity, None
    if availability is not None:
        return None, availability
    return known.get("capacity"), known.get("availability")


def board_node(state: State, config: RunnableConfig) -> dict:
    """
    Прочитать доску и посчитать отчёт. Отказ Jira — честная записка ролям.

    Та же доска и ничего нового не названо — нода ничего не делает. Названа
    другая ёмкость — план пересчитывается по снимку треда без чтения Jira.
    """
    question = sources.question_of(state)
    boards = flow_roles.boards_in(question)
    known = state.get("pm") or {}
    board_id = boards[0] if boards else known.get("board_id")
    # Ёмкость названа для своей команды: у другой доски другая команда, и
    # прежняя поправка на неё не переезжает.
    capacity, availability = _overrides(
        question, known if known.get("board_id") == board_id else {}
    )
    snapshot = state.get("pm_data") or {}
    same = (
        known.get("read")
        and snapshot
        and known.get("board_id") == board_id
        and not _REFRESH.search(question)
    )
    if same and (capacity, availability) == (known.get("capacity"), known.get("availability")):
        return {}

    if not same:
        try:
            snapshot = pm_jira.collect(
                int(board_id),
                who=credentials.current_subject() or "",
                store_ok=not nested(config),
            )
        except (jira.JiraError, db.DatabaseUnavailable, ValueError) as exc:
            reason = str(exc)
            return {
                "pm": {"read": False, "board_id": board_id, "error": reason},
                "pm_data": {},
                # Прежние числа треда на экране выглядели бы ответом на этот запрос.
                "pm_view": {"Доска": str(board_id), "Не прочитано": reason},
                "artifacts": {pm_roles.BOARD: f"Доска не прочитана: {reason}."},
                "messages": [AIMessage(content=f"Доска {board_id} не прочитана: {reason}.")],
                "stage": "board",
            }

    report = pm.build(
        snapshot,
        capacity_value=capacity,
        availability=availability,
        velocity_sprints=cfg.pm_velocity_sprints(),
        blocked_statuses=cfg.flow_blocked_statuses(),
    )
    table = pm.render(report, link=_link)
    board = report["board"]
    name = f" «{board['name']}»" if board.get("name") else ""
    plan = report["plan"]
    units = report["units"].label
    if plan["cap"] is None:
        tail = "ёмкость не посчитана — назовите её в запросе («ёмкость 30»)"
    else:
        tail = f"в план до черты ёмкости {flow.number(plan['cap'])} {units} вошло {flow.number(plan['taken'])} {units}"
    note = (
        ("План пересчитан по прочитанной доске" if same else f"Прочитана доска {board_id}{name}")
        + f": {tail}; сигналов приоритета — {len(report['conflicts'])}. "
        "Дальше модель пишет состояние, приоритеты и план по этим числам."
    )
    return {
        "pm": {
            "read": True,
            "board_id": int(board_id),
            "name": str(board.get("name") or ""),
            "read_at": _stamp(snapshot.get("read_at")),
            "capacity": capacity,
            "availability": availability,
        },
        "pm_data": snapshot,
        "pm_view": pm.view(report),
        "artifacts": {pm_roles.BOARD: table},
        "messages": [AIMessage(content=note)],
        "stage": "board",
    }


def build_graph(llm: Any = None):
    """
    Собрать конвейер по описанию из `pm_roles.py`.

    Сборка общая; своего — проверка входа и узел чтения доски. Схема
    состояния обязательна: без неё LangGraph выбросит из обновления поля
    `pm` и `pm_data`, и следующий ход читал бы доску заново.
    """
    return common_graph.build_graph(
        llm=llm,
        pipeline=PIPELINE,
        admission=missing_board,
        prelude=board_node,
        state_schema=State,
    )


# Для Studio / langgraph dev: компилируем БЕЗ чекпоинтера, как и остальные графы.
graph = metrics.observe(build_graph().compile(), "pm")
