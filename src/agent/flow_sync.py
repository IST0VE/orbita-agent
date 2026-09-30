"""
Сбор доски Jira в метрики потока: прочитать, разобрать историю, сохранить.

Первый сбор доски читает окно FLOW_HISTORY_DAYS целиком — так базовые
показатели за прошлые полгода появляются сразу. Следующие читают только
задачи, обновлённые с прошлого сбора, с часовым запасом: окно задаётся
относительным JQL (`updated >= -90m`), потому что абсолютная дата в JQL
читается в поясе пользователя Jira, а его пояс сборщику неизвестен.

Спринты и факты задач пересчитываются на каждом сборе целиком, из
сохранённой истории: это арифметика без сети, а правила (статусы аналитики и
блокировки) могли смениться с прошлого раза.

Чьим токеном читать, решает `credentials`, как и везде в проекте: в прогоне
графа и в запросе пользователя — его личным, в фоновом сборе и в скрипте —
общим из `.env`.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from agent import config as cfg
from agent import db, flow, flow_jira, flow_store, jira

_LOG = logging.getLogger(__name__)

#: Запас инкрементного сбора: задача, обновлённая в момент прошлого сбора,
#: не должна проскочить между двумя окнами.
OVERLAP = timedelta(hours=1)
#: Сколько задач писать в базу одной транзакцией.
BATCH = 200


class FlowBusy(RuntimeError):
    """Доску уже собирают: второй сбор параллельно ничего не добавит."""


@dataclass(frozen=True)
class Board:
    """Что сборщику нужно знать о доске, прежде чем читать задачи."""

    meta: dict
    fields: flow.Fields
    categories: dict


def rules() -> flow.Rules:
    return flow.Rules.of(cfg.flow_analysis_statuses(), cfg.flow_blocked_statuses())


def describe(board_id: int, s: jira.Settings) -> Board:
    """Имя, фильтр, поле оценки, поля спринта и флага, категории статусов."""
    meta = flow_jira.board(board_id, s)
    config = flow_jira.configuration(board_id, s)
    fields = flow_jira.fields(config, s)
    meta.update({
        "jql": flow_jira.board_jql(config["filter_id"], s),
        "estimate_field": fields.estimate,
        "hours": fields.hours,
    })
    return Board(meta=meta, fields=fields, categories=flow_jira.categories(s))


def row_of(board: Board, issue: Mapping, rule: flow.Rules, now: datetime) -> dict:
    """Строка задачи для базы: история и посчитанные по ней факты."""
    data = issue.get("fields") or {}
    kind = data.get("issuetype") or {}
    labels = [str(label) for label in data.get("labels") or []]
    row = {
        "board_id": board.meta["board_id"],
        "key": str(issue.get("key") or ""),
        "issue_type": str(kind.get("name") or ""),
        "subtask": bool(kind.get("subtask")),
        "orbita": any(label.startswith(flow.ORBITA_LABEL) for label in labels),
        "timeline": flow.timeline(issue, board.fields, board.categories),
        "jira_updated": flow.parse_time(data.get("updated")),
    }
    return recount(row, rule, now)


_FACT_COLUMNS = (
    "status", "category", "estimate", "created_at", "started_at", "done_at", "cycle_days",
    "lead_days", "reopens", "analysis_returns", "blocked_hours",
)


def recount(row: Mapping, rule: flow.Rules, now: datetime) -> dict:
    """Факты задачи заново из её истории: для новой строки и для сохранённой."""
    facts = flow.facts(row["timeline"], rule, now)
    return {**row, **{name: facts[name] for name in _FACT_COLUMNS}, "wip_days": facts["wip_days"]}


def read(board: Board, s: jira.Settings, *, jql: str, limit: int) -> tuple[list[dict], dict]:
    """
    Задачи по JQL с полной историей. Второе — что вышло: сколько, упёрлись ли в потолок.

    История, обрезанная поиском (у Cloud в поиске её не больше сотни записей),
    дочитывается отдельным запросом на задачу: без начала истории задача
    считалась бы начатой в момент первой уцелевшей записи.
    """
    rule, now = rules(), datetime.now(UTC)
    rows, completed = [], 0
    for issue in flow_jira.search(jql, board.fields, s, limit=limit):
        if flow_jira.truncated(issue):
            issue = {**issue, "changelog": {
                "histories": flow_jira.full_changelog(str(issue.get("key")), s)
            }}
            completed += 1
        try:
            rows.append(row_of(board, issue, rule, now))
        except ValueError as exc:
            _LOG.warning("flow: задача пропущена: %s", exc)
    return rows, {"read": len(rows), "limited": len(rows) >= limit, "changelogs": completed}


def _jql(board: Board, since: datetime | timedelta) -> str:
    """Фильтр доски и окно по обновлению. Окно — в минутах от «сейчас» Jira."""
    span = since if isinstance(since, timedelta) else datetime.now(UTC) - since
    minutes = max(1, math.ceil(span.total_seconds() / 60))
    return f"({board.meta['jql']}) AND updated >= -{minutes}m"


def sprint_rows(board_id: int, sprints: list[dict], rows: list[Mapping], now: datetime) -> list[dict]:
    found = []
    for sprint in sprints:
        facts = flow.sprint_facts(sprint, rows, now)
        if facts:
            found.append({"board_id": board_id, **facts})
    return found


def sync(board_id: int, *, who: str = "", days: int | None = None, full: bool = False) -> dict:
    """
    Собрать доску в базу. Итог — что прочитано и сохранено.

    `days` — окно истории, которое должно быть в базе после сбора. Если прежние
    сборы покрыли окно короче, доска читается заново на всю глубину: иначе отчёт
    за год сравнивал бы полгода с пустотой.
    """
    flow_store.available()
    s = jira.load_settings()
    board = describe(board_id, s)
    store = flow_store.store
    known = store.board(board_id) or {}
    if not store.begin(board.meta, who):
        raise FlowBusy(f"доску {board_id} уже собирают; повторите позже")

    started = datetime.now(UTC)
    window = timedelta(days=max(days or 0, cfg.flow_history_days()))
    needed_start = started - window
    covered = known.get("window_start")
    incremental = (
        not full and known.get("synced_at") is not None
        and covered is not None and covered <= needed_start + timedelta(days=1)
    )
    try:
        since = (known["synced_at"] - OVERLAP) if incremental else needed_start
        fresh, info = read(board, s, jql=_jql(board, since), limit=cfg.flow_max_issues())
        truncated = info["limited"] or (incremental and bool(known.get("truncated")))

        rule, now = rules(), datetime.now(UTC)
        by_key = {row["key"]: row for row in store.issues(board_id)}
        by_key.update({row["key"]: row for row in fresh})
        rows = [recount(row, rule, now) for row in by_key.values()]
        for start in range(0, len(rows), BATCH):
            store.save_issues(rows[start:start + BATCH])

        sprints = sprint_rows(board_id, flow_jira.sprints(board_id, s), rows, now)
        store.save_sprints(board_id, sprints)
        detail = (
            f"прочитано {info['read']}, всего задач {len(rows)}, спринтов {len(sprints)}"
            + ("; упёрлись в FLOW_MAX_ISSUES — прочитаны не все задачи" if info["limited"] else "")
        )
        store.finish(board_id, state=flow_store.OK, detail=detail, synced_at=started,
                     window_start=None if incremental else needed_start, truncated=truncated)
    except Exception as exc:
        store.finish(board_id, state=flow_store.FAILED, detail=str(exc)[:500])
        raise
    return {
        "board_id": board_id,
        "name": board.meta.get("name", ""),
        "incremental": incremental,
        "read": info["read"],
        "issues": len(rows),
        "sprints": len(sprints),
        "truncated": truncated,
        "changelogs": info["changelogs"],
    }


# --------------------------------------------------------------------------
# Показатели для отчёта
# --------------------------------------------------------------------------
@dataclass
class Measured:
    """Показатели доски за период и то, откуда они взялись."""

    board: dict
    current: dict
    previous: dict | None
    notes: list[str]
    stored: bool


def _summaries(rows: list[Mapping], sprints: list[Mapping], period: int, now: datetime,
               covered_from: datetime | None) -> tuple[dict, dict | None, list[str]]:
    rule = rules()
    since = now - timedelta(days=period)
    current = flow.summary(rows, sprints, since=since, until=now, now=now,
                           analysis_configured=bool(rule.analysis))
    notes: list[str] = []
    before_since = since - timedelta(days=period)
    previous = None
    if covered_from is None or covered_from <= before_since + timedelta(days=1):
        previous = flow.summary(rows, sprints, since=before_since, until=since, now=now,
                                analysis_configured=bool(rule.analysis))
    else:
        notes.append(
            "Предыдущего периода нет: история доски собрана с "
            f"{covered_from.astimezone(UTC):%d.%m.%Y}, а для сравнения нужна с "
            f"{before_since:%d.%m.%Y}."
        )
    if not rule.analysis:
        notes.append(
            "Возвраты в аналитику не считаются: статусы аналитики не названы "
            "(FLOW_ANALYSIS_STATUSES)."
        )
    if current["cycle"]["n"] and current["cycle"]["n"] < flow.FEW:
        notes.append(
            f"Закрытых задач со временем в работе всего {current['cycle']['n']}: "
            "перцентили — пересказ отдельных случаев, а не показатель."
        )
    return current, previous, notes


def measure(board_id: int, period: int, *, who: str = "", store_ok: bool = True,
            refresh: bool = True) -> Measured:
    """
    Показатели доски за `period` дней и за столько же до них.

    С базой — сначала сбор (инкрементный, если окно уже покрыто), потом расчёт
    по всему сохранённому; `refresh=False` — только расчёт по сохранённому.
    Без базы или во вложенном прогоне (`store_ok=False`) — чтение окна двух
    периодов прямо сейчас и расчёт в памяти: медленнее, но отчёт получается и
    там, где метрики не копят.
    """
    now = datetime.now(UTC)
    notes: list[str] = []
    if store_ok and cfg.postgres_configured():
        try:
            if refresh:
                sync(board_id, who=who, days=period * 2)
        except FlowBusy as exc:
            notes.append(f"Свежий сбор не выполнен: {exc}. Показаны сохранённые данные.")
        store = flow_store.store
        board = store.board(board_id)
        if board is None:
            raise flow_jira.FlowJiraError(f"доска {board_id} ещё не собрана")
        rows = [recount(row, rules(), now) for row in store.issues(board_id)]
        sprints = store.sprints(board_id)
        if board.get("state") == flow_store.FAILED:
            notes.append(f"Последний сбор доски не удался: {board.get('detail')}.")
        if board.get("truncated"):
            notes.append("Доска прочитана не целиком: упёрлись в потолок FLOW_MAX_ISSUES.")
        current, previous, more = _summaries(rows, sprints, period, now, board.get("window_start"))
        meta = {key: board.get(key) for key in ("board_id", "name", "kind", "hours", "synced_at")}
        return Measured(meta, current, previous, notes + more, stored=True)

    s = jira.load_settings()
    described = describe(board_id, s)
    window_start = now - timedelta(days=period * 2)
    rows, info = read(described, s, jql=_jql(described, window_start), limit=cfg.flow_max_issues())
    sprints = sprint_rows(board_id, flow_jira.sprints(board_id, s), rows, now)
    if info["limited"]:
        notes.append("Доска прочитана не целиком: упёрлись в потолок FLOW_MAX_ISSUES.")
    notes.append("Метрики посчитаны по свежему чтению Jira и в базе не сохранены.")
    current, previous, more = _summaries(rows, sprints, period, now, window_start)
    meta = {**{key: described.meta.get(key) for key in ("board_id", "name", "kind", "hours")},
            "synced_at": now}
    return Measured(meta, current, previous, notes + more, stored=False)


def jsonable(value: Any) -> Any:
    """Показатели для JSON: время — строкой ISO."""
    if isinstance(value, datetime):
        return flow.iso(value)
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


# --------------------------------------------------------------------------
# Фоновый сбор FLOW_BOARDS
# --------------------------------------------------------------------------
def due() -> tuple[int, ...]:
    """Доски фонового сбора, если он включён и есть куда писать."""
    if not cfg.postgres_configured() or cfg.flow_sync_interval_h() <= 0:
        return ()
    return cfg.flow_boards()


def sync_all() -> list[dict]:
    """Пройти по FLOW_BOARDS. Отказ одной доски не останавливает остальные."""
    results = []
    for board_id in due():
        try:
            results.append(sync(board_id, who="service"))
        except (FlowBusy, jira.JiraError, db.DatabaseUnavailable) as exc:
            _LOG.warning("flow: доска %s не собрана: %s", board_id, exc)
            results.append({"board_id": board_id, "error": str(exc)})
    return results
