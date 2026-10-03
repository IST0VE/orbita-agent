"""
Проджект-менеджер в числах: этап спринта, скорость, ёмкость, план и прогнозы.

Расчёт без сети и без базы, поэтому проверяется на задачах в том виде, в
каком их кладёт в снимок `pm_jira.issue_of`, и на итогах спринтов в том виде,
в каком их считает `flow.sprint_facts`. Каждая проверка — правило, которое
можно сверить руками: доля времени против доли сделанного, медиана скорости,
сумма оценок до черты.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from agent import flow, pm
from agent.pm_jira import ACTIVE, BACKLOG, FUTURE_PLACE, NEXT, QUEUE, WORK

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
TODAY = NOW.date()
SP = pm.Units(pm.POINTS, "SP", "Story Points")
COUNT = pm.Units(pm.COUNT, "задач")


def task(key: str, *, estimate: float | None = 3.0, category: str = flow.NEW,
         place: str = BACKLOG, level: int | None = 3, priority: str = "Medium",
         due: str | None = None, blocks=(), blocked_by=(), flagged: bool = False,
         epic: str = "", versions=(), sprint_id: int | None = None, created: str | None = None,
         subtask: bool = False, is_epic: bool = False, status: str = "") -> dict:
    return {
        "key": key, "summary": f"Задача {key}", "type": "Story", "subtask": subtask,
        "is_epic": is_epic, "status": status or {flow.NEW: "To Do", flow.WORK: "In Progress",
                                                  flow.DONE: "Done"}[category],
        "category": category, "priority": priority, "level": level, "estimate": estimate,
        "flagged": flagged, "due": due, "created": created or "2026-09-01T10:00:00+00:00",
        "epic": epic, "epic_name": f"Эпик {epic}" if epic else "", "versions": list(versions),
        "blocks": list(blocks), "blocked_by": list(blocked_by), "place": place,
        "sprint_id": sprint_id,
    }


def closed(sid: int, start_days_ago: int, *, committed: float, completed: float,
           count: int = 5, done: int = 4, length: int = 14) -> dict:
    start = NOW - timedelta(days=start_days_ago)
    return {
        "sprint_id": sid, "name": f"S-{sid}", "state": "closed",
        "start_at": start.isoformat(), "end_at": (start + timedelta(days=length)).isoformat(),
        "complete_at": (start + timedelta(days=length)).isoformat(),
        "committed_count": count, "committed_points": committed,
        "completed_count": done, "completed_points": completed,
        "completed_committed_count": done, "completed_committed_points": completed,
        "added_count": 1, "removed_count": 0, "carried_count": 1,
    }


SPRINT = {
    "sprint_id": 30, "name": "S-30", "goal": "Оплата частями в бете",
    "start_at": (NOW - timedelta(days=6)).isoformat(),
    "end_at": (NOW + timedelta(days=4)).isoformat(),
}


# --------------------------------------------------------------------------
# Единица объёма
# --------------------------------------------------------------------------
def test_units_are_points_only_when_the_board_actually_estimates():
    board = {"estimate_field": "customfield_10002", "estimate_name": "Story Points"}
    estimated = [closed(1, 40, committed=20, completed=18)]

    assert pm.units_of(board, pm.SCRUM, estimated, []).mode == pm.POINTS
    assert pm.units_of({**board, "hours": True}, pm.SCRUM, estimated, []).label == "ч"
    # Поле в настройке доски есть, а оценок нет ни в спринтах, ни у задач.
    unused = pm.units_of(board, pm.SCRUM, [closed(1, 40, committed=0, completed=0)],
                         [task("A-1", estimate=None)])
    assert unused.mode == pm.COUNT and unused.field == "Story Points"
    assert pm.units_of({}, pm.SCRUM, estimated, []).mode == pm.COUNT
    assert pm.units_of(board, pm.KANBAN, estimated, []).mode == pm.COUNT


# --------------------------------------------------------------------------
# Идущий спринт
# --------------------------------------------------------------------------
def sprint_items(done_points: float, total_points: float = 20.0) -> list[dict]:
    left = total_points - done_points
    return [
        task("A-1", estimate=done_points, category=flow.DONE, place=ACTIVE, sprint_id=30),
        task("A-2", estimate=left, category=flow.WORK, place=ACTIVE, sprint_id=30),
    ]


@pytest.mark.parametrize(
    ("done", "verdict"),
    [(16.0, "опережает"), (10.0, "в графике"), (8.0, "под риском"), (2.0, "отстаёт")],
)
def test_the_sprint_verdict_compares_done_share_with_time_share(done, verdict):
    # Прошло 6 дней из 10 — 60% времени.
    status = pm.sprint_status(SPRINT, sprint_items(done), SP, NOW)

    assert status["time_share"] == pytest.approx(0.6)
    assert status["done_share"] == pytest.approx(done / 20)
    assert status["verdict"] == verdict
    assert status["remaining"] == pytest.approx(20 - done)


def test_the_first_fifth_of_a_sprint_is_too_early_to_judge():
    early = {**SPRINT, "start_at": (NOW - timedelta(days=1)).isoformat(),
             "end_at": (NOW + timedelta(days=9)).isoformat()}

    assert pm.sprint_status(early, sprint_items(0.0), SP, NOW)["verdict"] == "рано судить"


def test_an_open_sprint_past_its_end_is_named_as_such():
    late = {**SPRINT, "end_at": (NOW - timedelta(days=1)).isoformat()}

    assert "срок спринта прошёл" in pm.sprint_status(late, sprint_items(10.0), SP, NOW)["verdict"]


def test_without_estimates_the_share_is_counted_by_issues_and_said_so():
    items = [
        task("A-1", estimate=None, category=flow.DONE, place=ACTIVE),
        task("A-2", estimate=None, place=ACTIVE),
    ]
    status = pm.sprint_status(SPRINT, items, SP, NOW)

    assert status["by_count"] is True
    assert status["done_share"] == pytest.approx(0.5)
    assert status["unestimated"] == 2
    # Остаток для человека — в тех же штуках, что и доля; для плана — в SP,
    # и незакрытая задача без оценки в него не входит, а названа отдельно.
    assert status["remaining"] == 1.0
    assert (status["carry"], status["carry_unestimated"]) == (0.0, 1)


def test_stuck_and_late_issues_are_listed_and_subtasks_ignored():
    items = [
        task("A-1", category=flow.WORK, place=ACTIVE, blocked_by=["X-9"]),
        task("A-2", category=flow.WORK, place=ACTIVE, flagged=True),
        task("A-3", place=ACTIVE),
        task("A-4", place=ACTIVE, subtask=True),
    ]
    status = pm.sprint_status(SPRINT, items, SP, NOW, facts={"added_count": 2, "removed_count": 1})

    assert [item["key"] for item in status["stuck"]] == ["A-1", "A-2"]
    # Половина спринта прошла, а A-3 не начата; подзадача не считается.
    assert [item["key"] for item in status["late"]] == ["A-3"]
    assert status["counts"] == {"done": 0, "work": 2, "todo": 1}
    assert (status["added"], status["removed"]) == (2, 1)


def test_a_configured_blocked_status_counts_as_stuck():
    item = task("A-5", category=flow.WORK, place=ACTIVE, status="Blocked")
    blocked = pm.blocked_names(["blocked", " Ждёт заказчика "])

    assert pm.sprint_status(SPRINT, [item], SP, NOW)["stuck"] == []
    assert pm.sprint_status(SPRINT, [item], SP, NOW, blocked=blocked)["stuck"] == [item]
    assert pm.wip_status([item], [], blocked)["stuck"] == [item]
    assert pm.blocked_status(task("A-6", status="ждёт заказчика"), blocked)


# --------------------------------------------------------------------------
# Скорость и ёмкость
# --------------------------------------------------------------------------
def history() -> list[dict]:
    return [
        closed(1, 100, committed=20, completed=10),
        closed(2, 86, committed=20, completed=18),
        closed(3, 72, committed=22, completed=20),
        closed(4, 58, committed=24, completed=22),
        closed(5, 44, committed=24, completed=24),
        closed(6, 30, committed=26, completed=26),
        closed(7, 16, committed=26, completed=30),
        # Пустой спринт — спринта не было; идущий — ещё не скорость.
        {**closed(8, 200, committed=0, completed=0), "committed_count": 0, "completed_count": 0},
        {**closed(9, 6, committed=20, completed=4), "state": "active"},
    ]


def test_velocity_takes_the_last_closed_sprints_and_their_spread():
    speed = pm.velocity(history(), SP, limit=6)

    assert [row["name"] for row in speed["rows"]] == ["S-2", "S-3", "S-4", "S-5", "S-6", "S-7"]
    # Шесть значений 18, 20, 22, 24, 26, 30: медиана по ближайшему рангу — третье,
    # 20-й перцентиль — второе, 80-й — пятое.
    assert (speed["low"], speed["p50"], speed["high"]) == (20.0, 22.0, 26.0)
    assert speed["n"] == 6 and speed["cadence"] == 14
    assert speed["rows"][0]["kept"] == pytest.approx(18 / 20)


def test_velocity_in_issues_uses_counts():
    speed = pm.velocity(history(), COUNT, limit=3)

    assert [row["completed"] for row in speed["rows"]] == [4.0, 4.0, 4.0]


def test_kanban_throughput_skips_the_running_week_and_sums_two_week_windows():
    monday = datetime(2026, 9, 28, tzinfo=UTC)
    weeks = [{"week": (monday - timedelta(days=7 * n)).isoformat(), "done": n + 1}
             for n in range(7)]
    speed = pm.throughput(weeks, NOW, limit=6)

    # Неделя с 28.09 идёт — не считается. Окна от последней полной назад:
    # (2+3), (4+5), (6+7); неделя с 7 задачами в одиночестве окна не образует.
    assert [row["completed"] for row in speed["rows"]] == [13.0, 9.0, 5.0]
    assert speed["kind"] == pm.KANBAN and speed["cadence"] == 14


def test_capacity_is_the_median_scaled_by_availability_or_the_named_number():
    speed = pm.velocity(history(), SP, limit=6)

    assert pm.capacity(speed)["value"] == 22.0
    scaled = pm.capacity(speed, availability=0.5)
    assert (scaled["low"], scaled["value"], scaled["high"]) == (10.0, 11.0, 13.0)
    assert "доступность 50%" in scaled["source"]
    named = pm.capacity(speed, explicit=30)
    assert named["value"] == 30.0 and named["source"] == "названа в запросе"
    assert pm.capacity(pm.velocity([], SP, 6))["value"] is None


# --------------------------------------------------------------------------
# План
# --------------------------------------------------------------------------
def decisions(planned: dict) -> dict:
    return {row["key"]: row["decision"] for row in planned["rows"]}


def test_the_plan_follows_rank_up_to_both_lines():
    candidates = [task(f"B-{n}", estimate=5.0) for n in range(1, 6)]

    # Ёмкость 20, в идущем спринте не закрыто 8: на новое без переноса — 12.
    planned = pm.plan(candidates, SP, 20.0, 8.0, [])

    assert decisions(planned) == {
        "B-1": pm.TAKE, "B-2": pm.TAKE, "B-3": pm.MAYBE, "B-4": pm.MAYBE, "B-5": pm.NOFIT,
    }
    assert (planned["taken"], planned["maybe"], planned["line"]) == (10.0, 10.0, 12.0)


def test_a_task_that_does_not_fit_is_skipped_but_not_more_than_three_times():
    candidates = [
        task("B-1", estimate=8.0), task("B-2", estimate=13.0), task("B-3", estimate=2.0),
        task("B-4", estimate=13.0), task("B-5", estimate=13.0), task("B-6", estimate=13.0),
        task("B-7", estimate=1.0),
    ]
    planned = pm.plan(candidates, SP, 10.0, 0.0, [])

    assert decisions(planned) == {
        "B-1": pm.TAKE, "B-2": pm.NOFIT, "B-3": pm.TAKE, "B-4": pm.NOFIT,
        "B-5": pm.NOFIT, "B-6": pm.BELOW, "B-7": pm.BELOW,
    }
    assert planned["rows"][1]["note"] == "13 при остатке 2"


def test_unestimated_and_blocked_tasks_are_not_promised():
    candidates = [
        task("B-1", estimate=None),
        task("B-2", blocked_by=["X-1"]),
        task("B-3", blocked_by=["A-7"]),
        task("B-4"),
        task("B-5", blocked_by=["B-4"]),
    ]
    planned = pm.plan(candidates, SP, 20.0, 0.0, active_open=["A-7"])
    rows = {row["key"]: row for row in planned["rows"]}

    assert rows["B-1"]["decision"] == pm.ESTIMATE
    assert rows["B-2"]["decision"] == pm.BLOCKED and rows["B-2"]["note"] == "ждёт: X-1"
    # Блокировка в идущем спринте может успеть — задача берётся с пометкой.
    assert rows["B-3"]["decision"] == pm.TAKE and "A-7" in rows["B-3"]["note"]
    # Блокировка выше в плане снимется раньше.
    assert rows["B-5"]["decision"] == pm.TAKE


def test_a_task_in_a_blocked_status_is_not_taken():
    candidates = [task("B-1", category=flow.WORK, status="Blocked"), task("B-2")]
    blocked = pm.blocked_names(["Blocked"])
    planned = pm.plan(candidates, SP, 20.0, 0.0, [], blocked)

    assert decisions(planned) == {"B-1": pm.BLOCKED, "B-2": pm.TAKE}
    assert planned["rows"][0]["note"] == "статус «Blocked»"
    assert pm.signals(candidates[0], units=SP, cap=20.0, today=TODAY, horizon=None,
                      blocked=blocked)[0] == "в статусе блокировки «Blocked»"
    # Без настройки статус не узнаётся: задача выглядит начатой и годной.
    assert decisions(pm.plan(candidates, SP, 20.0, 0.0, []))["B-1"] == pm.TAKE


def test_in_issues_every_task_weighs_one():
    planned = pm.plan([task(f"B-{n}", estimate=None) for n in range(1, 5)], COUNT, 3.0, 1.0, [])

    assert decisions(planned) == {"B-1": pm.TAKE, "B-2": pm.TAKE, "B-3": pm.MAYBE, "B-4": pm.NOFIT}


def test_without_capacity_there_is_no_line_and_no_decision():
    planned = pm.plan([task("B-1"), task("B-2", estimate=None)], SP, None, 0.0, [])

    assert decisions(planned) == {"B-1": "", "B-2": ""}
    assert planned["line"] is None


# --------------------------------------------------------------------------
# Сигналы и конфликты
# --------------------------------------------------------------------------
def test_signals_name_dates_priority_links_size_and_age():
    old = (NOW - timedelta(days=400)).isoformat()
    item = task("B-1", estimate=13.0, level=1, priority="Highest", due="2026-10-10",
                blocks=["B-2"], blocked_by=["X-1"], flagged=True, created=old)
    found = pm.signals(item, units=SP, cap=20.0, today=TODAY, horizon=date(2026, 10, 16))

    assert found == [
        "срок 10.10 — до конца спринта", "приоритет Highest", "блокирует B-2",
        "заблокирована X-1", "флаг", "крупная: 13 SP при ёмкости 20", "в бэклоге 400 дн.",
    ]
    overdue = pm.signals(task("B-2", due="2026-09-30"), units=SP, cap=20.0, today=TODAY,
                         horizon=None)
    assert overdue == ["просрочена: срок 30.09"]


def test_conflicts_show_where_rank_argues_with_other_signals():
    active = [task("A-1", category=flow.WORK, place=ACTIVE, blocked_by=["B-9"])]
    candidates = [
        task("B-1", estimate=8.0, level=4, priority="Low"),
        task("B-2", estimate=None),
        task("B-3", estimate=13.0, blocked_by=["B-4"]),
        task("B-4", estimate=2.0),
        task("B-5", estimate=8.0, level=1, priority="Highest"),
        task("B-6", estimate=3.0, due="2026-10-08"),
        task("B-9", estimate=8.0),
    ]
    items = [*active, *candidates]
    planned = pm.plan(candidates, SP, 12.0, 0.0, ["A-1"])
    found = pm.conflicts(planned["rows"], active, items, units=SP, cap=12.0, today=TODAY,
                         horizon=date(2026, 10, 16))
    kinds = {item["kind"]: item for item in found}

    # В план вошли B-1 (Low) и B-4 (Medium) — обе ниже Highest у B-5 за чертой.
    assert [row["key"] for row in kinds["priority"]["lower"]] == ["B-1", "B-4"]
    assert kinds["priority"]["key"] == "B-5"
    assert kinds["due"]["key"] == "B-6"
    assert kinds["inversion"] == {"kind": "inversion", "key": "B-3", "blockers": ["B-4"]}
    assert kinds["sprint_waits"]["blockers"] == ["B-9"]
    assert [row["key"] for row in kinds["refine"]["items"]] == ["B-2"]
    assert [row["key"] for row in kinds["big"]["items"]] == ["B-1", "B-3", "B-5", "B-9"]
    assert "overdue" not in kinds


# --------------------------------------------------------------------------
# Эпики и релизы
# --------------------------------------------------------------------------
def child(key: str, category: str, estimate: float | None = 5.0) -> dict:
    return {"key": key, "subtask": False, "category": category, "estimate": estimate}


@pytest.mark.parametrize(
    ("children", "stage"),
    [
        ([], "пустой"),
        ([child("C-1", flow.DONE), child("C-2", flow.DONE)], "готов к закрытию"),
        ([child("C-1", flow.NEW), child("C-2", flow.NEW)], "не начат"),
        ([child("C-1", flow.DONE, 9.0), child("C-2", flow.NEW, 1.0)], "завершается"),
        ([child("C-1", flow.DONE), child("C-2", flow.WORK)], "в работе"),
    ],
)
def test_the_epic_stage_follows_done_share_not_the_epic_status(children, stage):
    rows = pm.epic_rows([{"key": "E-1", "name": "Оплата", "children": children}], SP, set(), [])

    assert rows[0]["stage"] == stage


def test_epic_rows_count_what_is_left_and_what_is_planned():
    epics = [
        {"key": "E-1", "name": "Оплата", "children": [
            child("C-1", flow.DONE), child("C-2", flow.WORK), child("C-3", flow.NEW, None),
        ]},
        {"key": "E-2", "name": "Отчёты", "error": "HTTP 404"},
    ]
    items = [task("C-2", epic="E-1"), task("C-3", epic="E-1")]
    rows = pm.epic_rows(epics, SP, {"C-3"}, items)

    assert rows[0]["share"] == pytest.approx(0.5)
    assert (rows[0]["left"], rows[0]["unestimated"], rows[0]["in_plan"]) == (5.0, 1, 1)
    assert rows[1]["error"] == "HTTP 404"


def test_a_release_forecast_counts_all_work_ahead_in_rank():
    order = [
        task("A-1", estimate=10.0, place=ACTIVE),
        task("B-1", estimate=20.0, versions=["v24"]),
        task("B-2", estimate=10.0),
        task("B-3", estimate=20.0, versions=["v24"]),
        task("B-4", estimate=None, versions=["v25"]),
    ]
    speed = {"p50": 20.0, "low": 10.0, "high": 30.0, "cadence": 14}
    versions = [
        {"id": "v24", "name": "2.4", "release_date": "2026-11-20"},
        {"id": "v25", "name": "2.5", "release_date": None},
        {"id": "v-old", "name": "старая", "release_date": "2026-09-01"},
        {"id": "v-none", "name": "пустая", "release_date": "2026-12-01"},
    ]
    rows = {row["name"]: row for row in pm.release_rows(versions, order, SP, speed, TODAY)}

    # Впереди 2.4 — 60 SP: обычно 3 спринта (42 дня, к 13.11) — успевает при
    # обычной скорости; медленно 6 спринтов — не успевает.
    first = rows["2.4"]
    assert first["ahead"] == 60.0 and first["open"] == 2 and first["own"] == 40.0
    assert first["sprints"]["usual"] == pytest.approx(3.0)
    assert first["when"]["usual"] == date(2026, 11, 13)
    assert first["verdict"] == "успевает при обычной скорости"
    assert rows["2.5"]["unestimated"] == 1
    assert rows["2.5"]["verdict"] == "дата релиза не назначена"
    # У версии без открытых задач на доске прогнозировать нечего.
    assert "пустая" not in rows and "старая" not in rows
    assert first["label"] == "2.4"


def test_versions_with_one_name_in_two_projects_stay_apart():
    order = [
        task("PAY-1", estimate=10.0, versions=["10010"]),
        task("REP-1", estimate=30.0, versions=["20010"]),
    ]
    speed = {"p50": 20.0, "low": 10.0, "high": 30.0, "cadence": 14}
    versions = [
        {"id": "10010", "name": "2.4", "project_id": "1", "release_date": "2026-10-20"},
        {"id": "20010", "name": "2.4", "project_id": "2", "release_date": "2026-10-20"},
    ]
    rows = pm.release_rows(versions, order, SP, speed, TODAY)

    # Имя одно, задачи и прогнозы — свои у каждой версии.
    assert [(row["label"], row["open"], row["ahead"]) for row in rows] == [
        ("2.4 (PAY)", 1, 10.0), ("2.4 (REP)", 1, 40.0),
    ]
    assert [row["verdict"] for row in rows] == ["успевает", "не успевает"]


def test_a_release_without_velocity_has_no_forecast():
    rows = pm.release_rows([{"id": "v24", "name": "2.4", "release_date": "2026-11-20"}],
                           [task("B-1", versions=["v24"])], SP,
                           {"p50": None, "low": None, "high": None}, TODAY)

    assert rows[0]["verdict"] == "нет скорости для прогноза"


# --------------------------------------------------------------------------
# Отчёт целиком
# --------------------------------------------------------------------------
def snapshot(**extra) -> dict:
    items = [
        task("A-1", estimate=8.0, category=flow.DONE, place=ACTIVE, sprint_id=30, epic="E-1"),
        task("A-2", estimate=5.0, category=flow.WORK, place=ACTIVE, sprint_id=30, epic="E-1",
             blocked_by=["X-1"]),
        task("A-3", estimate=3.0, place=ACTIVE, sprint_id=30),
        task("N-1", estimate=8.0, place=NEXT, sprint_id=31, epic="E-1", versions=["v24"]),
        task("N-2", estimate=13.0, place=NEXT, sprint_id=31),
        task("F-1", estimate=5.0, place=FUTURE_PLACE, sprint_id=32),
        task("B-1", estimate=5.0, level=1, priority="Highest", due="2026-10-08"),
        task("B-2", estimate=None),
        task("B-3", estimate=3.0, versions=["v24"]),
    ]
    return {
        "board": {"board_id": 42, "name": "Платежи", "kind": "scrum",
                  "estimate_field": "customfield_10002", "estimate_name": "Story Points"},
        "mode": pm.SCRUM,
        "read_at": NOW.isoformat(),
        "active": [SPRINT],
        "next": [{"sprint_id": 31, "name": "S-31", "start_at": "2026-10-06T09:00:00+00:00",
                  "end_at": "2026-10-16T18:00:00+00:00"},
                 {"sprint_id": 32, "name": "S-32"}],
        "future_total": 2,
        "items": items,
        "epics": [{"key": "E-1", "name": "Оплата частями", "children": [
            child("A-1", flow.DONE, 8.0), child("A-2", flow.WORK), child("N-1", flow.NEW, 8.0),
        ]}],
        "versions": [{"id": "v24", "name": "2.4", "release_date": "2026-11-20"}],
        "history": {"read": True, "sprints": history(), "weeks": [], "oldest": [
            {"key": "A-2", "status": "In Progress", "days": 19.0},
        ], "notes": ["Метрики посчитаны по свежему чтению Jira и в базе не сохранены."]},
        "notes": [],
        **extra,
    }


def test_the_report_is_built_and_rendered_from_a_snapshot():
    report = pm.build(snapshot(), now=NOW)

    assert report["units"].mode == pm.POINTS
    assert report["capacity"]["value"] == 22.0
    # Незакрыто в идущем спринте 5 + 3 = 8; на новое без переноса — 14.
    assert report["plan"]["carry"] == 8.0 and report["plan"]["line"] == 14.0
    assert decisions(report["plan"]) == {
        "N-1": pm.TAKE, "N-2": pm.MAYBE, "B-1": pm.NOFIT, "B-2": pm.ESTIMATE, "B-3": pm.NOFIT,
    }
    assert report["target_total"] == 21.0
    assert {item["kind"] for item in report["conflicts"]} >= {"priority", "due", "refine"}
    assert report["releases"][0]["name"] == "2.4"
    assert report["epics"][0]["in_plan"] == 1

    text = pm.render(report, link=lambda key: f"[{key}]")
    for heading in (
        "## Доска", "## Идущий спринт «S-30»", "## Скорость команды",
        "## Ёмкость следующего спринта", "## План спринта «S-31»", "## Сигналы приоритета",
        "## Этапы эпиков", "## Прогноз релизов", "## Ограничения данных",
    ):
        assert heading in text, heading
    assert "Цель спринта: Оплата частями в бете" in text
    assert "| 1 | [N-1] Задача N-1 | следующий спринт |" in text
    assert "оставить, если идущий спринт закроется" in text
    assert "**Дольше всех в работе**" in text and "[A-2] — In Progress, 19 дн." in text
    assert "Исполнители не читались" in text


def test_every_planned_task_is_in_the_table_however_many():
    items = [task(f"B-{n}", estimate=1.0) for n in range(1, 81)]
    report = pm.build(snapshot(items=items, active=[], next=[], epics=[], versions=[]),
                      capacity_value=60, now=NOW)
    text = pm.render(report)

    assert sum(row["decision"] == pm.TAKE for row in report["plan"]["rows"]) == 60
    assert "Итог: в план — 60 задач" in text
    # Все 60 задач плана названы; за чертой — десять, остальные — числом.
    assert all(f"| B-{n} Задача B-{n} |" in text for n in range(1, 71))
    assert "| B-71 " not in text
    assert "Ниже по рангу ещё 10 кандидатов" in text


def test_a_kanban_board_plans_the_queue_by_throughput():
    monday = datetime(2026, 9, 28, tzinfo=UTC)
    weeks = [{"week": (monday - timedelta(days=7 * n)).isoformat(), "done": 2} for n in range(5)]
    items = [
        task("K-1", category=flow.WORK, place=WORK),
        *[task(f"Q-{n}", place=QUEUE) for n in range(1, 6)],
    ]
    report = pm.build(snapshot(mode=pm.KANBAN, items=items, active=[], next=[], epics=[],
                               history={"read": True, "weeks": weeks, "oldest": []}), now=NOW)

    # Окна по 4 задачи; одна в работе — на новое 3.
    assert report["units"].mode == pm.COUNT
    assert report["capacity"]["value"] == 4.0
    assert decisions(report["plan"])["Q-3"] == pm.TAKE
    assert decisions(report["plan"])["Q-4"] == pm.MAYBE
    text = pm.render(report)
    assert "## Работа в процессе" in text and "## Что брать дальше" in text
    assert "## Пропускная способность" in text


def test_blocked_statuses_reach_the_report():
    found = snapshot()
    found["items"] = [
        *found["items"],
        task("A-9", category=flow.WORK, place=ACTIVE, sprint_id=30, status="Blocked"),
    ]
    text = pm.render(pm.build(found, blocked_statuses=["Blocked"], now=NOW))

    assert "- A-9 Задача A-9 — Blocked; статус блокировки" in text


def test_without_history_the_report_asks_for_capacity_instead_of_guessing():
    report = pm.build(snapshot(history={"read": False, "error": "HTTP 403"}), now=NOW)
    text = pm.render(report)

    assert report["capacity"]["value"] is None
    assert "назовите её в запросе" in text
    assert "История спринтов не прочитана: HTTP 403." in text
    assert pm.view(report)["Ёмкость"] == "не посчитана: назовите в запросе"


def test_the_view_shows_the_main_numbers():
    found = pm.view(pm.build(snapshot(), capacity_value=30, now=NOW))

    assert found["Доска"] == "42 «Платежи»"
    # Сделано 8 SP из 16 за 6 дней из 10.
    assert found["Спринт «S-30»"].startswith("сделано 50% за 60% времени")
    assert found["Ёмкость"] == "30 SP, названа в запросе"
    # Без переноса на новое 30 − 8 = 22: N-1 и N-2 (21 SP); B-1 и B-3 (8 SP) — если спринт закроется.
    assert found["В план"] == "21 SP, ещё 8 если спринт закроется"


@pytest.mark.parametrize("children", [[], [child("C-1", flow.DONE)]])
def test_a_partial_epic_cannot_be_named_empty_or_ready_to_close(children):
    rows = pm.epic_rows(
        [{"key": "E-1", "children": children, "limited": True}], SP, set(), []
    )

    assert rows[0]["stage"] == "прочитан не целиком"


def test_the_current_sprint_reports_blockers_outside_the_candidate_list():
    active = [
        task("A-1", place=ACTIVE, category=flow.WORK,
             blocked_by=["EXT-9", "F-1", "A-2", "B-1"]),
        task("A-2", place=ACTIVE, category=flow.WORK),
    ]
    candidates = [task("B-1")]
    items = [*active, task("F-1", place=FUTURE_PLACE), *candidates]
    planned = pm.plan(candidates, SP, 20.0, 0.0, ["A-1", "A-2"])
    found = pm.conflicts(planned["rows"], active, items, units=SP, cap=20.0,
                         today=TODAY, horizon=None)

    assert [item for item in found if item["kind"] == "sprint_waits"] == [
        {"kind": "sprint_waits", "key": "A-1", "blockers": ["EXT-9", "F-1"]},
    ]
