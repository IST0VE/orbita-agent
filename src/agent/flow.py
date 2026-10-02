"""
Метрики потока задач: из истории задач Jira — в числа, которые можно сверить.

Пилот Orbita оценивается по трудозатратам команды, и сравнивать их не с чем,
пока нет базовых показателей потока: сколько задача идёт от начала работы до
готовности, сколько команда закрывает в неделю, сколько из взятого в спринт
доезжает до конца. Jira хранит всё это в истории изменений задачи, поэтому
базовые показатели за прошлые полгода восстанавливаются сразу, а не
накапливаются три месяца.

Здесь только расчёт: ни сети, ни базы. Чтение Jira — `flow_jira.py`, хранение —
`flow_store.py`, порядок сбора — `flow_sync.py`. Так же, как вердикт SLA в НТ
и проверка ссылок в `critic.py`, числа считает код: модель графа `metrics`
получает готовые таблицы и пишет по ним сводку, а пересчитать их не может.

Три решения, которых не видно из формул:

- **Категория статуса, а не его имя.** «В работе» — это категория
  `indeterminate`, которую Jira даёт каждому статусу. Названия у команд свои
  («In Dev», «Разработка», «Code review»), и список имён разошёлся бы с первой
  же доской. Имена нужны только там, где Jira категории не различает: статусы
  аналитики и блокировки команда называет в настройках (`FLOW_ANALYSIS_STATUSES`,
  `FLOW_BLOCKED_STATUSES`).
- **История хранится целиком, факты считаются из неё.** Сменили список статусов
  аналитики — факты пересчитываются из сохранённой истории, без повторного
  чтения Jira.
- **Людей здесь нет.** Исполнитель не читается и не хранится: метрики командные,
  и сравнивать сотрудников ими не предполагается.
"""

from __future__ import annotations

import math
import re
from bisect import bisect_right
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

#: Категории статусов Jira (`statusCategory.key`).
NEW = "new"
WORK = "indeterminate"
DONE = "done"

#: Метка задач, заведённых Orbita (`actions.LABEL_PREFIX`). Повторена строкой,
#: а не импортом: расчёт не должен тянуть за собой журнал действий.
ORBITA_LABEL = "orbita-op"

#: Меньше стольких задач перцентиль — это пересказ нескольких случаев, а не
#: показатель. В таблице он остаётся, но с пометкой.
FEW = 10


# --------------------------------------------------------------------------
# Время
# --------------------------------------------------------------------------
def parse_time(value: Any) -> datetime | None:
    """Время Jira с поясом: `2026-09-01T10:15:30.000+0300`. Без пояса — UTC."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        found = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    return found if found.tzinfo else found.replace(tzinfo=UTC)


def iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def _days(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 86400


# --------------------------------------------------------------------------
# Поля доски
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Fields:
    """
    Где у этой доски оценка, спринт и флаг блокировки.

    Id полей — кастомные (`customfield_10002`) и у каждой Jira свои, поэтому они
    читаются из конфигурации доски и списка полей, а не угадываются. Имена нужны
    истории изменений: на Data Center её запись называет поле именем
    («Story Points»), а `fieldId` пишет не всегда.
    """

    estimate: str = ""
    estimate_name: str = ""
    sprint: str = ""
    sprint_name: str = "Sprint"
    flagged: str = ""
    flagged_name: str = "Flagged"

    @property
    def hours(self) -> bool:
        """Доска оценивает временем: Jira отдаёт секунды, показываем часы."""
        return self.estimate in {"timeoriginalestimate", "timeestimate"}


@dataclass(frozen=True)
class Rules:
    """Имена статусов, которых категория не различает. Сравниваются без регистра."""

    analysis: frozenset[str] = frozenset()
    blocked: frozenset[str] = frozenset()

    @classmethod
    def of(cls, analysis: Iterable[str] = (), blocked: Iterable[str] = ()) -> Rules:
        clean = lambda names: frozenset(n.strip().casefold() for n in names if n and n.strip())  # noqa: E731
        return cls(clean(analysis), clean(blocked))


# --------------------------------------------------------------------------
# История задачи
# --------------------------------------------------------------------------
_SPRINT_ID = re.compile(r"\bid=(\d+)")
_NUMBER = re.compile(r"\d+")


def sprint_ids(value: Any) -> list[int]:
    """
    Спринты задачи из текущего значения поля Sprint.

    Форм две: объекты с `id` (Cloud и новые Data Center) и строки старого
    формата `com.atlassian.greenhopper.service.sprint.Sprint@1a2b[id=12,...]`.
    """
    found: list[int] = []
    for item in value or []:
        if isinstance(item, Mapping) and item.get("id") is not None:
            try:
                found.append(int(item["id"]))
            except (TypeError, ValueError):
                continue
        elif isinstance(item, str) and (match := _SPRINT_ID.search(item)):
            found.append(int(match.group(1)))
    return sorted(set(found))


def _ids(text: Any) -> list[int]:
    """Спринты из записи истории: `"12, 13"` или пусто."""
    return sorted({int(part) for part in _NUMBER.findall(str(text or ""))})


def _number(item: Mapping, side: str, hours: bool) -> float | None:
    """Оценка из записи истории. Время приходит секундами в `from`/`to`."""
    raw = item.get(side) if hours else item.get(f"{side}String", item.get(side))
    if raw in (None, ""):
        return None
    try:
        value = float(str(raw).replace(",", "."))
    except ValueError:
        return None
    return value / 3600 if hours else value


def estimate_of(value: Any, fields: Fields) -> float | None:
    """Текущая оценка задачи в единицах доски."""
    if value in (None, ""):
        return None
    try:
        found = float(value)
    except (TypeError, ValueError):
        return None
    return found / 3600 if fields.hours else found


def _kind(item: Mapping, fields: Fields) -> str:
    """Какую из четырёх историй двигает запись: статус, спринт, оценку или флаг."""
    field_id = str(item.get("fieldId") or "")
    name = str(item.get("field") or "")
    if field_id == "status" or (not field_id and name.casefold() == "status"):
        return "status"
    if (fields.sprint and field_id == fields.sprint) or (not field_id and name == fields.sprint_name):
        return "sprint"
    if fields.estimate and (
        field_id == fields.estimate or (not field_id and name and name == fields.estimate_name)
    ):
        return "estimate"
    if (fields.flagged and field_id == fields.flagged) or (not field_id and name == fields.flagged_name):
        return "flagged"
    return ""


def _series(created: datetime, changes: list, current: Any, read: Callable[[Mapping, str], Any],
            updated: datetime | None, same: Callable[[Any], Any] = lambda value: value) -> list[list]:
    """
    Значение поля во времени: `[[с какого момента, значение], ...]`.

    Начальное значение — `from` первой записи истории: до первой правки поле
    было таким. Без записей поле не менялось с создания. Если последняя запись
    не совпала с текущим значением (история обрезана или поле правили в обход
    неё), текущее значение ставится с момента последнего обновления задачи:
    конец истории обязан сходиться с тем, что показывает трекер. `same` — по
    чему сравнивать: статус сравнивается по id, переименование его не меняет.
    """
    if not changes:
        return [[created, current]]
    series = [[created, read(changes[0][1], "from")]]
    for at, item in changes:
        series.append([at, read(item, "to")])
    if same(series[-1][1]) != same(current):
        series.append([max(updated or series[-1][0], series[-1][0]), current])
    return series


def timeline(issue: Mapping, fields: Fields, categories: Mapping[str, str]) -> dict:
    """
    История задачи из ответа поиска Jira с `expand=changelog`.

    `categories` — категория статуса по его id (`flow_jira.statuses`). Категория
    пишется в историю сразу: статус, удалённый из схемы позже, иначе потерял бы
    её вместе со всеми фактами задачи.
    """
    data = issue.get("fields") or {}
    created = parse_time(data.get("created"))
    if created is None:
        raise ValueError(f"{issue.get('key')}: у задачи нет даты создания")
    updated = parse_time(data.get("updated"))

    entries = []
    for history in (issue.get("changelog") or {}).get("histories") or []:
        at = parse_time(history.get("created"))
        if at is None:
            continue
        for item in history.get("items") or []:
            if isinstance(item, Mapping) and (kind := _kind(item, fields)):
                entries.append((at, kind, item))
    entries.sort(key=lambda entry: entry[0])
    by_kind: dict[str, list] = {"status": [], "sprint": [], "estimate": [], "flagged": []}
    for at, kind, item in entries:
        by_kind[kind].append((at, item))

    status = data.get("status") or {}
    now_status = (str(status.get("id") or ""), str(status.get("name") or ""))
    statuses = _series(
        created, by_kind["status"], now_status,
        lambda item, side: (str(item.get(side) or ""), str(item.get(f"{side}String") or "")),
        updated,
        same=lambda value: value[0],
    )
    now_category = str((status.get("statusCategory") or {}).get("key") or "")
    known = dict(categories)
    if now_status[0] and now_category:
        known.setdefault(now_status[0], now_category)

    sprint = _series(
        created, by_kind["sprint"],
        sprint_ids(data.get(fields.sprint)) if fields.sprint else [],
        lambda item, side: _ids(item.get(side)),
        updated,
    ) if fields.sprint else []
    estimate = _series(
        created, by_kind["estimate"],
        estimate_of(data.get(fields.estimate), fields) if fields.estimate else None,
        lambda item, side: _number(item, side, fields.hours),
        updated,
    ) if fields.estimate else []
    flagged = _series(
        created, by_kind["flagged"],
        bool(data.get(fields.flagged)) if fields.flagged else False,
        lambda item, side: bool(str(item.get(f"{side}String") or item.get(side) or "").strip()),
        updated,
    ) if fields.flagged else []

    return {
        "created": iso(created),
        "status": [
            [iso(at), sid, name, known.get(sid, "")] for at, (sid, name) in statuses
        ],
        "sprint": [[iso(at), value] for at, value in sprint],
        "estimate": [[iso(at), value] for at, value in estimate],
        "flagged": [[iso(at), value] for at, value in flagged],
    }


# --------------------------------------------------------------------------
# Факты задачи
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Segment:
    start: datetime
    end: datetime | None
    name: str
    category: str


def _segments(timeline_: Mapping) -> list[Segment]:
    points = [
        (parse_time(at), str(name or ""), str(category or ""))
        for at, _sid, name, category in timeline_.get("status") or []
    ]
    points = [point for point in points if point[0] is not None]
    return [
        Segment(start, points[index + 1][0] if index + 1 < len(points) else None, name, category)
        for index, (start, name, category) in enumerate(points)
    ]


def _merged(intervals: list[tuple[datetime, datetime]]) -> float:
    """Часы в объединении интервалов: флаг и статус блокировки не считаются дважды."""
    total, last_end = 0.0, None
    for start, end in sorted(intervals):
        if last_end is not None and start < last_end:
            if end <= last_end:
                continue
            start = last_end
        total += (end - start).total_seconds()
        last_end = end if last_end is None else max(last_end, end)
    return total / 3600


def facts(timeline_: Mapping, rules: Rules, now: datetime) -> dict:
    """
    Что известно о задаче из её истории.

    Готова — задача, которая сейчас в категории «готово»; момент готовности —
    начало последнего непрерывного пребывания там (Done → Closed — одно
    пребывание). Начало работы — первый вход в категорию «в работе»: задача,
    вернувшаяся из готовности, начатой заново не считается, её возврат считает
    `reopens`.
    """
    created = parse_time(timeline_.get("created"))
    segments = _segments(timeline_)
    started = next((s.start for s in segments if s.category == WORK), None)
    done_at = None
    if segments and segments[-1].category == DONE:
        index = len(segments) - 1
        while index > 0 and segments[index - 1].category == DONE:
            index -= 1
        done_at = segments[index].start
    category = segments[-1].category if segments else ""

    reopens = sum(
        1 for before, after in zip(segments, segments[1:], strict=False)
        if before.category == DONE and after.category != DONE
    )
    returns = None
    if rules.analysis:
        returns = sum(
            1 for before, after in zip(segments, segments[1:], strict=False)
            if before.category in (WORK, DONE)
            and before.name.casefold() not in rules.analysis
            and after.name.casefold() in rules.analysis
        )

    until = done_at or now
    blocked: list[tuple[datetime, datetime]] = []
    for segment in segments:
        if segment.name.casefold() in rules.blocked:
            end = min(segment.end or until, until)
            if end > segment.start:
                blocked.append((segment.start, end))
    flags = timeline_.get("flagged") or []
    for index, (at, value) in enumerate(flags):
        start = parse_time(at)
        if not value or start is None:
            continue
        end = parse_time(flags[index + 1][0]) if index + 1 < len(flags) else None
        end = min(end or until, until)
        if end > start:
            blocked.append((start, end))

    estimates = timeline_.get("estimate") or []
    return {
        "created_at": created,
        "started_at": started,
        "done_at": done_at,
        "category": category,
        "status": segments[-1].name if segments else "",
        "cycle_days": _days(started, done_at) if started and done_at and done_at >= started else None,
        "lead_days": _days(created, done_at) if created and done_at else None,
        "wip_days": _days(started, now) if category == WORK and started else None,
        "reopens": reopens,
        "analysis_returns": returns,
        "blocked_hours": _merged(blocked),
        "estimate": estimates[-1][1] if estimates else None,
    }


# --------------------------------------------------------------------------
# Значение в момент времени
# --------------------------------------------------------------------------
def _at(series: Sequence[Sequence], moment: datetime) -> Any:
    """
    Значение ряда в момент `moment`; до первой точки — None (задачи ещё не было).

    Значение — последний элемент точки: у оценки и спринта это само значение,
    у статуса (`[время, id, имя, категория]`) — категория.
    """
    times = [parse_time(point[0]) for point in series]
    index = bisect_right(times, moment) - 1
    return series[index][-1] if index >= 0 else None


def _category_at(timeline_: Mapping, moment: datetime) -> str:
    return str(_at(timeline_.get("status") or [], moment) or "")


def _in_sprint(timeline_: Mapping, sprint: int, moment: datetime) -> bool:
    return sprint in (_at(timeline_.get("sprint") or [], moment) or [])


def _moves(timeline_: Mapping, sprint: int, start: datetime, end: datetime) -> tuple[bool, bool]:
    """
    Входила ли задача в спринт и выходила ли из него между стартом и концом.

    Смотрятся переходы ряда, а не его значения на концах: задача, которую
    добавили и убрали посреди спринта, не видна ни на старте, ни в конце, но
    изменение объёма она сделала.
    """
    joined = left = False
    series = timeline_.get("sprint") or []
    for before, after in zip(series, series[1:], strict=False):
        at = parse_time(after[0])
        if at is None or not start < at <= end:
            continue
        was, now_ = sprint in (before[1] or []), sprint in (after[1] or [])
        joined = joined or (now_ and not was)
        left = left or (was and not now_)
    return joined, left


# --------------------------------------------------------------------------
# Спринт
# --------------------------------------------------------------------------
def sprint_facts(sprint: Mapping, issues: Iterable[Mapping], now: datetime) -> dict | None:
    """
    Взятое, закрытое, добавленное, убранное и перенесённое в одном спринте.

    `issues` — строки задач доски с полями `key`, `subtask`, `timeline`. Подзадачи
    не считаются: оценку и спринт Jira ведёт по родителю, и подзадача в отчёте
    спринта — это его же родитель второй раз.

    Закрытый спринт остаётся в поле Sprint незакрытой задачи вместе со
    следующим («12, 13»), поэтому «была в спринте к концу» читается прямо из
    истории, без знания о том, куда задача уехала потом. У активного спринта
    конец — сейчас, а перенесённого ещё нет.
    """
    start = parse_time(sprint.get("start_at"))
    if start is None or start > now or sprint.get("state") == "future":
        return None
    closed = sprint.get("state") == "closed"
    if closed:
        end = parse_time(sprint.get("complete_at")) or parse_time(sprint.get("end_at")) or now
    else:
        end = now
    sid = int(sprint["sprint_id"])

    counted = {
        "committed_count": 0, "committed_points": 0.0,
        "completed_count": 0, "completed_points": 0.0,
        "completed_committed_count": 0, "completed_committed_points": 0.0,
        "added_count": 0, "removed_count": 0, "carried_count": 0,
    }
    for row in issues:
        if row.get("subtask"):
            continue
        tl = row.get("timeline") or {}
        committed = _in_sprint(tl, sid, start)
        member_end = _in_sprint(tl, sid, end)
        joined, left = _moves(tl, sid, start, end)
        if not (committed or member_end or joined):
            continue
        estimate_start = _at(tl.get("estimate") or [], start) or 0.0
        estimate_end = _at(tl.get("estimate") or [], end) or 0.0
        done = member_end and _category_at(tl, end) == DONE
        if committed:
            counted["committed_count"] += 1
            counted["committed_points"] += float(estimate_start)
        if done:
            counted["completed_count"] += 1
            counted["completed_points"] += float(estimate_end)
            if committed:
                counted["completed_committed_count"] += 1
                counted["completed_committed_points"] += float(estimate_end)
        if joined and not committed:
            counted["added_count"] += 1
        if left and not member_end:
            counted["removed_count"] += 1
        if closed and member_end and not done:
            counted["carried_count"] += 1
    return {
        "sprint_id": sid,
        "name": str(sprint.get("name") or ""),
        "state": str(sprint.get("state") or ""),
        "start_at": start,
        "end_at": parse_time(sprint.get("end_at")),
        "complete_at": parse_time(sprint.get("complete_at")),
        **counted,
    }


# --------------------------------------------------------------------------
# Показатели за период
# --------------------------------------------------------------------------
def percentile(values: Sequence[float], share: float) -> float | None:
    """Перцентиль по ближайшему рангу: значение, которое действительно было."""
    ordered = sorted(value for value in values if value is not None)
    if not ordered:
        return None
    rank = max(1, math.ceil(share * len(ordered)))
    return ordered[rank - 1]


def _spread(values: Sequence[float]) -> dict:
    values = [value for value in values if value is not None]
    return {"n": len(values), "p50": percentile(values, 0.5), "p85": percentile(values, 0.85)}


def _share(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _week(moment: datetime) -> datetime:
    day = moment.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return day - timedelta(days=day.weekday())


def summary(rows: Sequence[Mapping], sprints: Sequence[Mapping], *, since: datetime,
            until: datetime, now: datetime, analysis_configured: bool) -> dict:
    """
    Показатели за период `[since, until)`.

    `rows` — задачи доски с фактами (`facts`) и полями `key`, `subtask`,
    `orbita`; `sprints` — факты спринтов (`sprint_facts`). Готовность задачи
    относит её к периоду, в котором она закрыта; незавершённая работа (WIP)
    считается на конец периода только для текущего периода — у прошлого её
    состояние на тот момент история знает, но спрашивать «что висело месяц
    назад» оператору незачем.
    """
    own = [row for row in rows if not row.get("subtask")]
    done = [row for row in own if row.get("done_at") and since <= row["done_at"] < until]

    weeks: dict[datetime, int] = {}
    cursor = _week(since)
    while cursor < until:
        weeks[cursor] = 0
        cursor += timedelta(days=7)
    for row in done:
        weeks[_week(row["done_at"])] = weeks.get(_week(row["done_at"]), 0) + 1

    current = until >= now - timedelta(minutes=1)
    wip = [row for row in own if row.get("category") == WORK] if current else []
    oldest = sorted(wip, key=lambda row: row.get("wip_days") or 0, reverse=True)[:5]

    def group(items: Sequence[Mapping]) -> dict:
        returns = [row for row in items if (row.get("analysis_returns") or 0) > 0]
        return {
            "done": len(items),
            "cycle": _spread([row.get("cycle_days") for row in items]),
            "returns": len(returns) if analysis_configured else None,
            "returns_share": _share(len(returns), len(items)) if analysis_configured else None,
        }

    in_period = [
        item for item in sprints
        if item.get("start_at") and since <= item["start_at"] < until
    ]
    closed = [item for item in in_period if item.get("state") == "closed" and item["committed_count"]]
    reopened = [row for row in done if row.get("reopens")]
    blocked = [row for row in done if (row.get("blocked_hours") or 0) > 0]
    returned = [row for row in done if (row.get("analysis_returns") or 0) > 0]
    days = max((until - since).total_seconds() / 86400, 1e-9)
    return {
        "since": since,
        "until": until,
        "days": round(days),
        "done": len(done),
        "per_week": len(done) / days * 7,
        "weeks": [{"week": week, "done": count} for week, count in sorted(weeks.items())],
        "cycle": _spread([row.get("cycle_days") for row in done]),
        "lead": _spread([row.get("lead_days") for row in done]),
        "wip": {
            **_spread([row.get("wip_days") for row in wip]),
            "count": len(wip) if current else None,
            "oldest": [
                {"key": row["key"], "status": row.get("status", ""), "days": row.get("wip_days")}
                for row in oldest
            ],
        },
        "reopened": {"n": len(reopened), "share": _share(len(reopened), len(done))},
        "blocked": {
            "n": len(blocked),
            "share": _share(len(blocked), len(done)),
            "hours": _spread([row.get("blocked_hours") for row in blocked]),
        },
        "returns": {
            "configured": analysis_configured,
            "n": len(returned) if analysis_configured else None,
            "share": _share(len(returned), len(done)) if analysis_configured else None,
        },
        "orbita": {
            "orbita": group([row for row in done if row.get("orbita")]),
            "other": group([row for row in done if not row.get("orbita")]),
        },
        "sprints": in_period,
        "predictability": {
            "sprints": len(closed),
            "count": _spread([
                item["completed_committed_count"] / item["committed_count"] for item in closed
            ]),
            "points": _spread([
                item["completed_committed_points"] / item["committed_points"]
                for item in closed if item["committed_points"]
            ]),
        },
    }


# --------------------------------------------------------------------------
# Таблицы для отчёта
# --------------------------------------------------------------------------
def number(value: float | None, digits: int = 1) -> str:
    """Число для таблицы: запятая, без «,0» у целых, прочерк вместо пустоты."""
    if value is None:
        return "—"
    text = f"{value:.{digits}f}".replace(".", ",")
    return text[:-2] if digits and text.endswith(",0") else text


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{round(value * 100)}%"


def _few(spread: Mapping) -> str:
    return " (мало данных)" if 0 < spread.get("n", 0) < FEW else ""


def _delta(now_: float | None, before: float | None, *, percent: bool = False) -> str:
    if now_ is None or before is None:
        return "—"
    diff = now_ - before
    if abs(diff) < 1e-9:
        return "без изменений"
    sign = "+" if diff > 0 else "−"
    return f"{sign}{round(abs(diff) * 100)} п.п." if percent else f"{sign}{number(abs(diff))}"


def _date(value: datetime | None) -> str:
    return value.astimezone(UTC).strftime("%d.%m.%Y") if value else "—"


def render(board: Mapping, current: Mapping, previous: Mapping | None, *,
           notes: Sequence[str] = (), link: Callable[[str], str] | None = None) -> str:
    """
    Показатели таблицами: их читает модель и они же уходят приложением на страницу.

    Предыдущий период стоит рядом с текущим, потому что сводку пишут ради
    вопроса «что изменилось», а изменение без базы — это одно число без смысла.
    """
    link = link or (lambda key: key)
    unit = "ч" if board.get("hours") else "SP"
    before = previous or {}
    title = f"Доска {board.get('board_id')}" + (f" «{board['name']}»" if board.get("name") else "")
    lines = [
        f"{title}. Период: {_date(current['since'])} — {_date(current['until'])} "
        f"({current['days']} дн.)"
        + (f"; предыдущий: {_date(before.get('since'))} — {_date(before.get('until'))}." if previous else "."),
        "",
        "| Показатель | Текущий период | Предыдущий период | Изменение |",
        "| --- | --- | --- | --- |",
    ]

    def row(name: str, value: str, prior: str, change: str) -> None:
        lines.append(f"| {name} | {value} | {prior if previous else '—'} | {change if previous else '—'} |")

    row("Закрыто задач", str(current["done"]), str(before.get("done", "—")),
        _delta(current["done"], before.get("done")))
    row("Закрыто в неделю, в среднем", number(current["per_week"]), number(before.get("per_week")),
        _delta(current["per_week"], before.get("per_week")))
    for key, name in (("cycle", "Время в работе"), ("lead", "Время от создания")):
        for share in ("p50", "p85"):
            now_value = current[key][share]
            prior = (before.get(key) or {}).get(share)
            row(f"{name}, {share}, дн.", number(now_value) + _few(current[key]),
                number(prior), _delta(now_value, prior))
    wip = current["wip"]
    if wip.get("count") is not None:
        row("В работе сейчас (WIP)", str(wip["count"]), "—", "—")
        row("Возраст WIP, p85, дн.", number(wip["p85"]) + _few(wip), "—", "—")
    for key, name in (("reopened", "Переоткрыты после готовности"),
                      ("blocked", "Были в блокировке")):
        row(f"{name}, доля закрытых", _pct(current[key]["share"]),
            _pct((before.get(key) or {}).get("share")),
            _delta(current[key]["share"], (before.get(key) or {}).get("share"), percent=True))
    if current["returns"]["configured"]:
        row("Возвращались в аналитику, доля закрытых", _pct(current["returns"]["share"]),
            _pct((before.get("returns") or {}).get("share")),
            _delta(current["returns"]["share"], (before.get("returns") or {}).get("share"),
                   percent=True))
    predict = current["predictability"]
    if predict["sprints"]:
        prior = (before.get("predictability") or {}).get("count", {}).get("p50")
        row("Выполнено из взятого в спринт, медиана", _pct(predict["count"]["p50"]),
            _pct(prior), _delta(predict["count"]["p50"], prior, percent=True))

    lines += ["", "### Закрыто по неделям", "", "| Неделя с | Закрыто |", "| --- | --- |"]
    lines += [f"| {_date(item['week'])} | {item['done']} |" for item in current["weeks"]]

    if current["sprints"]:
        lines += [
            "", "### Спринты периода", "",
            f"| Спринт | Состояние | Взято, задач / {unit} | Закрыто из взятого | Добавлено | "
            "Убрано | Перенесено | Выполнено из взятого |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        state_names = {"closed": "закрыт", "active": "идёт"}
        for item in current["sprints"]:
            share = _share(item["completed_committed_count"], item["committed_count"])
            lines.append(
                f"| {item['name'] or item['sprint_id']} | {state_names.get(item['state'], item['state'])} | "
                f"{item['committed_count']} / {number(item['committed_points'])} | "
                f"{item['completed_committed_count']} | {item['added_count']} | "
                f"{item['removed_count']} | "
                f"{item['carried_count'] if item['state'] == 'closed' else '—'} | "
                f"{_pct(share)}{' (спринт идёт)' if item['state'] == 'active' else ''} |"
            )

    groups = current["orbita"]
    if groups["orbita"]["done"]:
        lines += [
            "", "### Задачи, заведённые Orbita, и остальные", "",
            "| Группа | Закрыто | Время в работе, p50, дн. | Возвращались в аналитику |",
            "| --- | --- | --- | --- |",
        ]
        for key, name in (("orbita", "Заведены Orbita"), ("other", "Остальные")):
            item = groups[key]
            lines.append(
                f"| {name} | {item['done']} | {number(item['cycle']['p50'])}{_few(item['cycle'])} | "
                f"{_pct(item['returns_share']) if item['returns_share'] is not None else '—'} |"
            )

    if wip.get("oldest"):
        lines += ["", "### Дольше всех в работе", "", "| Задача | Статус | Дней в работе |",
                  "| --- | --- | --- |"]
        lines += [
            f"| {link(item['key'])} | {item['status']} | {number(item['days'])} |"
            for item in wip["oldest"]
        ]

    if notes:
        lines += ["", "### Ограничения данных", ""]
        lines += [f"- {note}" for note in notes]
    return "\n".join(lines)
