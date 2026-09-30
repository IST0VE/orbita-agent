"""
Метрики потока: из истории задачи Jira — в числа, которые можно сверить руками.

Расчёт без сети и без базы, поэтому проверяется на задачах, собранных здесь
же в том виде, в каком их отдаёт поиск Data Center с `expand=changelog`:
статус в истории — id, поле называется именем («Story Points», «Sprint»),
спринты — строкой «12, 13».
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent import flow

FIELDS = flow.Fields(
    estimate="customfield_10002",
    estimate_name="Story Points",
    sprint="customfield_10005",
    flagged="customfield_10021",
)
CATEGORIES = {"1": "new", "3": "indeterminate", "4": "indeterminate", "10002": "done", "6": "done"}
NAMES = {"1": "To Do", "3": "In Progress", "4": "Analysis", "10002": "Done", "6": "Closed"}
NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def at(day: int, hour: int = 10, month: int = 6) -> str:
    return datetime(2026, month, day, hour, tzinfo=UTC).isoformat()


def status(src: str, dst: str) -> dict:
    return {"field": "status", "fieldtype": "jira", "from": src, "fromString": NAMES[src],
            "to": dst, "toString": NAMES[dst]}


def sprint(src: str, dst: str) -> dict:
    return {"field": "Sprint", "fieldtype": "custom", "from": src, "fromString": "", "to": dst,
            "toString": ""}


def points(src: str | None, dst: str | None) -> dict:
    return {"field": "Story Points", "fieldtype": "custom", "from": None, "fromString": src,
            "to": None, "toString": dst}


def flag(on: bool) -> dict:
    return {"field": "Flagged", "fieldtype": "custom", "from": None, "fromString": None,
            "to": "[10019]" if on else None, "toString": "Impediment" if on else None}


def issue(key: str, created: str, now_status: str, changes: list[tuple[str, dict]], *,
          sprints: list | None = None, estimate: float | None = None, flagged: bool = False,
          subtask: bool = False, labels: tuple[str, ...] = ()) -> dict:
    return {
        "key": key,
        "fields": {
            "created": created,
            "updated": changes[-1][0] if changes else created,
            "issuetype": {"name": "Sub-task" if subtask else "Story", "subtask": subtask},
            "status": {"id": now_status, "name": NAMES[now_status],
                       "statusCategory": {"key": CATEGORIES[now_status]}},
            "labels": list(labels),
            "customfield_10002": estimate,
            "customfield_10005": sprints or [],
            "customfield_10021": [{"value": "Impediment"}] if flagged else None,
        },
        "changelog": {"histories": [{"created": when, "items": [item]} for when, item in changes]},
    }


NO_RULES = flow.Rules()


def row(raw: dict, rules: flow.Rules = NO_RULES, now: datetime = NOW) -> dict:
    timeline = flow.timeline(raw, FIELDS, CATEGORIES)
    kind = raw["fields"]["issuetype"]
    return {
        "key": raw["key"],
        "subtask": kind["subtask"],
        "orbita": any(label.startswith(flow.ORBITA_LABEL) for label in raw["fields"]["labels"]),
        "timeline": timeline,
        **flow.facts(timeline, rules, now),
    }


# --------------------------------------------------------------------------
# История и факты задачи
# --------------------------------------------------------------------------
WORKED = issue(
    "PAY-1", at(1, 9), "6",
    [
        (at(2), status("1", "3")),
        (at(3), status("3", "4")),
        (at(4), status("4", "3")),
        (at(5), flag(True)),
        (at(5, 22), flag(False)),
        (at(6), status("3", "10002")),
        (at(7), status("10002", "3")),
        (at(8), status("3", "10002")),
        (at(8, 12), status("10002", "6")),
    ],
    estimate=3,
)


def test_work_starts_at_the_first_entry_into_work_and_ends_at_the_last_entry_into_done():
    """
    Done → Closed — одно пребывание в готовности, а возврат из готовности —
    переоткрытие, а не новое начало работы.
    """
    facts = row(WORKED, flow.Rules.of(analysis=["analysis"]))

    assert facts["started_at"] == datetime(2026, 6, 2, 10, tzinfo=UTC)
    assert facts["done_at"] == datetime(2026, 6, 8, 10, tzinfo=UTC)
    assert facts["cycle_days"] == pytest.approx(6.0)
    assert facts["lead_days"] == pytest.approx(7 + 1 / 24)
    assert facts["reopens"] == 1
    assert facts["analysis_returns"] == 1
    assert facts["category"] == flow.DONE
    assert facts["wip_days"] is None


def test_returns_to_analysis_are_not_counted_when_the_statuses_are_not_named():
    """Ноль и «не считалось» — разные ответы: без названных статусов — None."""
    assert row(WORKED)["analysis_returns"] is None


def test_the_first_analysis_after_the_backlog_is_not_a_return():
    fresh = issue("PAY-2", at(1, 9), "4", [(at(2), status("1", "4"))])

    assert row(fresh, flow.Rules.of(analysis=["Analysis"]))["analysis_returns"] == 0


def test_a_flag_and_a_blocked_status_at_the_same_time_are_counted_once():
    blocked = issue(
        "PAY-3", at(1, 9), "3",
        [
            (at(2, 0), status("1", "3")),
            (at(2, 6), flag(True)),
            (at(2, 12), status("3", "4")),
            (at(2, 18), flag(False)),
            (at(3, 0), status("4", "3")),
        ],
    )
    facts = row(blocked, flow.Rules.of(blocked=["analysis"]))

    # Флаг 06:00–18:00, статус 12:00–00:00: вместе 06:00–00:00, восемнадцать часов.
    assert facts["blocked_hours"] == pytest.approx(18)
    assert facts["category"] == flow.WORK
    assert facts["wip_days"] == pytest.approx((NOW - datetime(2026, 6, 2, tzinfo=UTC)).total_seconds() / 86400)


def test_the_end_of_the_history_agrees_with_what_the_tracker_shows():
    """
    История могла быть обрезана или поле правили мимо неё: последний статус
    истории — «в работе», а трекер показывает «готово». Верить надо трекеру.
    """
    raw = issue("PAY-4", at(1, 9), "10002", [(at(2), status("1", "3"))])
    raw["fields"]["updated"] = at(9)
    facts = row(raw)

    assert facts["done_at"] == datetime(2026, 6, 9, 10, tzinfo=UTC)


def test_a_renamed_status_is_the_same_status():
    raw = issue("PAY-5", at(1, 9), "3", [(at(2), status("1", "3"))])
    raw["fields"]["status"]["name"] = "Разработка"

    assert len(flow.timeline(raw, FIELDS, CATEGORIES)["status"]) == 2


def test_an_issue_that_never_changed_status_has_no_start():
    facts = row(issue("PAY-6", at(1, 9), "1", []))

    assert (facts["started_at"], facts["done_at"], facts["cycle_days"]) == (None, None, None)
    assert facts["category"] == flow.NEW


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ([{"id": 12, "name": "S12"}, {"id": "13"}], [12, 13]),
        (["com.atlassian.greenhopper.service.sprint.Sprint@1a2b[id=12,rapidViewId=3,state=CLOSED]"], [12]),
        (None, []),
        (["мусор без id"], []),
    ],
)
def test_the_sprint_field_is_read_in_both_formats(value, expected):
    assert flow.sprint_ids(value) == expected


def test_a_time_estimate_is_shown_in_hours():
    fields = flow.Fields(estimate="timeoriginalestimate")
    raw = issue("PAY-7", at(1, 9), "1", [])
    raw["fields"]["timeoriginalestimate"] = 7200

    assert fields.hours
    assert flow.timeline(raw, fields, CATEGORIES)["estimate"][-1][1] == 2.0


# --------------------------------------------------------------------------
# Спринт
# --------------------------------------------------------------------------
SPRINT = {
    "sprint_id": 12, "name": "S12", "state": "closed",
    "start_at": at(1, 12), "end_at": at(14, 12), "complete_at": at(14, 12),
}
DONE_IN_SPRINT = issue(
    "A-1", at(28, 9, month=5), "10002",
    [
        (at(31, 9, month=5), sprint("", "12")),
        (at(2), status("1", "3")),
        (at(3), points("2", "3")),
        (at(10), status("3", "10002")),
    ],
    sprints=[{"id": 12}], estimate=3,
)
CARRIED = issue(
    "A-2", at(28, 9, month=5), "3",
    [(at(3), status("1", "3")), (at(5), sprint("", "12")), (at(14, 13), sprint("12", "12, 13"))],
    sprints=[{"id": 12}, {"id": 13}], estimate=5,
)
REMOVED = issue(
    "A-3", at(28, 9, month=5), "1",
    [(at(31, 9, month=5), sprint("", "12")), (at(7), sprint("12", ""))],
    estimate=1,
)
SUBTASK = issue(
    "A-4", at(28, 9, month=5), "10002",
    [(at(31, 9, month=5), sprint("", "12")), (at(4), status("1", "10002"))],
    sprints=[{"id": 12}], subtask=True,
)
IN_AND_OUT = issue(
    "A-5", at(28, 9, month=5), "1",
    [(at(3), sprint("", "12")), (at(4), sprint("12", ""))],
)


def test_a_closed_sprint_counts_commitment_scope_change_and_carry_over():
    rows = [row(raw) for raw in (DONE_IN_SPRINT, CARRIED, REMOVED, SUBTASK, IN_AND_OUT)]
    facts = flow.sprint_facts(SPRINT, rows, NOW)

    assert facts["committed_count"] == 2  # A-1 и A-3; подзадача не считается
    assert facts["committed_points"] == 2 + 1  # оценка A-1 на старте — 2, а не 3
    assert facts["completed_count"] == facts["completed_committed_count"] == 1
    assert facts["completed_points"] == 3  # на конце — уже 3
    assert facts["added_count"] == 2  # A-2 и A-5
    assert facts["removed_count"] == 2  # A-3 и A-5
    assert facts["carried_count"] == 1  # A-2 уехала в спринт 13


def test_an_active_sprint_has_no_carry_over_yet():
    active = {**SPRINT, "state": "active", "complete_at": None}
    facts = flow.sprint_facts(active, [row(CARRIED, now=at_dt(10))], at_dt(10))

    assert facts["carried_count"] == 0
    assert facts["added_count"] == 1


def at_dt(day: int) -> datetime:
    return datetime(2026, 6, day, 12, tzinfo=UTC)


def test_a_future_sprint_is_not_counted():
    assert flow.sprint_facts({**SPRINT, "state": "future"}, [], NOW) is None
    assert flow.sprint_facts({**SPRINT, "start_at": None}, [], NOW) is None


# --------------------------------------------------------------------------
# Показатели за период
# --------------------------------------------------------------------------
def done_row(key: str, done: datetime, cycle: float, **extra) -> dict:
    return {
        "key": key, "subtask": False, "orbita": False, "category": flow.DONE,
        "done_at": done, "cycle_days": cycle, "lead_days": cycle + 1, "reopens": 0,
        "blocked_hours": 0.0, "analysis_returns": 0, **extra,
    }


def test_percentiles_are_values_that_actually_happened():
    assert flow.percentile([1, 2, 3, 4, 10], 0.5) == 3
    assert flow.percentile([1, 2, 3, 4, 10], 0.85) == 10
    assert flow.percentile([], 0.5) is None


def test_a_period_counts_what_was_closed_inside_it_and_every_week_even_empty():
    since = datetime(2026, 6, 1, tzinfo=UTC)
    rows = [
        done_row("B-1", since + timedelta(days=1), 2.0),
        done_row("B-2", since + timedelta(days=2), 4.0, reopens=1, orbita=True),
        done_row("B-3", since + timedelta(days=20), 8.0, blocked_hours=5.0),
        done_row("B-4", since - timedelta(days=3), 1.0),
        {"key": "B-5", "subtask": False, "category": flow.WORK, "wip_days": 12.0, "status": "Dev"},
        done_row("B-6", since + timedelta(days=5), 1.0, subtask=True),
    ]
    found = flow.summary(rows, [], since=since, until=since + timedelta(days=28),
                         now=since + timedelta(days=28), analysis_configured=False)

    assert found["done"] == 3
    assert [week["done"] for week in found["weeks"]] == [2, 0, 1, 0]
    assert found["cycle"] == {"n": 3, "p50": 4.0, "p85": 8.0}
    assert found["reopened"]["share"] == pytest.approx(1 / 3)
    assert found["blocked"]["n"] == 1
    assert found["returns"] == {"configured": False, "n": None, "share": None}
    assert found["wip"]["count"] == 1
    assert found["wip"]["oldest"][0]["key"] == "B-5"
    assert found["orbita"]["orbita"]["done"] == 1


def test_a_past_period_does_not_pretend_to_know_the_wip():
    since = datetime(2026, 5, 1, tzinfo=UTC)
    found = flow.summary([], [], since=since, until=since + timedelta(days=30), now=NOW,
                         analysis_configured=True)

    assert found["wip"]["count"] is None
    assert found["returns"]["share"] is None


def test_predictability_uses_closed_sprints_started_in_the_period():
    since = datetime(2026, 6, 1, tzinfo=UTC)
    sprints = [
        {"sprint_id": 1, "state": "closed", "start_at": since + timedelta(days=1),
         "committed_count": 4, "completed_committed_count": 3,
         "committed_points": 8.0, "completed_committed_points": 6.0},
        {"sprint_id": 2, "state": "active", "start_at": since + timedelta(days=15),
         "committed_count": 5, "completed_committed_count": 1,
         "committed_points": 0.0, "completed_committed_points": 0.0},
    ]
    found = flow.summary([], sprints, since=since, until=NOW, now=NOW, analysis_configured=False)

    assert found["predictability"]["sprints"] == 1
    assert found["predictability"]["count"]["p50"] == 0.75
    assert len(found["sprints"]) == 2


# --------------------------------------------------------------------------
# Таблицы
# --------------------------------------------------------------------------
def test_the_report_puts_both_periods_side_by_side_and_keeps_the_limitations():
    since = datetime(2026, 6, 1, tzinfo=UTC)
    current = flow.summary([done_row("C-1", since + timedelta(days=2), 3.0),
                            {"key": "C-2", "subtask": False, "category": flow.WORK,
                             "wip_days": 9.5, "status": "Dev"}],
                           [], since=since, until=NOW, now=NOW, analysis_configured=False)
    previous = flow.summary([done_row("C-0", since - timedelta(days=5), 5.0)],
                            [], since=since - timedelta(days=30), until=since, now=NOW,
                            analysis_configured=False)
    text = flow.render({"board_id": 42, "name": "Платежи"}, current, previous,
                       notes=["Статусы аналитики не названы."],
                       link=lambda key: f"[{key}](https://jira.test/browse/{key})")

    assert "Доска 42 «Платежи»" in text
    assert "| Время в работе, p50, дн. | 3 (мало данных) | 5 | −2 |" in text
    assert "[C-2](https://jira.test/browse/C-2)" in text
    assert "### Ограничения данных" in text and "Статусы аналитики не названы." in text
    assert "Возвращались в аналитику" not in text


def test_numbers_are_written_the_russian_way():
    assert flow.number(4.0) == "4"
    assert flow.number(4.25) == "4,2"
    assert flow.number(None) == "—"
