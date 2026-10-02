"""
Метрики потока и оценки на настоящем Postgres, если ему дали адрес.

Запускается с ORBITA_TEST_POSTGRES_URI=postgresql://… (например, сервис
postgres из Compose) и без него пропускается. Сеть в тестах закрыта
(`conftest.no_external_network`), поэтому проверка идёт отдельным процессом,
как у личных подключений (`test_credentials`).

Базу приложения тест не трогает: заводит свою временную базу и свою роль
читателя (`orbita_metrics_test`) и удаляет обе в конце. Роль общая на весь
сервер Postgres, и смена пароля настоящей `orbita_metrics` сломала бы вход
Grafana до перезапуска агента.

Проверяется то, чего подделки не видят: SQL миграций 5–6, запись и чтение
через `executemany`, `LEAST` окна истории, защита чужой оценки в `ON CONFLICT`,
права роли читателя по колонкам и то, что каждый запрос доски Grafana
выполняется этой ролью без ошибки.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = r"""
import json
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

import psycopg

base = os.environ["ORBITA_TEST_POSTGRES_URI"]
name = "orbita_flow_" + secrets.token_hex(4)
role = "orbita_metrics_test"
password = secrets.token_hex(16)
with psycopg.connect(base, autocommit=True) as admin:
    admin.execute(f"CREATE DATABASE {name}")
parts = urlsplit(base)
uri = urlunsplit(parts._replace(path="/" + name))
os.environ["POSTGRES_URI"] = uri

from agent import db, flow_store, outcomes

try:
    with db.connection() as conn:
        versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY 1")]
    assert versions == [m[0] for m in db.MIGRATIONS], versions

    # ---------------------------------------------------------------- доска
    store = flow_store.store
    meta = {"board_id": 42, "name": "Платежи", "kind": "scrum", "project": "PAY",
            "jql": "project = PAY", "estimate_field": "customfield_10002", "hours": False}
    assert store.begin(meta, "anna") is True
    assert store.begin(meta, "tim") is False, "вторая отметка сбора прошла"
    now = datetime.now(UTC)
    timeline = {"created": now.isoformat(), "status": [[now.isoformat(), "3", "Dev", "indeterminate"]],
                "sprint": [[now.isoformat(), [12]]], "estimate": [], "flagged": []}
    row = {"board_id": 42, "key": "PAY-1", "issue_type": "Story", "subtask": False, "orbita": True,
           "status": "Done", "category": "done", "estimate": 3.0, "created_at": now - timedelta(days=9),
           "started_at": now - timedelta(days=8), "done_at": now - timedelta(days=2),
           "cycle_days": 6.0, "lead_days": 7.0, "reopens": 1, "analysis_returns": None,
           "blocked_hours": 4.5, "timeline": timeline, "jira_updated": now}
    assert store.save_issues([row, {**row, "key": "PAY-2", "orbita": False}]) == 2
    assert store.save_issues([{**row, "cycle_days": 5.0}]) == 1
    issues = {item["key"]: item for item in store.issues(42)}
    assert issues["PAY-1"]["cycle_days"] == 5.0
    assert issues["PAY-1"]["timeline"] == timeline
    assert issues["PAY-1"]["done_at"].tzinfo is not None
    sprint = {"board_id": 42, "sprint_id": 12, "name": "S12", "state": "closed",
              "start_at": now - timedelta(days=14), "end_at": now, "complete_at": now,
              "committed_count": 2, "committed_points": 5.0, "completed_count": 1,
              "completed_points": 3.0, "completed_committed_count": 1,
              "completed_committed_points": 3.0, "added_count": 1, "removed_count": 0,
              "carried_count": 1}
    store.save_sprints(42, [sprint])
    store.save_sprints(42, [sprint])
    assert [item["sprint_id"] for item in store.sprints(42)] == [12]
    early = now - timedelta(days=180)
    store.finish(42, state=flow_store.OK, detail="ok", synced_at=now, window_start=early)
    store.finish(42, state=flow_store.OK, synced_at=now, window_start=now - timedelta(days=30))
    store.finish(42, state=flow_store.FAILED, detail="x")
    board = store.board(42)
    assert board["window_start"] == early, board["window_start"]
    assert board["issues"] == 2 and board["state"] == flow_store.FAILED
    assert store.begin(meta, "tim") is True, "после итога сбора доска заперта"

    # --------------------------------------------------------------- оценки
    thread = "11111111-1111-4111-8111-111111111111"
    seen = {"graph": "prep", "thread_created_at": now - timedelta(hours=3), "turns": 2,
            "cost_usd": 0.01, "llm_calls": 5, "refs": 4, "verified": 3, "findings": 1,
            "jira_created": None}
    saved = outcomes.store.save("sub-anna", thread, {"verdict": "edited", "minutes": 20, "reason": "план"}, seen)
    assert saved["verdict_title"] == "принят с правками" and "owner" not in saved
    again = outcomes.store.save("sub-anna", thread, {"verdict": "accepted", "minutes": 25, "reason": ""}, seen)
    assert again["created_at"] == saved["created_at"] and again["verdict"] == "accepted"
    assert outcomes.store.get("sub-tim", thread) is None
    try:
        outcomes.store.save("sub-tim", thread, {"verdict": "rejected", "minutes": None, "reason": ""}, seen)
    except outcomes.OutcomeError:
        pass
    else:
        raise AssertionError("чужая оценка перезаписана")
    assert outcomes.store.get("sub-anna", thread)["verdict"] == "accepted"
    (summary,) = outcomes.store.summary(30)
    assert summary["graph"] == "prep" and summary["count"] == 1
    assert abs(summary["hours_to_verdict_p50"] - 3) < 0.1, summary

    # ------------------------------------------------------ роль читателя
    with db.connection() as conn:
        db.grant_reader(conn, password, role=role)
        db.grant_reader(conn, password, role=role)
    reader = urlunsplit(parts._replace(
        netloc=f"{role}:{password}@{parts.hostname}:{parts.port or 5432}", path="/" + name))
    with psycopg.connect(reader, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM flow_issues").fetchone()[0] == 2
        assert conn.execute("SELECT graph, verdict FROM run_outcomes").fetchall()
        for denied in ("SELECT owner FROM run_outcomes", "SELECT reason FROM run_outcomes",
                       "SELECT thread_id FROM run_outcomes", "SELECT jql FROM flow_boards",
                       "SELECT timeline FROM flow_issues", "SELECT * FROM user_secrets",
                       "SELECT * FROM actions"):
            try:
                conn.execute(denied)
            except psycopg.errors.InsufficientPrivilege:
                continue
            raise AssertionError("читателю открыто: " + denied)

        dashboard = json.loads(open("config/grafana/dashboards/orbita-flow.json", encoding="utf-8").read())
        queries = [target["rawSql"] for panel in dashboard["panels"] for target in panel.get("targets", [])]
        queries += [item["query"] for item in dashboard["templating"]["list"]]
        assert len(queries) > 10
        for query in queries:
            text = query.replace("$board", "42")
            text = re.sub(r"\$__timeFilter\((\w+)\)",
                          r"\1 BETWEEN now() - interval '400 days' AND now() + interval '1 day'", text)
            assert "$" not in text, text
            conn.execute(text).fetchall()
    print("ok")
finally:
    db.close()
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.execute(f"DROP ROLE IF EXISTS {role}")
"""


@pytest.mark.skipif(
    not os.environ.get("ORBITA_TEST_POSTGRES_URI"),
    reason="нужен Postgres: ORBITA_TEST_POSTGRES_URI=postgresql://… (например, сервис postgres из Compose)",
)
def test_flow_and_outcomes_round_trip_through_real_postgres():
    env = {**os.environ, "PYTHON_DOTENV_DISABLED": "1"}
    env.pop("METRICS_DB_PASSWORD", None)
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, timeout=120, check=False,
    )

    assert result.returncode == 0, result.stderr[-4000:]
    assert result.stdout.strip().endswith("ok")
