"""
Оценка результата чата и роуты метрик потока.

Оценку ставит владелец треда и только он: чужой тред отвечает тем же 404, что
и несуществующий. Снимок треда — ходы, стоимость, проверка ссылок, заведённые
задачи — сервер берёт из состояния треда, а не из тела запроса. Без базы
оценку некуда положить, и роут говорит об этом 503.

Роуты досок — только администратору: доска собирается токеном того, кто
попросил, а сводку видят все, кто её откроет.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest
from starlette.testclient import TestClient
from test_flow_sync import FakeJira, MemoryFlow

from agent import api, flow_store, outcomes, security
from agent.security import Principal

ANNA_CHAT = "11111111-1111-4111-8111-111111111111"
TIM_CHAT = "22222222-2222-4222-8222-222222222222"
USERS = {
    "anna": Principal("sub-anna", "anna"),
    "tim": Principal("sub-tim", "tim"),
    "boss": Principal("sub-boss", "boss", admin=True),
}
THREADS = {
    ANNA_CHAT: {
        "thread_id": ANNA_CHAT,
        "created_at": "2026-09-30T08:00:00+00:00",
        "metadata": {"owner": "sub-anna", "graph_id": "prep"},
        "values": {
            "messages": [
                {"type": "human", "content": "Подготовь PAY-1"},
                {"type": "ai", "content": "…"},
                {"type": "human", "content": "поправь план"},
            ],
            "usage": {"calls": 7},
            "spend": {"usd": 0.0123, "naive_usd": 0.05, "unpriced_calls": 0},
            "citations": {"plan": {"refs": 4, "quotes": 1, "verified": 3, "findings": []}},
        },
    },
    TIM_CHAT: {"thread_id": TIM_CHAT, "created_at": "2026-09-30T09:00:00+00:00",
               "metadata": {"owner": "sub-tim", "graph_id": "jira"},
               "values": {"issues": [{"key": "PAY-7"}, {"local": "x"}]}},
}


class MemoryOutcomes:
    """Оценки в памяти: те же методы, что у `outcomes.PostgresOutcomes`."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def get(self, owner: str, thread_id: str) -> dict | None:
        row = self.rows.get(thread_id)
        return self._public(row) if row and row["owner"] == owner else None

    def save(self, owner: str, thread_id: str, answer: dict, seen: dict) -> dict:
        was = self.rows.get(thread_id)
        if was and was["owner"] != owner:
            raise outcomes.OutcomeError("тред оценил другой человек")
        now = datetime.now(UTC).isoformat()
        self.rows[thread_id] = {
            "thread_id": thread_id, "owner": owner, **answer, **seen,
            "created_at": was["created_at"] if was else now, "updated_at": now,
        }
        return self._public(self.rows[thread_id])

    def summary(self, days: int) -> list[dict]:
        return [{"graph": row["graph"], "verdict": row["verdict"], "count": 1}
                for row in self.rows.values()]

    @staticmethod
    def _public(row: dict) -> dict:
        """Как `outcomes._row`: без владельца, время строкой ISO."""
        found = {key: value for key, value in row.items() if key != "owner"}
        if isinstance(found.get("thread_created_at"), datetime):
            found["thread_created_at"] = found["thread_created_at"].isoformat()
        found["verdict_title"] = outcomes.VERDICTS[row["verdict"]]
        return found


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> MemoryOutcomes:
    fake = MemoryOutcomes()
    monkeypatch.setattr(outcomes, "store", fake)
    monkeypatch.setenv("POSTGRES_URI", "postgresql://example.invalid/orbita")
    return fake


@pytest.fixture
def client(store, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("API_ADMIN_TOKEN", "test-only-admin-token-with-32-characters")
    checked = security.authenticate
    monkeypatch.setattr(
        security, "authenticate",
        lambda headers: USERS.get(headers.get("authorization", "").removeprefix("Bearer "))
        or checked(headers),
    )

    async def owns(principal, thread_id, *, must_exist=False):
        thread = THREADS.get(thread_id)
        return bool(thread) and (principal.service or thread["metadata"]["owner"] == principal.subject)

    async def record(thread_id):
        return THREADS.get(thread_id)

    monkeypatch.setattr(api, "owns_thread", owns)
    monkeypatch.setattr(api, "thread_record", record)
    return TestClient(api.app)


def as_(name: str) -> dict:
    return {"Authorization": f"Bearer {name}"}


# --------------------------------------------------------------------------
# Разбор и снимок
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("body", "error"),
    [
        ({"verdict": "ok"}, "вердикт"),
        ({"verdict": "accepted", "minutes": "полчаса"}, "минуты"),
        ({"verdict": "accepted", "minutes": -1}, "от 0"),
        ({"verdict": "accepted", "minutes": True}, "минуты"),
        ({"verdict": "rejected", "reason": "х" * 2001}, "причина"),
    ],
)
def test_a_wrong_answer_is_refused_in_words(body, error):
    with pytest.raises(outcomes.OutcomeError, match=error):
        outcomes.parse(body)


def test_minutes_may_be_left_empty():
    assert outcomes.parse({"verdict": "edited", "minutes": ""})["minutes"] is None
    assert outcomes.parse({"verdict": "edited", "minutes": "25"})["minutes"] == 25


def test_the_snapshot_is_taken_from_the_thread_not_invented():
    seen = outcomes.snapshot(THREADS[ANNA_CHAT], THREADS[ANNA_CHAT]["values"])

    assert seen["graph"] == "prep"
    assert seen["turns"] == 2
    assert seen["llm_calls"] == 7
    assert seen["cost_usd"] == pytest.approx(0.0123)
    assert (seen["refs"], seen["verified"]) == (4, 3)
    # Задач Jira у подготовки нет: пусто, а не ноль.
    assert seen["jira_created"] is None
    assert seen["thread_created_at"] == datetime(2026, 9, 30, 8, tzinfo=UTC)


def test_the_snapshot_counts_only_issues_with_a_tracker_key():
    seen = outcomes.snapshot(THREADS[TIM_CHAT], THREADS[TIM_CHAT]["values"])

    assert seen["jira_created"] == 1
    assert seen["refs"] is None


# --------------------------------------------------------------------------
# Роуты оценки
# --------------------------------------------------------------------------
def test_the_owner_rates_the_chat_and_sees_the_rating(client, store):
    empty = client.get(f"/api/outcomes/{ANNA_CHAT}", headers=as_("anna"))
    saved = client.put(f"/api/outcomes/{ANNA_CHAT}", headers=as_("anna"),
                       json={"verdict": "edited", "minutes": 25, "reason": "поправил план",
                             "turns": 99, "cost_usd": 0})

    assert empty.status_code == 200 and empty.json()["outcome"] is None
    assert set(empty.json()["verdicts"]) == set(outcomes.VERDICTS)
    assert saved.status_code == 200, saved.text
    outcome = saved.json()["outcome"]
    assert outcome["verdict_title"] == "принят с правками"
    assert outcome["minutes"] == 25
    # Числа снимка — из треда, а не из тела запроса.
    assert outcome["turns"] == 2 and outcome["cost_usd"] == pytest.approx(0.0123)
    assert "owner" not in outcome
    assert client.get(f"/api/outcomes/{ANNA_CHAT}", headers=as_("anna")).json()["outcome"]["minutes"] == 25


def test_a_second_rating_replaces_the_first_but_keeps_its_time(client, store):
    first = client.put(f"/api/outcomes/{ANNA_CHAT}", headers=as_("anna"), json={"verdict": "accepted"})
    second = client.put(f"/api/outcomes/{ANNA_CHAT}", headers=as_("anna"),
                        json={"verdict": "reworked", "minutes": 90})

    assert second.json()["outcome"]["verdict"] == "reworked"
    assert second.json()["outcome"]["created_at"] == first.json()["outcome"]["created_at"]


def test_someone_elses_chat_is_not_found(client, store):
    for method in ("get", "put"):
        response = getattr(client, method)(
            f"/api/outcomes/{TIM_CHAT}", headers=as_("anna"),
            **({"json": {"verdict": "accepted"}} if method == "put" else {}),
        )
        assert response.status_code == 404, response.text
    assert store.rows == {}


def test_a_bad_verdict_is_a_bad_request(client):
    response = client.put(f"/api/outcomes/{ANNA_CHAT}", headers=as_("anna"), json={"verdict": "ok"})

    assert response.status_code == 400
    assert response.json()["error_code"] == "outcome_invalid"


def test_without_a_database_the_rating_is_unavailable_not_lost(client, monkeypatch):
    monkeypatch.delenv("POSTGRES_URI")

    response = client.put(f"/api/outcomes/{ANNA_CHAT}", headers=as_("anna"), json={"verdict": "accepted"})

    assert response.status_code == 503
    assert response.json()["error_code"] == "outcomes_unavailable"


def test_the_pilot_summary_is_for_the_administrator(client):
    client.put(f"/api/outcomes/{ANNA_CHAT}", headers=as_("anna"), json={"verdict": "accepted"})

    assert client.get("/api/outcomes", headers=as_("anna")).status_code == 403
    summary = client.get("/api/outcomes?days=30", headers=as_("boss"))
    assert summary.status_code == 200, summary.text
    assert summary.json()["rows"] == [{"graph": "prep", "verdict": "accepted", "count": 1}]
    assert client.get("/api/outcomes?days=0", headers=as_("boss")).status_code == 400


# --------------------------------------------------------------------------
# Роуты досок
# --------------------------------------------------------------------------
@pytest.fixture
def boards(monkeypatch: pytest.MonkeyPatch) -> MemoryFlow:
    fake = MemoryFlow()
    monkeypatch.setattr(flow_store, "store", fake)
    return fake


def settled(client: TestClient, board_id: int = 42) -> dict:
    """Сбор идёт фоновой задачей: дождаться, пока доска перестанет быть собираемой."""
    deadline = time.monotonic() + 5
    while True:
        found = {item["board_id"]: item for item in client.get("/api/flow/boards", headers=as_("boss")).json()["boards"]}
        board = found.get(board_id)
        if board and board["state"] != flow_store.RUNNING or time.monotonic() > deadline:
            return board
        time.sleep(0.02)


def test_boards_are_for_the_administrator(client, boards):
    assert client.get("/api/flow/boards", headers=as_("anna")).status_code == 403
    assert client.post("/api/flow/boards/42/sync", headers=as_("anna")).status_code == 403
    assert client.get("/api/flow/boards/42/summary", headers=as_("anna")).status_code == 403


def test_a_sync_is_started_and_its_result_is_listed(client, boards, monkeypatch):
    FakeJira(monkeypatch)

    started = client.post("/api/flow/boards/42/sync", headers=as_("boss"), json={"days": 30})
    board = settled(client)

    assert started.status_code == 202, started.text
    assert board["board_id"] == 42 and board["state"] == flow_store.OK
    assert board["synced_by"] == "sub-boss"
    assert "jql" not in board


def test_the_summary_of_a_collected_board_needs_no_new_sync(client, boards, monkeypatch):
    tracker = FakeJira(monkeypatch)
    client.post("/api/flow/boards/42/sync", headers=as_("boss"))
    settled(client)
    searches = len(tracker.searches)

    summary = client.get("/api/flow/boards/42/summary?days=90", headers=as_("boss"))
    unknown = client.get("/api/flow/boards/7/summary", headers=as_("boss"))

    assert summary.status_code == 200, summary.text
    assert summary.json()["board"]["name"] == "Платежи"
    assert "cycle" in summary.json()["current"]
    assert len(tracker.searches) == searches
    assert unknown.status_code == 404


def test_a_wrong_board_number_is_a_bad_request(client, boards):
    assert client.post("/api/flow/boards/abc/sync", headers=as_("boss")).status_code == 400
    response = client.post("/api/flow/boards/42/sync", headers=as_("boss"), json={"days": 0})
    assert response.status_code == 400
