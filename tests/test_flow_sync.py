"""
Сбор доски: какое окно читать, что делать с прочитанным и когда сдаваться.

Jira и база здесь подделаны: чтение доски — функциями `flow_jira`, которые
запоминают JQL, база — словарём с тем же набором методов, что у
`flow_store.PostgresFlow`. Проверяется то, что без них не видно: первый сбор
читает окно целиком, следующий — только обновлённое, короткое окно не
выдаётся за длинное, а отказ посреди сбора не оставляет доску «собираемой».
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from test_flow import CATEGORIES, FIELDS, WORKED, at, issue, status

from agent import flow_jira, flow_store, flow_sync, jira

SETTINGS = jira.Settings(base_url="https://jira.test", token="t", api_path="/rest/api/2",
                         search_path="/search", interval_s=0.0)


class MemoryFlow:
    """База метрик в памяти: те же методы, что у `flow_store.PostgresFlow`."""

    def __init__(self) -> None:
        self.meta: dict[int, dict] = {}
        self.rows: dict[tuple[int, str], dict] = {}
        self.sprint_rows: dict[int, list[dict]] = {}
        self.busy: set[int] = set()

    def boards(self) -> list[dict]:
        return [dict(item) for item in self.meta.values()]

    def board(self, board_id: int) -> dict | None:
        found = self.meta.get(board_id)
        return dict(found) if found else None

    def begin(self, meta: dict, who: str) -> bool:
        if meta["board_id"] in self.busy:
            return False
        known = self.meta.setdefault(meta["board_id"], {"synced_at": None, "window_start": None,
                                                        "truncated": False})
        known.update({**meta, "state": flow_store.RUNNING, "synced_by": who})
        return True

    def finish(self, board_id: int, *, state: str, detail: str = "", synced_at=None,
               window_start=None, truncated: bool = False) -> None:
        known = self.meta[board_id]
        known.update({"state": state, "detail": detail, "truncated": truncated})
        if synced_at:
            known["synced_at"] = synced_at
        if window_start:
            known["window_start"] = min(filter(None, (known["window_start"], window_start)))

    def save_issues(self, rows) -> int:
        for row in rows:
            self.rows[(row["board_id"], row["key"])] = dict(row)
        return len(rows)

    def issues(self, board_id: int) -> list[dict]:
        return [dict(row) for (board, _), row in self.rows.items() if board == board_id]

    def save_sprints(self, board_id: int, rows) -> None:
        self.sprint_rows[board_id] = list(rows)

    def sprints(self, board_id: int) -> list[dict]:
        return list(self.sprint_rows.get(board_id, []))


class FakeJira:
    """Доска 42: фильтр, поля, спринты и задачи — и журнал того, что спросили."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.issues: list[dict] = [WORKED]
        self.sprints: list[dict] = []
        self.searches: list[str] = []
        self.limits: list[int] = []
        self.full: list[str] = []
        self.fail: Exception | None = None
        monkeypatch.setattr(jira, "load_settings", lambda: SETTINGS)
        monkeypatch.setattr(flow_jira, "board", lambda board_id, s: {
            "board_id": board_id, "name": "Платежи", "kind": "scrum", "project": "PAY"})
        monkeypatch.setattr(flow_jira, "configuration", lambda board_id, s: {
            "filter_id": "1", "estimate": FIELDS.estimate, "estimate_name": "Story Points"})
        monkeypatch.setattr(flow_jira, "fields", lambda config, s: FIELDS)
        monkeypatch.setattr(flow_jira, "board_jql", lambda filter_id, s: "project = PAY")
        monkeypatch.setattr(flow_jira, "categories", lambda s: CATEGORIES)
        monkeypatch.setattr(flow_jira, "sprints", lambda board_id, s: list(self.sprints))
        monkeypatch.setattr(flow_jira, "search", self.search)
        monkeypatch.setattr(flow_jira, "full_changelog", self.changelog)

    def search(self, jql, fields, s, *, limit):
        self.searches.append(jql)
        self.limits.append(limit)
        if self.fail:
            raise self.fail
        yield from self.issues[:limit]

    def changelog(self, key, s):
        self.full.append(key)
        return WORKED["changelog"]["histories"]

    def minutes(self, index: int = -1) -> int:
        return int(re.search(r"updated >= -(\d+)m", self.searches[index]).group(1))


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> MemoryFlow:
    fake = MemoryFlow()
    monkeypatch.setattr(flow_store, "store", fake)
    monkeypatch.setenv("POSTGRES_URI", "postgresql://example.invalid/orbita")
    return fake


@pytest.fixture
def tracker(monkeypatch: pytest.MonkeyPatch) -> FakeJira:
    return FakeJira(monkeypatch)


# --------------------------------------------------------------------------
# Окно сбора
# --------------------------------------------------------------------------
def test_the_first_sync_reads_the_whole_history_window(store, tracker):
    result = flow_sync.sync(42, who="anna")

    assert tracker.searches[0].startswith("(project = PAY) AND updated >= -")
    assert tracker.minutes() == pytest.approx(180 * 24 * 60, abs=2)
    assert result["incremental"] is False
    assert result["issues"] == 1
    board = store.board(42)
    assert board["state"] == flow_store.OK
    assert board["synced_by"] == "anna"
    assert board["window_start"] <= datetime.now(UTC) - timedelta(days=179)
    saved = store.issues(42)[0]
    assert saved["key"] == "PAY-1" and saved["cycle_days"] == pytest.approx(6.0)


def test_the_next_sync_reads_only_what_changed_since_the_last_one(store, tracker):
    flow_sync.sync(42)
    store.meta[42]["synced_at"] = datetime.now(UTC) - timedelta(hours=5)
    tracker.issues = [issue("PAY-9", at(20), "3", [(at(21), status("1", "3"))])]

    result = flow_sync.sync(42)

    assert result["incremental"] is True
    # Пять часов с прошлого сбора и час запаса.
    assert tracker.minutes() == pytest.approx(6 * 60, abs=2)
    assert sorted(row["key"] for row in store.issues(42)) == ["PAY-1", "PAY-9"]


def test_a_longer_report_rereads_the_board_instead_of_comparing_with_emptiness(store, tracker):
    flow_sync.sync(42)

    result = flow_sync.sync(42, days=365)

    assert result["incremental"] is False
    assert tracker.minutes() == pytest.approx(365 * 24 * 60, abs=2)


def test_the_history_setting_sets_the_first_window(store, tracker, monkeypatch):
    monkeypatch.setenv("FLOW_HISTORY_DAYS", "30")

    flow_sync.sync(42)

    assert tracker.minutes() == pytest.approx(30 * 24 * 60, abs=2)


# --------------------------------------------------------------------------
# Отказы и неполнота
# --------------------------------------------------------------------------
def test_a_board_being_collected_is_not_collected_twice(store, tracker):
    store.busy.add(42)

    with pytest.raises(flow_sync.FlowBusy):
        flow_sync.sync(42)
    assert tracker.searches == []


def test_a_failure_in_the_middle_does_not_leave_the_board_running(store, tracker):
    tracker.fail = jira.JiraError("GET /search: HTTP 500")

    with pytest.raises(jira.JiraError):
        flow_sync.sync(42)

    assert store.board(42)["state"] == flow_store.FAILED
    assert "HTTP 500" in store.board(42)["detail"]


def test_hitting_the_issue_limit_marks_the_board_incomplete_until_a_full_read(store, tracker, monkeypatch):
    monkeypatch.setenv("FLOW_MAX_ISSUES", "100")
    tracker.issues = [issue(f"PAY-{n}", at(1), "1", []) for n in range(100, 201)]

    first = flow_sync.sync(42)
    store.meta[42]["synced_at"] = datetime.now(UTC) - timedelta(hours=1)
    tracker.issues = []
    second = flow_sync.sync(42)

    assert tracker.limits[0] == 100
    assert first["truncated"] is True
    # Инкрементный сбор не дочитывает того, что не влезло в первый.
    assert second["truncated"] is True
    assert store.board(42)["truncated"] is True


def test_a_changelog_cut_by_the_search_is_read_in_full(store, tracker):
    cut = {**WORKED, "changelog": {"total": 150, "histories": WORKED["changelog"]["histories"][:2]}}
    tracker.issues = [cut]

    flow_sync.sync(42)

    assert tracker.full == ["PAY-1"]
    assert store.issues(42)[0]["done_at"] is not None


def test_sprints_are_recounted_from_every_stored_issue(store, tracker):
    tracker.sprints = [{"sprint_id": 12, "name": "S12", "state": "active",
                        "start_at": at(1, 12), "end_at": None, "complete_at": None}]

    flow_sync.sync(42)

    (sprint,) = store.sprints(42)
    assert sprint["sprint_id"] == 12 and sprint["board_id"] == 42


# --------------------------------------------------------------------------
# Показатели для отчёта
# --------------------------------------------------------------------------
def test_without_a_database_the_report_reads_jira_now_and_says_so(tracker, monkeypatch):
    measured = flow_sync.measure(42, 30)

    assert measured.stored is False
    assert tracker.minutes() == pytest.approx(60 * 24 * 60, abs=2)
    assert any("не сохранены" in note for note in measured.notes)
    assert measured.board["name"] == "Платежи"


def test_a_nested_run_does_not_write_even_with_a_database(store, tracker):
    measured = flow_sync.measure(42, 30, store_ok=False)

    assert measured.stored is False
    assert store.meta == {}


def test_with_a_database_the_report_follows_a_sync_and_knows_how_far_back_it_sees(store, tracker):
    measured = flow_sync.measure(42, 120)

    assert measured.stored is True
    # Окно сбора — два периода: 240 дней, длиннее FLOW_HISTORY_DAYS.
    assert tracker.minutes() == pytest.approx(240 * 24 * 60, abs=2)
    assert measured.previous is not None
    assert any("FLOW_ANALYSIS_STATUSES" in note for note in measured.notes)


def test_a_busy_board_is_reported_from_what_is_stored(store, tracker):
    flow_sync.sync(42)
    store.busy.add(42)

    measured = flow_sync.measure(42, 30)

    assert any("уже собирают" in note for note in measured.notes)
    assert measured.current is not None


def test_a_board_never_collected_is_not_reported_from_the_database(store, tracker):
    with pytest.raises(flow_jira.FlowJiraError):
        flow_sync.measure(7, 30, refresh=False)


# --------------------------------------------------------------------------
# Фоновый сбор
# --------------------------------------------------------------------------
def test_the_background_sync_needs_boards_a_database_and_an_interval(monkeypatch):
    assert flow_sync.due() == ()
    monkeypatch.setenv("FLOW_BOARDS", "42, 57")
    assert flow_sync.due() == ()
    monkeypatch.setenv("POSTGRES_URI", "postgresql://example.invalid/orbita")
    assert flow_sync.due() == (42, 57)
    monkeypatch.setenv("FLOW_SYNC_INTERVAL_H", "0")
    assert flow_sync.due() == ()


def test_one_failing_board_does_not_stop_the_others(store, tracker, monkeypatch):
    monkeypatch.setenv("FLOW_BOARDS", "42,57")
    store.busy.add(42)

    results = flow_sync.sync_all()

    assert "error" in results[0] and results[1]["board_id"] == 57


def test_board_numbers_are_checked(monkeypatch):
    from agent import config as cfg

    monkeypatch.setenv("FLOW_BOARDS", "42, доска")
    with pytest.raises(cfg.ConfigError):
        cfg.flow_boards()
