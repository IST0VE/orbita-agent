"""
Чтение доски для проджект-менеджера: разбор задачи Agile API и сборка снимка.

Jira подделана целиком: `jira.call` отвечает заготовками по пути запроса и
запоминает, что спросили. Проверяется то, чего без подделки не увидеть:
порядок задач — порядок доски (идущий спринт, следующий, будущие, бэклог),
постраничное чтение упирается в потолок и говорит об этом, отказ версий,
эпика или истории спринтов не роняет снимок, а отказ самой доски — роняет.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent import flow, flow_jira, flow_sync, jira, pm_jira

FIELDS = flow.Fields(estimate="customfield_10002", estimate_name="Story Points",
                     sprint="customfield_10005", flagged="customfield_10021")
SETTINGS = jira.Settings(base_url="https://jira.test", token="t", api_path="/rest/api/2",
                         search_path="/search", interval_s=0.0)
NOW = datetime(2026, 10, 2, 12, tzinfo=UTC)


def status(category: str, name: str = "") -> dict:
    return {"name": name or category, "statusCategory": {"key": category}}


def raw(key: str, *, category: str = "new", estimate: float | None = 3.0, **fields) -> dict:
    return {"key": key, "fields": {
        "summary": f"Задача {key}", "issuetype": {"name": "Story", "subtask": False},
        "status": status(category), "priority": {"name": "High"},
        "customfield_10002": estimate, "created": "2026-09-01T10:00:00.000+0300",
        **fields,
    }}


# --------------------------------------------------------------------------
# Разбор задачи
# --------------------------------------------------------------------------
def test_an_issue_keeps_what_planning_needs():
    item = pm_jira.issue_of(raw(
        "PAY-1", duedate="2026-10-10", flagged=True,
        epic={"key": "PAY-100", "name": "Оплата частями", "done": False},
        fixVersions=[{"id": "10010", "name": "2.4"}],
        summary="  Оплата\nчастями  ",
    ), FIELDS, place=pm_jira.BACKLOG)

    assert item["summary"] == "Оплата частями"
    assert (item["priority"], item["level"], item["estimate"]) == ("High", 2, 3.0)
    assert (item["epic"], item["epic_name"]) == ("PAY-100", "Оплата частями")
    # Версия — по id: имя уникально только внутри проекта.
    assert item["versions"] == ["10010"] and item["due"] == "2026-10-10"
    assert item["flagged"] is True and item["place"] == pm_jira.BACKLOG


def test_an_epic_parent_counts_as_the_epic_and_a_story_parent_does_not():
    epic_parent = {"key": "PAY-100", "fields": {"summary": "Оплата",
                                                 "issuetype": {"name": "Epic"}}}
    story_parent = {"key": "PAY-7", "fields": {"summary": "Сценарий",
                                                "issuetype": {"name": "Story"}}}

    assert pm_jira.issue_of(raw("A", parent=epic_parent), FIELDS, place="b")["epic"] == "PAY-100"
    assert pm_jira.issue_of(raw("B", parent=story_parent), FIELDS, place="b")["epic"] == ""


def test_blocking_links_keep_only_open_issues_on_the_other_side():
    blocks = {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"}
    relates = {"name": "Relates", "inward": "relates to", "outward": "relates to"}
    links = [
        {"type": blocks, "outwardIssue": {"key": "PAY-2", "fields": {"status": status("new")}}},
        {"type": blocks, "inwardIssue": {"key": "PAY-3", "fields": {"status": status("indeterminate")}}},
        {"type": blocks, "inwardIssue": {"key": "PAY-4", "fields": {"status": status("done")}}},
        {"type": relates, "inwardIssue": {"key": "PAY-5", "fields": {"status": status("new")}}},
        {"type": {"name": "Блокировка", "inward": "заблокирована", "outward": "блокирует"},
         "inwardIssue": {"key": "PAY-6", "fields": {"status": status("new")}}},
    ]

    assert pm_jira.links(links) == (["PAY-2"], ["PAY-3", "PAY-6"])


@pytest.mark.parametrize(
    ("name", "level"),
    [("Highest", 1), ("Блокирующий", 1), ("Major", 2), ("Средний", 3), ("Minor", 4),
     ("Trivial", 5), ("P2 — договориться", None), ("", None)],
)
def test_priority_levels_are_known_by_name_only(name, level):
    assert pm_jira.priority_level(name) == level


# --------------------------------------------------------------------------
# Списки и снимок
# --------------------------------------------------------------------------
class Paged(list):
    """Список задач Agile API: отдаётся страницами по `startAt` и `maxResults`."""


class FakeJira:
    """`jira.call` по заготовкам: путь -> ответ или исключение; `Paged` — постранично."""

    def __init__(self, routes: dict) -> None:
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, method, path, s, params=None, **kwargs):
        params = params or {}
        self.calls.append((path, dict(params)))
        found = self.routes.get(path)
        if found is None:
            raise jira.JiraError(f"GET {path}: объект не найден (HTTP 404)")
        if isinstance(found, Exception):
            raise found
        if isinstance(found, Paged):
            start = int(params.get("startAt", 0))
            page = found[start : start + int(params.get("maxResults", pm_jira.PAGE))]
            return {"startAt": start, "total": len(found), "issues": page}
        return found


def test_a_listing_pages_until_the_limit_and_says_it_stopped(monkeypatch):
    fake = FakeJira({"/l": Paged(raw(f"PAY-{n}") for n in range(120))})
    monkeypatch.setattr(jira, "call", fake)

    found, limited = pm_jira.listing("/l", SETTINGS, fields="summary", limit=70)
    assert len(found) == 70 and limited is True
    assert [params["startAt"] for _, params in fake.calls] == [0, 50]

    found, limited = pm_jira.listing("/l", SETTINGS, fields="summary", limit=500)
    assert len(found) == 120 and limited is False


def test_a_kanban_board_has_no_sprints(monkeypatch):
    path = f"{pm_jira.AGILE}/board/7/sprint"
    monkeypatch.setattr(jira, "call", FakeJira({
        path: jira.JiraError(f"GET {path}: HTTP 400 — The board does not support sprints"),
    }))

    assert pm_jira.sprints(7, SETTINGS) is None


BOARD = f"{pm_jira.AGILE}/board/42"


def routes(**extra) -> dict:
    found = {
        BOARD: {"id": 42, "name": "Платежи", "type": "scrum", "location": {"projectKey": "PAY"}},
        f"{BOARD}/configuration": {"filter": {"id": "1"},
                                   "estimation": {"field": {"fieldId": "customfield_10002",
                                                            "displayName": "Story Points"}}},
        "/rest/api/2/field": [],
        f"{BOARD}/sprint": {"isLast": True, "values": [
            {"id": 30, "name": "S-30", "state": "active", "goal": "Бета",
             "startDate": "2026-09-26T09:00:00.000Z", "endDate": "2026-10-06T18:00:00.000Z"},
            {"id": 31, "name": "S-31", "state": "future"},
            {"id": 32, "name": "S-32", "state": "future"},
        ]},
        f"{BOARD}/sprint/30/issue": Paged([
            raw("PAY-1", category="done", epic={"key": "PAY-100", "name": "Оплата"}),
            raw("PAY-2", category="indeterminate"),
        ]),
        f"{BOARD}/sprint/31/issue": Paged([raw("PAY-3", epic={"key": "PAY-100", "name": "Оплата"})]),
        f"{BOARD}/sprint/32/issue": Paged([raw("PAY-4", epic={"key": "PAY-200", "name": "Отчёты"})]),
        f"{BOARD}/backlog": Paged([raw("PAY-5"), raw("PAY-6")]),
        f"{BOARD}/version": {"isLast": True, "values": [
            {"id": 10010, "name": "2.4", "projectId": 10000, "releaseDate": "2026-11-20"},
            {"id": 10011, "name": "архив", "archived": True},
        ]},
        f"{BOARD}/epic": {"isLast": True, "values": [
            {"key": "PAY-100", "name": "Оплата", "done": False},
            {"key": "PAY-300", "name": "Старый импорт", "done": False},
            {"key": "PAY-400", "name": "Закрытый", "done": True},
        ]},
        f"{pm_jira.AGILE}/epic/PAY-300/issue": Paged([raw("PAY-31", category="done")]),
        f"{pm_jira.AGILE}/epic/PAY-100/issue": Paged([raw("PAY-1", category="done"), raw("PAY-3")]),
        f"{pm_jira.AGILE}/epic/PAY-200/issue": jira.JiraError("HTTP 400 — team-managed"),
    }
    found.update(extra)
    return found


class History:
    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.fail = fail

    def __call__(self, board_id, period, *, who="", store_ok=True, refresh=True):
        self.calls.append({"board_id": board_id, "period": period, "who": who,
                           "store_ok": store_ok})
        if self.fail:
            raise self.fail
        sprint = {"sprint_id": 29, "name": "S-29", "state": "closed",
                  "start_at": datetime(2026, 9, 12, tzinfo=UTC), "completed_points": 20.0}
        current = {"sprints": [sprint], "weeks": [], "wip": {"oldest": []}}
        return flow_sync.Measured(board={}, current=current, previous={"sprints": [sprint]},
                                  notes=["свежее чтение"], stored=False)


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(jira, "load_settings", lambda: SETTINGS)


def test_the_snapshot_keeps_the_board_order(monkeypatch):
    monkeypatch.setattr(jira, "call", FakeJira(routes()))
    history = History()
    monkeypatch.setattr(flow_sync, "measure", history)

    found = pm_jira.collect(42, who="u", store_ok=False, now=NOW)

    assert [(item["key"], item["place"]) for item in found["items"]] == [
        ("PAY-1", pm_jira.ACTIVE), ("PAY-2", pm_jira.ACTIVE), ("PAY-3", pm_jira.NEXT),
        ("PAY-4", pm_jira.FUTURE_PLACE), ("PAY-5", pm_jira.BACKLOG), ("PAY-6", pm_jira.BACKLOG),
    ]
    assert found["mode"] == "scrum" and found["active"][0]["goal"] == "Бета"
    assert [sprint["name"] for sprint in found["next"]] == ["S-31", "S-32"]
    assert found["board"]["estimate_field"] == "customfield_10002"
    assert found["versions"] == [
        {"id": "10010", "name": "2.4", "project_id": "10000", "release_date": "2026-11-20"},
    ]
    # Эпики — по рангу их ближайшей незакрытой задачи, за ними открытые эпики
    # доски без незакрытой работы на ней; отказ одного не роняет снимок.
    assert [epic["key"] for epic in found["epics"]] == ["PAY-100", "PAY-200", "PAY-300"]
    assert [child["key"] for child in found["epics"][0]["children"]] == ["PAY-1", "PAY-3"]
    assert "team-managed" in found["epics"][1]["error"]
    # История — из сбора метрик потока, без дублей спринта из двух периодов.
    assert history.calls == [{"board_id": 42, "period": pm_jira.HISTORY_DAYS, "who": "u",
                              "store_ok": False}]
    assert [sprint["sprint_id"] for sprint in found["history"]["sprints"]] == [29]
    assert found["history"]["sprints"][0]["start_at"].startswith("2026-09-12")
    assert found["read_at"].startswith("2026-10-02")


def test_limits_and_failed_parts_are_written_into_notes(monkeypatch):
    monkeypatch.setenv("PM_BACKLOG_LIMIT", "20")
    monkeypatch.setenv("PM_EPICS", "1")
    monkeypatch.setattr(jira, "call", FakeJira(routes(**{
        f"{BOARD}/backlog": Paged(raw(f"PAY-{n}") for n in range(10, 40)),
        f"{BOARD}/version": jira.JiraError("HTTP 403 — нет прав"),
    })))
    monkeypatch.setattr(flow_sync, "measure", History(fail=jira.JiraError("HTTP 500")))

    found = pm_jira.collect(42, now=NOW)

    assert len([item for item in found["items"] if item["place"] == pm_jira.BACKLOG]) == 20
    assert any("PM_BACKLOG_LIMIT" in note for note in found["notes"])
    assert any("Версии доски не прочитаны" in note for note in found["notes"])
    assert [epic["key"] for epic in found["epics"]] == ["PAY-100"]
    assert any("не посчитаны: PAY-200, PAY-300" in note for note in found["notes"])
    assert found["history"] == {"read": False, "error": "HTTP 500"}


def test_a_kanban_board_splits_open_issues_into_work_and_queue(monkeypatch):
    fake = FakeJira(routes(**{
        BOARD: {"id": 42, "name": "Поддержка", "type": "kanban"},
        f"{BOARD}/issue": Paged([raw("SUP-1", category="indeterminate"), raw("SUP-2")]),
    }))
    monkeypatch.setattr(jira, "call", fake)
    monkeypatch.setattr(flow_sync, "measure", History())

    found = pm_jira.collect(42, now=NOW)

    assert found["mode"] == "kanban"
    assert [(item["key"], item["place"]) for item in found["items"]] == [
        ("SUP-1", pm_jira.WORK), ("SUP-2", pm_jira.QUEUE),
    ]
    # У канбана спринты не спрашиваются, готовые отсекает JQL.
    paths = [path for path, _ in fake.calls]
    assert f"{BOARD}/sprint" not in paths
    assert next(params for path, params in fake.calls if path == f"{BOARD}/issue")["jql"] == (
        "statusCategory != Done"
    )


def test_an_epic_with_everything_closed_is_checked_and_named_ready_to_close():
    from agent import pm

    done = {"key": "PAY-1", "category": "done", "epic": "PAY-100", "epic_name": "Оплата",
            "epic_done": False}
    closed_epic = {"key": "PAY-2", "category": "new", "epic": "PAY-400", "epic_name": "Старое",
                   "epic_done": True}
    busy = {"key": "PAY-3", "category": "new", "epic": "PAY-200", "epic_name": "Отчёты",
            "epic_done": False}
    listed = [{"key": "PAY-300", "name": "Импорт"}, {"key": "PAY-200", "name": "Отчёты"}]

    # Эпик с незакрытой работой — первым; эпик, чьи задачи на доске все закрыты, и
    # эпик из списка доски без задач в бэклоге — за ним; закрытый — никогда.
    assert pm_jira.epic_order([done, closed_epic, busy], listed) == [
        ("PAY-200", "Отчёты"), ("PAY-100", "Оплата"), ("PAY-300", "Импорт"),
    ]
    rows = pm.epic_rows(
        [{"key": "PAY-100", "name": "Оплата", "children": [
            {"key": "PAY-1", "subtask": False, "category": "done", "estimate": 3.0},
        ]}],
        pm.Units(pm.POINTS, "SP"), set(), [],
    )
    assert rows[0]["stage"] == "готов к закрытию"


def test_without_the_board_epic_list_the_report_says_what_it_cannot_see(monkeypatch):
    monkeypatch.setattr(jira, "call", FakeJira(routes(**{
        f"{BOARD}/epic": jira.JiraError("HTTP 403 — нет прав"),
    })))
    monkeypatch.setattr(flow_sync, "measure", History())

    found = pm_jira.collect(42, now=NOW)

    assert [epic["key"] for epic in found["epics"]] == ["PAY-100", "PAY-200"]
    assert any("готов к закрытию" in note for note in found["notes"])


def test_an_unreadable_board_is_an_error_not_an_empty_report(monkeypatch):
    monkeypatch.setattr(jira, "call", FakeJira({}))
    monkeypatch.setattr(flow_sync, "measure", History())

    with pytest.raises(jira.JiraError, match="HTTP 404"):
        pm_jira.collect(42, now=NOW)


def test_the_fields_ask_for_agile_epic_and_flag_and_the_known_custom_ids(monkeypatch):
    monkeypatch.setenv("JIRA_EPIC_LINK_FIELD", "customfield_10014")
    asked = pm_jira.fields_param(FIELDS).split(",")

    assert {"epic", "flagged", "issuelinks", "fixVersions", "priority", "duedate"} <= set(asked)
    assert {"customfield_10002", "customfield_10021", "customfield_10014"} <= set(asked)
    assert flow_jira.AGILE == pm_jira.AGILE


def test_kanban_history_combines_weeks_split_between_periods(monkeypatch):
    from agent import pm

    class WeeklyHistory(History):
        def __call__(self, *args, **kwargs):
            measured = super().__call__(*args, **kwargs)
            measured.previous.update({
                "since": "2026-09-01T12:00:00+00:00",
                "weeks": [
                    {"week": "2026-08-31T00:00:00+00:00", "done": 1},
                    {"week": "2026-09-07T00:00:00+00:00", "done": 4},
                    {"week": "2026-09-14T00:00:00+00:00", "done": 2},
                ],
            })
            measured.current["weeks"] = [
                {"week": "2026-09-14T00:00:00+00:00", "done": 3},
                {"week": "2026-09-21T00:00:00+00:00", "done": 6},
                {"week": "2026-09-28T00:00:00+00:00", "done": 1},
            ]
            return measured

    monkeypatch.setattr(flow_sync, "measure", WeeklyHistory())
    found = pm_jira._history(42, who="u", store_ok=False)

    # Разделённая границей периодов неделя складывается, а первая неполная
    # неделя истории не занижает скорость команды.
    assert found["weeks"] == [
        {"week": "2026-09-07T00:00:00+00:00", "done": 4},
        {"week": "2026-09-14T00:00:00+00:00", "done": 5},
        {"week": "2026-09-21T00:00:00+00:00", "done": 6},
        {"week": "2026-09-28T00:00:00+00:00", "done": 1},
    ]
    assert pm.throughput(found["weeks"], NOW, limit=6)["rows"][0]["completed"] == 11.0
