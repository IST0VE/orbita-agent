"""
Проджект-менеджер в числах: где спринт, сколько команда успевает, что брать дальше.

Конвейер `pm` отвечает на три вопроса менеджера проекта: на каком мы этапе,
что сейчас важнее всего и что взять в следующий спринт. Все три держатся на
арифметике — доля прошедшего времени спринта против доли сделанного, скорость
прошлых спринтов, сумма оценок до черты ёмкости, работа впереди релиза, — и
её делает код. Модель получает готовые таблицы и пишет по ним выводы, как и
в метриках потока (`flow.py`): пересчитанное моделью число в отчёте выглядит
так же, как посчитанное кодом, и отличить их читатель не сможет.

Здесь только расчёт: ни сети, ни базы. Чтение доски — `pm_jira.py`, история
спринтов — сбор метрик потока (`flow_sync.measure`).

Решения, которых не видно из формул:

- **Ранг доски — порядок владельца продукта.** План набирается сверху вниз
  по рангу, а не по полю «Приоритет»: ранг выставили люди, и пересобрать его
  молча по другому признаку значило бы решить за них. Где ранг и другие
  признаки спорят (высокий приоритет за чертой, срок раньше конца спринта,
  задача выше своей блокирующей), код это показывает, а решает человек.
- **Две черты вместо одной.** Незакрытое в идущем спринте может переехать
  в следующий, а может успеть. План показывает обе границы: что войдёт, даже
  если переедет всё, и что войдёт, если идущий спринт закроется чисто.
- **Людей здесь нет.** Ёмкость — скорость команды, а не сумма часов
  сотрудников; исполнители не читаются, как и в метриках потока.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from agent import flow
from agent.pm_jira import ACTIVE, BACKLOG, FUTURE, FUTURE_PLACE, NEXT, QUEUE, WORK

SCRUM = "scrum"
KANBAN = "kanban"

POINTS = "points"
HOURS = "hours"
COUNT = "count"

#: Длина спринта, если её не из чего узнать, и горизонт плана канбан-доски.
DEFAULT_CADENCE = 14

#: Доля прошедшего времени спринта, до которой отставание не считается:
#: в первые дни сделанного всегда мало, и «отстаёт» там — шум.
EARLY = 0.2
#: Пороги разрыва «сделано минус прошло» для вердикта спринта.
AHEAD = 0.15
ON_TRACK = -0.10
AT_RISK = -0.25

#: Решения по задаче-кандидату в план.
TAKE = "take"
MAYBE = "maybe"
NOFIT = "nofit"
BELOW = "below"
ESTIMATE = "estimate"
BLOCKED = "blocked"

#: Сколько непоместившихся задач обойти, прежде чем подвести черту. Меньшую
#: задачу ниже по рангу взять можно, но дальше трёх обходов это уже не план
#: по рангу, а план по размеру.
SKIPS = 3
#: Сколько верхних кандидатов проверять на отсутствие оценки: к планированию
#: их надо оценить, глубже — забота следующего уточнения бэклога.
REFINE_TOP = 15
#: Задача крупнее этой доли ёмкости — кандидат на разбиение.
BIG_SHARE = 0.5
#: Дольше стольких дней в бэклоге — повод спросить, нужна ли задача.
STALE_DAYS = 180
#: Сколько задач за чертой показывать под планом: сам план показывается весь.
AFTER_LINE = 10
#: Сколько верхних кандидатов показывать, когда ёмкости нет и черты нет.
RANKED_ROWS = 25


# --------------------------------------------------------------------------
# Единица объёма
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Units:
    """В чём доска меряет объём: оценками или штуками задач."""

    mode: str
    #: Подпись в заголовке столбца: «SP», «ч», «задач».
    label: str
    #: Как поле оценки называется в Jira — для раздела «Данные».
    field: str = ""

    def size(self, item: Mapping) -> float | None:
        """Объём задачи. В штуках у любой задачи он 1; без оценки — None."""
        if self.mode == COUNT:
            return 1.0
        value = item.get("estimate")
        return float(value) if value is not None else None


def units_of(board: Mapping, mode: str, sprints: Sequence[Mapping],
             items: Sequence[Mapping]) -> Units:
    """
    Оценки, если доска ими живёт, иначе штуки.

    Поле оценки в настройке доски ещё не значит, что команда оценивает:
    оценки считаются единицей, если ими закрывались спринты или они стоят
    у задач сейчас. Канбан меряется штуками всегда — его скорость это
    пропускная способность, а она в штуках.
    """
    field = str(board.get("estimate_field") or "")
    name = str(board.get("estimate_name") or "")
    if mode == KANBAN or not field:
        return Units(COUNT, "задач")
    used = any((item.get("completed_points") or 0) > 0 for item in sprints) or any(
        item.get("estimate") is not None for item in items
    )
    if not used:
        return Units(COUNT, "задач", name)
    if board.get("hours"):
        return Units(HOURS, "ч", name)
    return Units(POINTS, "SP", name)


# --------------------------------------------------------------------------
# Мелочи
# --------------------------------------------------------------------------
def _day(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    moment = flow.parse_time(value)
    return moment.date() if moment else None


def _date(value: Any) -> str:
    found = _day(value)
    return f"{found:%d.%m.%Y}" if found else "—"


def _short(value: Any) -> str:
    found = _day(value)
    return f"{found:%d.%m}" if found else "—"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{round(value * 100)}%"


def _num(value: float | None) -> str:
    return flow.number(value)


def _open(item: Mapping) -> bool:
    return item.get("category") != flow.DONE


def _own(items: Iterable[Mapping]) -> list[Mapping]:
    """Задачи, которые планируют: без подзадач и без самих эпиков."""
    return [item for item in items if not item.get("subtask") and not item.get("is_epic")]


def _amount(items: Iterable[Mapping], units: Units) -> float:
    return sum(units.size(item) or 0.0 for item in items)


def blocked_status(item: Mapping, blocked: frozenset[str]) -> bool:
    """
    Стоит ли задача в статусе блокировки, названном командой (FLOW_BLOCKED_STATUSES).

    Категория Jira такого статуса — «в работе», и без списка имён задача
    в «Blocked» выглядела бы начатой и годной в план.
    """
    return bool(blocked) and str(item.get("status") or "").strip().casefold() in blocked


def stuck(item: Mapping, blocked: frozenset[str] = frozenset()) -> bool:
    """Задача стоит: под флагом, ждёт незакрытую задачу или в статусе блокировки."""
    return bool(item.get("flagged") or item.get("blocked_by") or blocked_status(item, blocked))


def blocked_names(names: Iterable[str]) -> frozenset[str]:
    """Статусы блокировки из настройки — так, как их сравнивает `blocked_status`."""
    return frozenset(name.strip().casefold() for name in names if name and name.strip())


def _cell(text: Any) -> str:
    """Текст в ячейку таблицы Markdown: без переводов строк и вертикальных черт."""
    return " ".join(str("" if text is None else text).split()).replace("|", "/")


def _tasks(count: int) -> str:
    """«1 задача», «3 задачи», «5 задач»."""
    tail = count % 100
    if 11 <= tail <= 14 or count % 10 in (0, 5, 6, 7, 8, 9):
        word = "задач"
    elif count % 10 == 1:
        word = "задача"
    else:
        word = "задачи"
    return f"{count} {word}"


def _summary(item: Mapping, limit: int = 70) -> str:
    text = _cell(item.get("summary"))
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _keys(items: Iterable[Mapping], link: Callable[[str], str], limit: int = 6) -> str:
    found = [link(str(item["key"])) for item in items]
    more = len(found) - limit
    return ", ".join(found[:limit]) + (f" и ещё {more}" if more > 0 else "")


# --------------------------------------------------------------------------
# Идущий спринт
# --------------------------------------------------------------------------
def sprint_status(sprint: Mapping, items: Sequence[Mapping], units: Units, now: datetime,
                  facts: Mapping | None = None, blocked: frozenset[str] = frozenset()) -> dict:
    """
    Где спринт: доля прошедшего времени против доли сделанного объёма.

    Сравнение с прямой линией грубое — работа закрывается не равномерно, — но
    оно честно говорит, сколько осталось на сколько дней. Вердикт не
    выносится в первую пятую часть спринта: там сделанного всегда мало.
    `facts` — итог спринта из истории задач (`flow.sprint_facts`): сколько
    добавлено и убрано после старта. Без истории этих строк нет.

    Остатков два, и путать их нельзя. `remaining` — для человека, в тех же
    единицах, что и доля сделанного: без оценок у задач спринта это штуки.
    `carry` — для плана, в единицах плана: без оценок он ноль, а сколько
    незакрытых задач в него не вошло, говорит `carry_unestimated`.
    """
    own = _own(items)
    done = [item for item in own if item.get("category") == flow.DONE]
    work = [item for item in own if item.get("category") == flow.WORK]
    todo = [item for item in own if item.get("category") not in (flow.DONE, flow.WORK)]
    left_open = [item for item in own if _open(item)]
    unestimated = [item for item in own if units.size(item) is None]

    total = _amount(own, units)
    made = _amount(done, units)
    carry = _amount(left_open, units)
    by_count = units.mode != COUNT and not total and bool(own)
    if by_count:
        total, made = float(len(own)), float(len(done))
    done_share = made / total if total else None

    start = flow.parse_time(sprint.get("start_at"))
    end = flow.parse_time(sprint.get("end_at"))
    days = passed = None
    time_share = None
    if start and end and end > start:
        days = (end - start).total_seconds() / 86400
        passed = min(max((now - start).total_seconds() / 86400, 0.0), days)
        time_share = passed / days
    gap = done_share - time_share if done_share is not None and time_share is not None else None

    if end and now > end:
        verdict = "срок спринта прошёл, а он не закрыт"
    elif time_share is None or done_share is None:
        verdict = "не из чего судить"
    elif time_share < EARLY:
        verdict = "рано судить"
    elif gap >= AHEAD:
        verdict = "опережает"
    elif gap >= ON_TRACK:
        verdict = "в графике"
    elif gap >= AT_RISK:
        verdict = "под риском"
    else:
        verdict = "отстаёт"

    late = todo if time_share is not None and time_share >= 0.5 else []
    return {
        "sprint_id": sprint.get("sprint_id"),
        "name": str(sprint.get("name") or ""),
        "goal": str(sprint.get("goal") or ""),
        "start": start,
        "end": end,
        "days": days,
        "passed": passed,
        "left": max(days - passed, 0.0) if days is not None and passed is not None else None,
        "time_share": time_share,
        "total": total,
        "made": made,
        "remaining": float(len(left_open)) if by_count else carry,
        "carry": carry,
        "carry_unestimated": sum(1 for item in left_open if units.size(item) is None),
        "done_share": done_share,
        "by_count": by_count,
        "gap": gap,
        "verdict": verdict,
        "counts": {"done": len(done), "work": len(work), "todo": len(todo)},
        "unestimated": len(unestimated) if units.mode != COUNT else 0,
        "stuck": [item for item in left_open if stuck(item, blocked)],
        "late": late,
        "added": (facts or {}).get("added_count"),
        "removed": (facts or {}).get("removed_count"),
    }


def wip_status(items: Sequence[Mapping], oldest: Sequence[Mapping],
               blocked: frozenset[str] = frozenset()) -> dict:
    """Что в работе у канбан-доски: сколько, что стоит и что дольше всех."""
    own = _own(items)
    return {
        "count": len(own),
        "stuck": [item for item in own if stuck(item, blocked)],
        "oldest": list(oldest or []),
    }


# --------------------------------------------------------------------------
# Скорость и ёмкость
# --------------------------------------------------------------------------
def _spread(values: Sequence[float]) -> dict:
    return {
        "n": len(values),
        "low": flow.percentile(values, 0.2),
        "p50": flow.percentile(values, 0.5),
        "high": flow.percentile(values, 0.8),
    }


def velocity(sprints: Sequence[Mapping], units: Units, limit: int) -> dict:
    """
    Сколько команда закрывала за спринт в последних `limit` закрытых спринтах.

    Пустые спринты (ничего не взято и ничего не закрыто) не считаются: это
    спринт, которого не было, и нулём он тянул бы скорость вниз. Диапазон —
    20-й и 80-й перцентили: при шести спринтах это второй снизу и второй
    сверху, то есть разброс без одного худшего и одного лучшего.
    """
    closed = [
        item for item in sprints
        if item.get("state") == "closed"
        and (item.get("committed_count") or item.get("completed_count"))
        and flow.parse_time(item.get("start_at"))
    ]
    closed.sort(key=lambda item: flow.parse_time(item["start_at"]))
    closed = closed[-limit:]
    rows, values, lengths = [], [], []
    for item in closed:
        start = flow.parse_time(item["start_at"])
        end = flow.parse_time(item.get("complete_at")) or flow.parse_time(item.get("end_at"))
        if units.mode == COUNT:
            committed, completed = item.get("committed_count") or 0, item.get("completed_count") or 0
            kept = item.get("completed_committed_count") or 0
        else:
            committed = item.get("committed_points") or 0.0
            completed = item.get("completed_points") or 0.0
            kept = item.get("completed_committed_points") or 0.0
        values.append(float(completed))
        if end and end > start:
            lengths.append((end - start).total_seconds() / 86400)
        rows.append({
            "name": str(item.get("name") or ""),
            "start": start,
            "end": end,
            "committed": float(committed),
            "completed": float(completed),
            "kept": kept / committed if committed else None,
            "added": item.get("added_count") or 0,
            "carried": item.get("carried_count") or 0,
        })
    cadence = round(flow.percentile(lengths, 0.5)) if lengths else DEFAULT_CADENCE
    return {"kind": SCRUM, "rows": rows, "cadence": max(cadence, 1), **_spread(values)}


def throughput(weeks: Sequence[Mapping], now: datetime, limit: int,
               cadence: int = DEFAULT_CADENCE) -> dict:
    """
    Пропускная способность канбан-доски окнами по `cadence` дней.

    Идущая неделя не считается: она не кончилась, и её неполный счёт выглядел
    бы падением. Окна собираются от последней полной недели назад.
    """
    full = [
        (flow.parse_time(item.get("week")), int(item.get("done") or 0)) for item in weeks
    ]
    full = [(week, done) for week, done in full if week and week + timedelta(days=7) <= now]
    full.sort()
    size = max(cadence // 7, 1)
    rows, values = [], []
    while len(full) >= size and len(rows) < limit:
        window, full = full[-size:], full[:-size]
        done = sum(count for _, count in window)
        values.append(float(done))
        rows.insert(0, {
            "start": window[0][0],
            "end": window[-1][0] + timedelta(days=7),
            "completed": float(done),
        })
    values.reverse()
    return {"kind": KANBAN, "rows": rows, "cadence": size * 7, **_spread(values)}


def capacity(speed: Mapping, *, explicit: float | None = None,
             availability: float | None = None) -> dict:
    """
    Ёмкость следующего спринта: названная оператором или медиана скорости.

    Доступность («в спринте 20% команды в отпуске») умножает и медиану, и
    диапазон. Названная ёмкость — решение оператора, и диапазона у неё нет.
    """
    if explicit is not None:
        return {"value": float(explicit), "low": float(explicit), "high": float(explicit),
                "source": "названа в запросе"}
    if not speed.get("n"):
        return {"value": None, "low": None, "high": None, "source": "истории нет"}
    share = availability if availability is not None else 1.0
    what = "медиана" if speed["kind"] == SCRUM else "медиана пропускной способности"
    source = f"{what} за {speed['n']} " + (
        "посл. спринтов" if speed["kind"] == SCRUM else "посл. окон"
    )
    if availability is not None:
        source += f" × доступность {round(availability * 100)}%"
    return {
        "value": round(speed["p50"] * share, 1),
        "low": round(speed["low"] * share, 1),
        "high": round(speed["high"] * share, 1),
        "source": source,
    }


# --------------------------------------------------------------------------
# План
# --------------------------------------------------------------------------
def plan(candidates: Sequence[Mapping], units: Units, cap: float | None, carry: float,
         active_open: Iterable[str], blocked: frozenset[str] = frozenset()) -> dict:
    """
    Набрать кандидатов по рангу до черты ёмкости.

    Черт две: `line` — ёмкость минус незакрытое в идущем спринте (войдёт, даже
    если переедет всё), `cap` — ёмкость целиком (войдёт, если идущий спринт
    закроется чисто). Задача без оценки в план не берётся: обещать то, что не
    оценено, нельзя. Задача в статусе блокировки (`blocked`) или та, которую
    блокирует незакрытая задача вне плана, тоже: блокировку снимают раньше или
    задачу не берут. Блокирующая задача в идущем спринте не мешает — она может
    успеть, и это отмечено.

    Непоместившуюся задачу код обходит и пробует следующую, но не дальше
    трёх обходов: дальше план набирался бы по размеру, а не по рангу.
    """
    rows: list[dict] = []
    in_sprint = set(active_open)
    planned: set[str] = set()
    used = taken = maybe = 0.0
    skips = 0
    beyond = cap is None
    # Первая задача «если идущий спринт закроется» закрывает первую черту:
    # задача ниже по рангу не встаёт в план раньше той, что выше.
    past_line = False
    line = max((cap or 0.0) - carry, 0.0)
    for item in candidates:
        size = units.size(item)
        row = {**item, "size": size, "decision": "", "note": ""}
        rows.append(row)
        if beyond:
            row["decision"] = BELOW if cap is not None else ""
            continue
        if blocked_status(item, blocked):
            row["decision"], row["note"] = BLOCKED, f"статус «{item.get('status')}»"
            continue
        if size is None:
            row["decision"] = ESTIMATE
            continue
        waits = [key for key in item.get("blocked_by") or [] if key not in planned]
        hard = [key for key in waits if key not in in_sprint]
        if hard:
            row["decision"], row["note"] = BLOCKED, "ждёт: " + ", ".join(hard)
            continue
        if waits:
            row["note"] = "ждёт задачу идущего спринта: " + ", ".join(waits)
        if used + size <= line + 1e-9 and not past_line:
            row["decision"] = TAKE
            taken += size
        elif used + size <= cap + 1e-9:
            row["decision"] = MAYBE
            maybe += size
            past_line = True
        else:
            skips += 1
            row["decision"] = NOFIT
            row["note"] = f"{_num(size)} при остатке {_num(max(cap - used, 0.0))}"
            beyond = skips >= SKIPS
            continue
        used += size
        planned.add(str(item["key"]))
    return {
        "rows": rows,
        "cap": cap,
        "carry": carry,
        "line": None if cap is None else line,
        "taken": taken,
        "maybe": maybe,
        "planned": planned,
    }


def signals(item: Mapping, *, units: Units, cap: float | None, today: date,
            horizon: date | None, blocked: frozenset[str] = frozenset()) -> list[str]:
    """Что о задаче стоит знать на планировании, кроме её места в ранге."""
    found: list[str] = []
    if blocked_status(item, blocked):
        found.append(f"в статусе блокировки «{item.get('status')}»")
    due = _day(item.get("due"))
    if due and _open(item):
        if due < today:
            found.append(f"просрочена: срок {_short(due)}")
        elif horizon and due <= horizon:
            found.append(f"срок {_short(due)} — до конца спринта")
    if (item.get("level") or 9) <= 2:
        found.append(f"приоритет {item.get('priority')}")
    if item.get("blocks"):
        found.append("блокирует " + ", ".join(item["blocks"][:3]))
    if item.get("blocked_by"):
        found.append("заблокирована " + ", ".join(item["blocked_by"][:3]))
    if item.get("flagged"):
        found.append("флаг")
    size = units.size(item)
    if size is None:
        found.append("без оценки")
    elif cap and units.mode != COUNT and size > cap * BIG_SHARE:
        found.append(f"крупная: {_num(size)} {units.label} при ёмкости {_num(cap)}")
    created = _day(item.get("created"))
    if item.get("place") in (BACKLOG, QUEUE) and created and (today - created).days > STALE_DAYS:
        found.append(f"в бэклоге {(today - created).days} дн.")
    return found


def conflicts(rows: Sequence[Mapping], active: Sequence[Mapping], items: Sequence[Mapping], *,
              units: Units, cap: float | None, today: date,
              horizon: date | None) -> list[dict]:
    """
    Где ранг доски спорит с другими признаками приоритета.

    Каждая находка — вопрос к владельцу продукта, а не готовое решение: код
    не знает, почему задача стоит там, где стоит. Порядок — от того, что
    ломает план, к тому, что его только уточняет.
    """
    found: list[dict] = []
    order = {str(item["key"]): index for index, item in enumerate(items)}
    in_plan = [row for row in rows if row["decision"] in (TAKE, MAYBE)]
    left_out = [row for row in rows if row["decision"] in (BELOW, NOFIT)]
    placed = {str(row["key"]) for row in in_plan}

    for row in left_out:
        level = row.get("level")
        if level is not None and level <= 2:
            lower = [other for other in in_plan if (other.get("level") or 3) > level]
            found.append({"kind": "priority", "key": row["key"], "level": level, "lower": lower})
    for row in rows:
        due = _day(row.get("due"))
        if row["decision"] and due and horizon and due <= horizon and str(row["key"]) not in placed:
            found.append({"kind": "due", "key": row["key"], "due": due,
                          "decision": row["decision"]})
    overdue = [
        item for item in _own(items) if _open(item) and _day(item.get("due"))
        and _day(item["due"]) < today
    ]
    if overdue:
        found.append({"kind": "overdue", "items": overdue})
    for row in rows:
        if row["decision"] != BLOCKED:
            continue
        lower = [
            key for key in row.get("blocked_by") or []
            if key in order and order[key] > order.get(str(row["key"]), -1)
        ]
        if lower:
            found.append({"kind": "inversion", "key": row["key"], "blockers": lower})
    current = {str(item["key"]) for item in _own(active) if _open(item)}
    for item in _own(active):
        blockers = [
            key for key in item.get("blocked_by") or []
            if key not in placed and key not in current
        ]
        if _open(item) and blockers:
            found.append({"kind": "sprint_waits", "key": item["key"], "blockers": blockers})
    refine = [
        row for row in rows[:REFINE_TOP] if row["decision"] == ESTIMATE
    ] if units.mode != COUNT else []
    if refine:
        found.append({"kind": "refine", "items": refine})
    big = [
        row for row in rows[:REFINE_TOP]
        if cap and units.mode != COUNT and row.get("size") and row["size"] > cap * BIG_SHARE
    ]
    if big:
        found.append({"kind": "big", "items": big})
    return found


# --------------------------------------------------------------------------
# Эпики и релизы
# --------------------------------------------------------------------------
def epic_rows(epics: Sequence[Mapping], units: Units, planned: set[str],
              items: Sequence[Mapping]) -> list[dict]:
    """
    Этап каждого эпика: сколько в нём сделано, начато и не начато.

    Этап — по доле сделанного объёма во всём эпике, а не по его статусу
    в Jira: эпик «В работе», у которого закрыто всё, — это эпик, который
    забыли закрыть, и его надо назвать, а не пропустить.
    """
    rows = []
    for epic in epics:
        if epic.get("error"):
            rows.append({"key": epic["key"], "name": epic.get("name", ""), "error": epic["error"]})
            continue
        own = [child for child in epic.get("children") or [] if not child.get("subtask")]
        done = [child for child in own if child.get("category") == flow.DONE]
        work = [child for child in own if child.get("category") == flow.WORK]
        todo = [child for child in own if child.get("category") not in (flow.DONE, flow.WORK)]
        total, made = _amount(own, units), _amount(done, units)
        share = made / total if total else (len(done) / len(own) if own else None)
        if epic.get("limited"):
            stage = "прочитан не целиком"
        elif not own:
            stage = "пустой"
        elif len(done) == len(own):
            stage = "готов к закрытию"
        elif not done and not work:
            stage = "не начат"
        elif share is not None and share >= 0.8:
            stage = "завершается"
        else:
            stage = "в работе"
        key = str(epic["key"])
        rows.append({
            "key": key,
            "name": epic.get("name", ""),
            "stage": stage,
            "done": len(done),
            "work": len(work),
            "todo": len(todo),
            "share": share,
            "left": total - made,
            "unestimated": sum(
                1 for child in own if _open(child) and units.size(child) is None
            ),
            "in_plan": sum(
                1 for item in items if item.get("epic") == key and str(item["key"]) in planned
            ),
            "limited": bool(epic.get("limited")),
        })
    return rows


def release_rows(versions: Sequence[Mapping], order: Sequence[Mapping], units: Units,
                 speed: Mapping, today: date) -> list[dict]:
    """
    Успеет ли версия к дате, если команда идёт по рангу доски.

    Работа впереди версии — всё незакрытое по рангу до последней её задачи
    включительно: чтобы сделать её последнюю задачу, команда по рангу сделает
    и всё, что стоит выше. Делится на скорость: быстрая (80-й перцентиль),
    обычная (медиана), медленная (20-й). Задачи без оценки в сумму не входят,
    поэтому прогноз с ними оптимистичен — это сказано в таблице.

    Версия узнаётся по id, а не по имени: у доски на несколько проектов «2.4»
    бывает в каждом, и по имени их задачи слились бы в один релиз с чужими
    сроками. Одинаковые имена в таблице различает ключ проекта.
    """
    cadence = speed.get("cadence") or DEFAULT_CADENCE
    rows = []
    for version in versions:
        name = str(version.get("name") or "")
        wanted = str(version.get("id") or "")
        positions = [
            index for index, item in enumerate(order)
            if wanted and wanted in (item.get("versions") or [])
        ]
        release = _day(version.get("release_date"))
        if not positions:
            continue
        ahead = order[: positions[-1] + 1]
        work = _amount(ahead, units)
        mine = [order[index] for index in positions]
        sprints = {
            label: (work / value if value else None)
            for label, value in (("fast", speed.get("high")), ("usual", speed.get("p50")),
                                 ("slow", speed.get("low")))
        }
        when = {
            label: today + timedelta(days=math.ceil(count * cadence))
            for label, count in sprints.items() if count is not None
        }
        fast, usual, slow = when.get("fast"), when.get("usual"), when.get("slow")
        if release and release < today:
            verdict = "дата релиза прошла"
        elif usual is None:
            verdict = "нет скорости для прогноза"
        elif not release:
            verdict = "дата релиза не назначена"
        elif slow and slow <= release:
            verdict = "успевает"
        elif usual <= release:
            verdict = "успевает при обычной скорости"
        elif fast and fast <= release:
            verdict = "под риском"
        else:
            verdict = "не успевает"
        projects = sorted({str(item["key"]).rsplit("-", 1)[0] for item in mine})
        rows.append({
            "id": wanted,
            "name": name,
            "project": ", ".join(projects) or f"проект {version.get('project_id') or '?'}",
            "release": release,
            "open": len(mine),
            "own": _amount(mine, units),
            "ahead": work,
            "unestimated": sum(1 for item in ahead if units.size(item) is None),
            "sprints": sprints,
            "when": when,
            "verdict": verdict,
        })
    names = [row["name"] for row in rows]
    for row in rows:
        row["label"] = (
            f"{row['name']} ({row['project']})" if names.count(row["name"]) > 1 else row["name"]
        )
    return rows


# --------------------------------------------------------------------------
# Сборка отчёта
# --------------------------------------------------------------------------
def build(snapshot: Mapping, *, capacity_value: float | None = None,
          availability: float | None = None, velocity_sprints: int = 6,
          blocked_statuses: Iterable[str] = (), now: datetime | None = None) -> dict:
    """
    Всё, что код может сказать о доске: из снимка (`pm_jira.collect`) — в отчёт.

    Снимок — прочитанное из Jira, отчёт — посчитанное по нему. Разделены они
    затем, чтобы следующий ход треда («а если ёмкость 25?») пересчитал план
    по тому же снимку, не перечитывая доску. `blocked_statuses` — статусы
    блокировки команды (FLOW_BLOCKED_STATUSES), те же, что у метрик потока.
    """
    now = now or datetime.now(UTC)
    today = now.date()
    blocked = blocked_names(blocked_statuses)
    board = snapshot.get("board") or {}
    mode = snapshot.get("mode") or SCRUM
    items = list(snapshot.get("items") or [])
    history = snapshot.get("history") or {}
    units = units_of(board, mode, history.get("sprints") or [], items)

    if mode == KANBAN:
        speed = throughput(history.get("weeks") or [], now, velocity_sprints)
    else:
        speed = velocity(history.get("sprints") or [], units, velocity_sprints)
    cap = capacity(speed, explicit=capacity_value, availability=availability)

    facts = {
        item.get("sprint_id"): item for item in history.get("sprints") or []
        if item.get("state") == "active"
    }
    if mode == KANBAN:
        active_items = [item for item in items if item.get("place") == WORK]
        status = [wip_status(active_items, history.get("oldest") or [], blocked)]
        candidates = [item for item in _own(items) if item.get("place") == QUEUE and _open(item)]
        carry = float(len(_own(active_items)))
        target = None
        horizon = today + timedelta(days=speed["cadence"])
    else:
        active_items = [item for item in items if item.get("place") == ACTIVE]
        status = [
            sprint_status(
                sprint,
                [item for item in active_items if item.get("sprint_id") == sprint.get("sprint_id")],
                units, now, facts.get(sprint.get("sprint_id")), blocked,
            )
            for sprint in snapshot.get("active") or []
        ]
        candidates = [
            item for item in _own(items) if item.get("place") in (NEXT, BACKLOG) and _open(item)
        ]
        # В единицах плана, а не в тех, которыми показан спринт: без оценок у
        # его задач `remaining` — штуки, и вычесть их из ёмкости в SP нельзя.
        carry = sum(entry["carry"] for entry in status)
        upcoming = snapshot.get("next") or []
        target = upcoming[0] if upcoming else None
        horizon = _day((target or {}).get("end_at"))
        if horizon is None:
            ends = [_day(entry["end"]) for entry in status if entry.get("end")]
            horizon = max([*ends, today]) + timedelta(days=speed["cadence"])

    active_open = [str(item["key"]) for item in _own(active_items) if _open(item)]
    planned = plan(candidates, units, cap["value"], carry, active_open, blocked)
    for row in planned["rows"]:
        row["signals"] = signals(row, units=units, cap=cap["value"], today=today, horizon=horizon,
                                 blocked=blocked)
    found = conflicts(planned["rows"], active_items, items, units=units, cap=cap["value"],
                      today=today, horizon=horizon)

    order = [
        item for item in _own(items)
        if _open(item) and item.get("place") in (ACTIVE, NEXT, FUTURE_PLACE, BACKLOG, WORK, QUEUE)
    ]
    epics = epic_rows(snapshot.get("epics") or [], units, planned["planned"], items)
    releases = release_rows(snapshot.get("versions") or [], order, units, speed, today)

    target_items = [row for row in planned["rows"] if row.get("place") == NEXT]
    return {
        "board": board,
        "mode": mode,
        "units": units,
        "read_at": snapshot.get("read_at"),
        "now": now,
        "status": status,
        "speed": speed,
        "capacity": cap,
        "availability": availability,
        "target": target,
        "target_total": _amount(target_items, units),
        "target_count": len(target_items),
        "horizon": horizon,
        "plan": planned,
        "conflicts": found,
        "epics": epics,
        "releases": releases,
        "history": history,
        "notes": list(snapshot.get("notes") or []),
        "future_total": snapshot.get("future_total") or 0,
        "carry_unestimated": sum(entry.get("carry_unestimated") or 0 for entry in status),
        "blocked": blocked,
    }


# --------------------------------------------------------------------------
# Таблицы для отчёта
# --------------------------------------------------------------------------
_DECISION = {
    (NEXT, TAKE): "оставить",
    (NEXT, MAYBE): "оставить, если идущий спринт закроется",
    (NEXT, NOFIT): "вынести: не помещается",
    (NEXT, BELOW): "вынести: за чертой",
    (NEXT, ESTIMATE): "оценить или вынести",
    (NEXT, BLOCKED): "вынести: заблокирована",
    (TAKE,): "взять",
    (MAYBE,): "взять, если идущий спринт закроется",
    (NOFIT,): "не помещается",
    (BELOW,): "за чертой",
    (ESTIMATE,): "оценить",
    (BLOCKED,): "заблокирована",
}

_PLACE = {NEXT: "следующий спринт", BACKLOG: "бэклог", QUEUE: "очередь"}


def decision_label(row: Mapping) -> str:
    if not row.get("decision"):
        return "—"
    return _DECISION.get((row.get("place"), row["decision"])) or _DECISION[(row["decision"],)]


def _table(head: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join(" --- " for _ in head) + "|"]
    lines += ["| " + " | ".join(_cell(value) for value in row) + " |" for row in rows]
    return lines


def _issue(item: Mapping, link: Callable[[str], str]) -> str:
    return f"{link(str(item['key']))} {_summary(item)}"


def _render_board(report: Mapping) -> list[str]:
    board, units = report["board"], report["units"]
    name = f" «{board['name']}»" if board.get("name") else ""
    kind = "канбан" if report["mode"] == KANBAN else "скрам"
    if report["mode"] == KANBAN:
        unit = "штуки задач: канбан меряется пропускной способностью"
    elif units.mode != COUNT:
        unit = f"оценки, поле «{units.field}» ({units.label})"
    elif units.field:
        unit = f"штуки задач: поле оценки «{units.field}» у задач доски не заполнено"
    else:
        unit = "штуки задач: у доски нет поля оценки"
    rows = [
        ("Доска", f"{board.get('board_id')}{name}, {kind}"),
        ("Прочитано", _when(report.get("read_at"))),
        ("Единица объёма", unit),
    ]
    if report["mode"] != KANBAN:
        target = report.get("target")
        rows.append((
            "Следующий спринт",
            f"«{target['name']}», {_date(target.get('start_at'))} — {_date(target.get('end_at'))}"
            if target and target.get("start_at") else
            f"«{target['name']}», даты не назначены" if target else "ещё не создан в Jira",
        ))
    return ["## Доска", "", *_table(("Что", "Значение"), rows)]


def _when(value: Any) -> str:
    moment = flow.parse_time(value)
    return f"{moment:%d.%m.%Y %H:%M} UTC" if moment else "—"


def _render_sprint(entry: Mapping, units: Units, link: Callable[[str], str],
                   blocked: frozenset[str]) -> list[str]:
    unit = "задач" if entry["by_count"] else units.label
    lines = [f"## Идущий спринт «{entry['name']}»", ""]
    lines.append(f"Цель спринта: {entry['goal']}" if entry["goal"] else "Цель спринта в Jira не записана.")
    lines.append("")
    rows = [
        ("Срок", f"{_date(entry['start'])} — {_date(entry['end'])}"
                 + (f" ({_num(entry['days'])} дн.)" if entry["days"] else "")),
        ("Прошло времени", f"{_pct(entry['time_share'])}"
                           + (f": {_num(entry['passed'])} из {_num(entry['days'])} дн., "
                              f"осталось {_num(entry['left'])}" if entry["days"] else "")),
        ("Сделано объёма", f"{_pct(entry['done_share'])}: {_num(entry['made'])} из "
                           f"{_num(entry['total'])} {unit}"),
        ("Разрыв «сделано − прошло»",
         "—" if entry["gap"] is None else f"{round(entry['gap'] * 100):+d} п.п."),
        ("Вердикт по прямой линии", entry["verdict"]),
        ("Задач: готово / в работе / не начато",
         f"{entry['counts']['done']} / {entry['counts']['work']} / {entry['counts']['todo']}"),
        ("Осталось сделать", f"{_num(entry['remaining'])} {unit}"),
    ]
    if units.mode != COUNT:
        rows.append(("Без оценки", entry["unestimated"]))
    if entry.get("added") is not None:
        rows.append(("Добавлено после старта / убрано", f"{entry['added']} / {entry['removed']}"))
    lines += _table(("Показатель", "Значение"), rows)
    if entry["by_count"]:
        lines += ["", "Оценок у задач спринта нет — доля сделанного посчитана по числу задач."]
    lines += _render_stuck(entry["stuck"], link, blocked)
    if entry["late"]:
        lines += ["", "**Не начаты, хотя прошла половина спринта:**", ""]
        lines += [f"- {_issue(item, link)}" for item in entry["late"][:10]]
        if len(entry["late"]) > 10:
            lines.append(f"- и ещё {len(entry['late']) - 10}")
    return lines


def _render_stuck(items: Sequence[Mapping], link: Callable[[str], str],
                  blocked: frozenset[str]) -> list[str]:
    """Стоящие задачи — и почему каждая стоит: связь, флаг или статус блокировки."""
    if not items:
        return []
    lines = ["", "**Заблокированы, под флагом или в статусе блокировки:**", ""]
    for item in items:
        why = []
        if item.get("blocked_by"):
            why.append("ждёт " + ", ".join(item["blocked_by"]))
        if item.get("flagged"):
            why.append("флаг")
        if blocked_status(item, blocked):
            why.append("статус блокировки")
        lines.append(f"- {_issue(item, link)} — {item.get('status')}; " + "; ".join(why))
    return lines


def _render_wip(entry: Mapping, link: Callable[[str], str], blocked: frozenset[str]) -> list[str]:
    return [
        "## Работа в процессе", "", f"В работе задач: {entry['count']}.",
        *_render_stuck(entry["stuck"], link, blocked),
    ]


def _render_oldest(oldest: Sequence[Mapping], link: Callable[[str], str]) -> list[str]:
    if not oldest:
        return []
    return [
        "", "**Дольше всех в работе** (по истории задач):", "",
        *[f"- {link(str(item['key']))} — {item.get('status')}, {_num(item.get('days'))} дн."
          for item in oldest],
    ]


def _render_speed(report: Mapping) -> list[str]:
    speed, units = report["speed"], report["units"]
    unit = units.label
    if speed["kind"] == KANBAN:
        lines = ["## Пропускная способность", ""]
        if not speed["rows"]:
            return [*lines, "Закрытых задач в истории доски нет — пропускную способность "
                    "посчитать не из чего."]
        lines += _table(
            ("Окно", "Закрыто задач"),
            [(f"{_date(row['start'])} — {_date(row['end'])}", _num(row["completed"]))
             for row in speed["rows"]],
        )
    else:
        lines = ["## Скорость команды", ""]
        if not speed["rows"]:
            return [*lines, "Закрытых спринтов в истории доски нет — скорость посчитать не "
                    "из чего. План без ёмкости: назовите её в запросе («ёмкость 30»)."]
        lines += _table(
            ("Спринт", "Даты", f"Взято на старте, {unit}", f"Сделано, {unit}",
             "Сделано из взятого", "Добавлено", "Перенесено"),
            [(row["name"], f"{_short(row['start'])} — {_short(row['end'])}",
              _num(row["committed"]), _num(row["completed"]), _pct(row["kept"]),
              row["added"], row["carried"]) for row in speed["rows"]],
        )
    few = " — мало данных, диапазон ненадёжен" if speed["n"] < 3 else ""
    lines += [
        "",
        f"Медиана: {_num(speed['p50'])} {unit} за {'окно' if speed['kind'] == KANBAN else 'спринт'}; "
        f"диапазон (20-й — 80-й перцентиль): {_num(speed['low'])} — {_num(speed['high'])}; "
        f"{'окон' if speed['kind'] == KANBAN else 'спринтов'} в расчёте: {speed['n']}{few}. "
        f"Длина {'окна' if speed['kind'] == KANBAN else 'спринта'} для прогнозов — "
        f"{speed['cadence']} дн.",
    ]
    return lines


def _render_capacity(report: Mapping) -> list[str]:
    cap, units, plan_ = report["capacity"], report["units"], report["plan"]
    title = "## Ёмкость следующих двух недель" if report["mode"] == KANBAN else "## Ёмкость следующего спринта"
    if cap["value"] is None:
        return [title, "", "Ёмкость не посчитана: истории нет. Назовите её в запросе — "
                "«ёмкость 30» — и план будет собран по ней."]
    carry_what = "в работе" if report["mode"] == KANBAN else "незакрыто в идущем спринте"
    rows = [
        ("Ёмкость", f"{_num(cap['value'])} {units.label}"
                    + (f" (диапазон {_num(cap['low'])} — {_num(cap['high'])})"
                       if cap["low"] != cap["high"] else "")),
        ("Как посчитана", cap["source"]),
        (carry_what.capitalize(), f"{_num(plan_['carry'])} {units.label}"
                                  + (f" (+{_tasks(report['carry_unestimated'])} без оценки)"
                                     if report["carry_unestimated"] else "")),
        ("На новое, если всё незакрытое переедет", f"{_num(plan_['line'])} {units.label}"),
    ]
    return [title, "", *_table(("Показатель", "Значение"), rows)]


def _render_plan(report: Mapping, link: Callable[[str], str]) -> list[str]:
    units, plan_, target = report["units"], report["plan"], report.get("target")
    if report["mode"] == KANBAN:
        title = "## Что брать дальше"
    else:
        title = f"## План спринта «{target['name']}»" if target else "## План следующего спринта"
    lines = [title, ""]
    rows = plan_["rows"]
    if not rows:
        return [*lines, "Кандидатов нет: бэклог и следующий спринт пусты."]
    if target and report["target_count"]:
        over = report["target_total"] - (plan_["cap"] or 0)
        lines.append(
            f"В спринт «{target['name']}» уже набрано {_tasks(report['target_count'])}, "
            f"{_num(report['target_total'])} {units.label}"
            + (f" — больше ёмкости на {_num(over)} {units.label}." if plan_["cap"] and over > 0 else ".")
        )
        lines.append("")
    # План и уже набранный следующий спринт показываются целиком, сколько бы в
    # них ни было задач: итог «в план — 60 задач» при таблице на 40 строк
    # оставил бы двадцать задач плана без имени. Обрезается только хвост за
    # чертой, а без ёмкости — верх ранга.
    last = max((index for index, row in enumerate(rows) if row["decision"] in (TAKE, MAYBE)),
               default=-1)
    must = max(last + 1, report["target_count"])
    shown = rows[: must + (AFTER_LINE if plan_["cap"] is not None else RANKED_ROWS)]
    lines += _table(
        ("№", "Задача", "Где", "Тип", "Приоритет", f"Оценка, {units.label}" if units.mode != COUNT
         else "Оценка", "Эпик", "Решение", "Сигналы"),
        [
            (index, _issue(row, link), _PLACE.get(row.get("place"), ""), row.get("type"),
             row.get("priority") or "—", _num(row.get("estimate")), row.get("epic") or "—",
             decision_label(row) + (f" ({row['note']})" if row.get("note") else ""),
             "; ".join(row.get("signals") or []) or "—")
            for index, row in enumerate(shown, start=1)
        ],
    )
    if len(rows) > len(shown):
        lines.append(f"\nНиже по рангу ещё {len(rows) - len(shown)} кандидатов — в таблицу не вошли.")
    if plan_["cap"] is not None:
        take = [row for row in rows if row["decision"] == TAKE]
        maybe = [row for row in rows if row["decision"] == MAYBE]
        lines += [
            "",
            f"Итог: в план — {_tasks(len(take))}, {_num(plan_['taken'])} {units.label}"
            + (f"; ещё {_tasks(len(maybe))}, {_num(plan_['maybe'])} {units.label}, — если идущий "
               "спринт закроется без переноса" if maybe else "")
            + f". Ёмкость — {_num(plan_['cap'])} {units.label}.",
        ]
    return lines


def _render_conflicts(report: Mapping, link: Callable[[str], str]) -> list[str]:
    lines = ["## Сигналы приоритета", ""]
    found = report["conflicts"]
    if not found:
        return [*lines, "Код не нашёл мест, где ранг доски спорит со сроками, приоритетом "
                "или блокировками."]
    units = report["units"]
    for item in found:
        kind = item["kind"]
        if kind == "priority":
            lower = item["lower"]
            lines.append(
                f"- **Высокий приоритет за чертой:** {link(item['key'])}"
                + (f"; при этом в план вошли задачи с приоритетом ниже: {_keys(lower, link)}."
                   if lower else ".")
            )
        elif kind == "due":
            lines.append(
                f"- **Срок раньше конца спринта, а в плане задачи нет:** {link(item['key'])} — "
                f"срок {_date(item['due'])}, решение: {_DECISION[(item['decision'],)]}."
            )
        elif kind == "overdue":
            lines.append(
                "- **Просрочены:** " + ", ".join(
                    f"{link(str(entry['key']))} (срок {_short(entry['due'])})" for entry in item["items"][:10]
                ) + "."
            )
        elif kind == "inversion":
            lines.append(
                f"- **Задача выше в ранге, чем её блокировка:** {link(item['key'])} ждёт "
                f"{', '.join(item['blockers'])}, а те стоят ниже — без их подъёма её не взять."
            )
        elif kind == "sprint_waits":
            lines.append(
                f"- **Идущий спринт ждёт задачу вне плана:** {link(item['key'])} заблокирована "
                f"{', '.join(item['blockers'])}."
            )
        elif kind == "refine":
            lines.append(
                f"- **Без оценки среди {REFINE_TOP} верхних кандидатов:** {_keys(item['items'], link, 10)} — "
                "оценить до планирования, иначе в план их не взять."
            )
        elif kind == "big":
            lines.append(
                "- **Крупные:** " + ", ".join(
                    f"{link(str(row['key']))} ({_num(row['size'])} {units.label})" for row in item["items"]
                ) + " — больше половины ёмкости; кандидаты на разбиение."
            )
    return lines


def _render_epics(report: Mapping, link: Callable[[str], str]) -> list[str]:
    rows = report["epics"]
    if not rows:
        return []
    units = report["units"]
    lines = ["## Этапы эпиков", ""]
    lines += _table(
        ("Эпик", "Этап", "Готово / в работе / не начато", "Сделано объёма",
         f"Осталось, {units.label}", "Без оценки", "В плане спринта"),
        [
            (f"{link(row['key'])} {_cell(row.get('name'))}", "не прочитан", "—", "—", "—", "—", "—")
            if row.get("error") else
            (f"{link(row['key'])} {_cell(row.get('name'))}", row["stage"],
             f"{row['done']} / {row['work']} / {row['todo']}", _pct(row["share"]),
             _num(row["left"]), row["unestimated"], row["in_plan"])
            for row in rows
        ],
    )
    failed = [row for row in rows if row.get("error")]
    if failed:
        lines.append("")
        lines += [f"Эпик {row['key']} не прочитан: {row['error']}." for row in failed]
    if any(row.get("limited") for row in rows if not row.get("error")):
        lines += ["", "У части эпиков задач больше, чем прочитано: доли — по прочитанным."]
    return lines


def _render_releases(report: Mapping) -> list[str]:
    rows = report["releases"]
    if not rows:
        return []
    units = report["units"]
    lines = ["## Прогноз релизов", ""]
    lines += _table(
        ("Версия", "Дата релиза", "Открыто задач", f"Работа впереди, {units.label}",
         "Спринтов: быстро / обычно / медленно", "Готово к (обычно)", "Вердикт"),
        [
            (row["label"], _date(row["release"]), row["open"],
             _num(row["ahead"]) + (f" (+{row['unestimated']} без оценки)" if row["unestimated"] else ""),
             " / ".join(_num(row["sprints"][label]) for label in ("fast", "usual", "slow")),
             _date(row["when"].get("usual")), row["verdict"])
            for row in rows
        ],
    )
    lines += [
        "",
        "Работа впереди — всё незакрытое по рангу до последней задачи версии: идя по рангу, "
        "команда сделает и то, что выше. Прогноз — от сегодняшнего дня, если вся ёмкость "
        "команды уходит на эту доску; задачи версии на других досках здесь не видны.",
    ]
    return lines


def _render_notes(report: Mapping) -> list[str]:
    notes = list(report["notes"])
    history = report["history"] or {}
    if history.get("read") is False and history.get("error"):
        notes.append(f"История спринтов не прочитана: {history['error']}.")
    notes += [f"История доски: {note}" for note in history.get("notes") or []]
    speed = report["speed"]
    if speed["n"] and speed["n"] < 3:
        notes.append(f"Скорость посчитана по {speed['n']} окнам — диапазон ненадёжен.")
    if report["units"].mode != COUNT:
        notes.append(
            "Задачи без оценки в суммы объёма не входят: план, остаток спринта и работа "
            "впереди релизов с ними больше, чем в таблицах."
        )
    if report["future_total"] > FUTURE:
        notes.append(
            f"Будущих спринтов {report['future_total']}, прочитаны первые {FUTURE}: задачи "
            "остальных в прогноз релизов не вошли."
        )
    notes.append(
        "Исполнители не читались: ёмкость командная, загрузку людей отчёт не показывает."
    )
    return ["## Ограничения данных", "", *[f"- {note}" for note in notes]]


def render(report: Mapping, *, link: Callable[[str], str] = lambda key: key) -> str:
    """Отчёт кода целиком — Markdown, который читают роли и который уходит приложением."""
    units = report["units"]
    lines = _render_board(report)
    if report["mode"] == KANBAN:
        for entry in report["status"]:
            lines += ["", *_render_wip(entry, link, report["blocked"])]
    elif report["status"]:
        for entry in report["status"]:
            lines += ["", *_render_sprint(entry, units, link, report["blocked"])]
    else:
        lines += ["", "## Идущий спринт", "", "Идущего спринта на доске нет."]
    lines += _render_oldest((report["history"] or {}).get("oldest") or [], link)
    for part in (
        _render_speed(report),
        _render_capacity(report),
        _render_plan(report, link),
        _render_conflicts(report, link),
        _render_epics(report, link),
        _render_releases(report),
        _render_notes(report),
    ):
        if part:
            lines += ["", *part]
    return "\n".join(lines).strip() + "\n"


def view(report: Mapping) -> dict:
    """Главное посчитанное — строками для панели справа, без открытия документа."""
    board, units, cap, plan_ = report["board"], report["units"], report["capacity"], report["plan"]
    found = {
        "Доска": f"{board.get('board_id')}" + (f" «{board['name']}»" if board.get("name") else ""),
    }
    for entry in report["status"]:
        if report["mode"] == KANBAN:
            found["В работе"] = entry["count"]
        else:
            found[f"Спринт «{entry['name']}»"] = (
                f"сделано {_pct(entry['done_share'])} за {_pct(entry['time_share'])} времени — "
                f"{entry['verdict']}"
            )
    speed = report["speed"]
    if speed["n"]:
        found["Скорость"] = (
            f"{_num(speed['p50'])} {units.label} ({_num(speed['low'])} — {_num(speed['high'])}), "
            f"окон: {speed['n']}"
        )
    found["Ёмкость"] = (
        f"{_num(cap['value'])} {units.label}, {cap['source']}" if cap["value"] is not None
        else "не посчитана: назовите в запросе"
    )
    if plan_["cap"] is not None:
        found["В план"] = (
            f"{_num(plan_['taken'])} {units.label}"
            + (f", ещё {_num(plan_['maybe'])} если спринт закроется" if plan_["maybe"] else "")
        )
    found["Сигналов приоритета"] = len(report["conflicts"])
    return found
