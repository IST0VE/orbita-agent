"""
Девятый конвейер: доска Jira -> показатели потока -> сводка для команды.

Граф:

  START -> context -> flow -> summary -> remember -> approve -> publish -> END

  START -> no_input -> remember      (доска не названа или Jira не настроена)

Что делает каждая нода:

  flow       без модели: собирает доску (`flow_sync.measure`) и считает
             показатели за период и за столько же до него (`flow.summary`).
             Таблицы кладутся в артефакты: их читает роль, и они же уходят
             приложением на страницу;
  summary    один вызов модели: сводка по готовым числам (`flow_prompts.py`);
  publish    один документ на прогон: сводка, под ней таблицы.

Вход — номер доски в запросе: «доска 42 за квартал», адрес доски или
`rapidView=42`. Не названа — отказ до первого вызова модели, даже если
в FLOW_BOARDS настроена ровно одна доска: выбирать доску за оператора нельзя.
Период не назван — квартал, и это написано в отчёте.

Следующий ход треда без новой доски и периода не собирает заново: «перепиши
короче» работает по уже посчитанным числам. Названа другая доска или другой
период — показатели считаются заново.

Во вложенном прогоне (`runtime.nested`) база не пишется: вызывающий граф
получает показатели по свежему чтению Jira, а копить метрики — не его дело.
"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph

from agent import config as cfg
from agent import credentials, db, flow, flow_roles, flow_sync, jira, metrics, sources
from agent import graph as common_graph
from agent.runtime import nested

PIPELINE = flow_roles.PIPELINE


class State(common_graph.State, total=False):
    """Состояние треда плюс то, какая доска и за какой период посчитана."""

    # Сводка посчитанного: доска, период, откуда данные и главные числа. Сами
    # таблицы лежат в `artifacts`; по этому полю нода понимает, что считать
    # заново нечего.
    flow: dict
    # То же для человека: подписи вместо ключей, числа в виде таблицы отчёта.
    # Отдельно от `flow`, потому что по `flow` решает код, и русские подписи в
    # нём пришлось бы читать как ключи.
    flow_view: dict


#: Просьба посчитать заново по той же доске и тому же периоду.
_REFRESH = re.compile(r"\b(?:обнов|пересчита|заново|свеж)", re.IGNORECASE)


def _wanted(state: State) -> tuple[list[int], int | None]:
    question = sources.question_of(state)
    return flow_roles.boards_in(question), flow_roles.period_in(question)


def missing_board(state: State, config: RunnableConfig) -> str:
    """
    Причина не начинать прогон, или пустая строка.

    Доска — единственный обязательный вход. Следующий ход уже начатого треда
    проверку проходит: доска названа раньше, и «перепиши короче» — нормальное
    сообщение, а не пустой вход.

    О прежней доске проверка узнаёт по артефакту сбора, а не по полю `flow`:
    её вызывают узлы общей сборки, чья схема состояния полей конвейера не
    знает, и `flow` до неё не доезжает (`routes.make_entry_router`).
    """
    boards, _ = _wanted(state)
    if len(boards) > 1:
        return (
            "В запросе названо несколько досок: " + ", ".join(map(str, boards))
            + ". Отчёт строится по одной — назовите её. Прогон остановлен до первого "
            "вызова модели — деньги не потрачены."
        )
    if not boards and not (state.get("artifacts") or {}).get(flow_roles.FLOW):
        known = cfg.flow_boards()
        hint = (
            " Сервер собирает доски: " + ", ".join(map(str, known)) + "."
            if known else ""
        )
        return (
            "Не названа доска Jira. Напишите её номер: «доска 42 за квартал» — или "
            "вставьте адрес доски (номер стоит в нём после rapidView= или /boards/)."
            + hint + " Прогон остановлен до первого вызова модели — деньги не потрачены."
        )
    absent = jira.missing_vars()
    if absent:
        return (
            "Читать доску нечем: " + credentials.missing_message(absent)
            + ". Прогон остановлен до первого вызова модели — деньги не потрачены."
        )
    return ""


def _date(value: Any) -> str:
    moment = flow.parse_time(value)
    return f"{moment:%d.%m.%Y}" if moment else ""


def _link(key: str) -> str:
    base = cfg.jira_base_url()
    return f"[{key}]({base}/browse/{key})" if base else key


def flow_node(state: State, config: RunnableConfig) -> dict:
    """
    Собрать доску и посчитать показатели. Отказ Jira или базы — честная записка роли.

    Период берётся из запроса, иначе прежний период треда, иначе квартал.
    Ничего нового не названо и показатели уже есть — нода ничего не делает.
    """
    boards, period = _wanted(state)
    known = state.get("flow") or {}
    board_id = boards[0] if boards else known.get("board_id")
    period = period or known.get("period") or flow_roles.DEFAULT_PERIOD
    if (
        known.get("read")
        and known.get("board_id") == board_id
        and known.get("period") == period
        and not _REFRESH.search(sources.question_of(state))
    ):
        return {}

    try:
        measured = flow_sync.measure(
            int(board_id), int(period),
            who=credentials.current_subject() or "",
            store_ok=not nested(config),
        )
    except (jira.JiraError, db.DatabaseUnavailable, flow_sync.FlowBusy, ValueError) as exc:
        reason = str(exc)
        note = f"Показатели доски {board_id} не посчитаны: {reason}."
        return {
            "flow": {"read": False, "board_id": board_id, "period": period, "error": reason},
            # Прежние числа треда на экране выглядели бы как ответ на этот запрос.
            "flow_view": {"Доска": str(board_id), "Не посчитано": reason},
            "artifacts": {flow_roles.FLOW: f"Показатели не посчитаны: {reason}."},
            "messages": [AIMessage(content=note)],
            "stage": "flow",
        }

    notes = list(measured.notes)
    if not flow_roles.period_in(sources.question_of(state)) and not known.get("period"):
        notes.insert(0, f"Период в запросе не назван — взят {period} дн.")
    table = flow.render(measured.board, measured.current, measured.previous, notes=notes, link=_link)
    current = measured.current
    summary = {
        "read": True,
        "board_id": int(board_id),
        "name": measured.board.get("name") or "",
        "period": int(period),
        "since": _date(current["since"]),
        "until": _date(current["until"]),
        "stored": measured.stored,
        "synced": _date(measured.board.get("synced_at")),
        "done": current["done"],
        "cycle_p50": current["cycle"]["p50"],
        "cycle_p85": current["cycle"]["p85"],
        "wip": current["wip"].get("count"),
    }
    cycle = flow.number(current["cycle"]["p50"])
    note = (
        f"Посчитаны показатели доски {board_id}"
        + (f" «{summary['name']}»" if summary["name"] else "")
        + f" за {period} дн.: закрыто задач {current['done']}, время в работе p50 — {cycle} дн. "
        "Дальше модель пишет сводку по этим числам."
    )
    view = {
        "Доска": f"{board_id}" + (f" «{summary['name']}»" if summary["name"] else ""),
        "Период": f"{period} дн.: {summary['since']} — {summary['until']}",
        "Закрыто задач": current["done"],
        "Время в работе p50 / p85, дн.": (
            f"{cycle} / {flow.number(current['cycle']['p85'])}"
        ),
        "В работе сейчас": current["wip"].get("count"),
        "Данные": (
            f"сохранённая история, сбор {summary['synced']}"
            if measured.stored else "свежее чтение Jira, без сохранения"
        ),
    }
    return {
        "flow": summary,
        "flow_view": view,
        "artifacts": {flow_roles.FLOW: table},
        "messages": [AIMessage(content=note)],
        "stage": "flow",
    }


def build_graph(llm: Any = None) -> StateGraph:
    """
    Собрать конвейер по описанию из `flow_roles.py`.

    Сборка общая; своего — проверка входа и узел сбора. Схема состояния
    обязательна: без неё LangGraph выбросит из обновления поле `flow`, и
    следующий ход собирал бы доску заново.
    """
    return common_graph.build_graph(
        llm=llm,
        pipeline=PIPELINE,
        admission=missing_board,
        prelude=flow_node,
        state_schema=State,
    )


# Для Studio / langgraph dev: компилируем БЕЗ чекпоинтера, как и остальные графы.
graph = metrics.observe(build_graph().compile(), "metrics")

