"""
Доска «Orbita · поток задач» сверяется с тем, что ей на самом деле доступно.

Grafana читает базу ролью `orbita_metrics`, и у этой роли есть только то, что
перечислено в `db.READER_GRANTS`. Панель с запросом к таблице, которой в
списке нет, на живой Grafana показала бы отказ в правах — а заметили бы это
только глядя на доску. Выполнение каждого запроса этой ролью проверяет
`test_flow_postgres` на настоящей базе; здесь — то, что видно без неё.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from agent import db

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = json.loads((ROOT / "config/grafana/dashboards/orbita-flow.json").read_text(encoding="utf-8"))
SOURCES = yaml.safe_load(
    (ROOT / "config/grafana/provisioning/datasources/postgres.yml").read_text(encoding="utf-8")
)
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]


def queries() -> list[str]:
    found = [target["rawSql"] for panel in DASHBOARD["panels"] for target in panel.get("targets", [])]
    return found + [item["query"] for item in DASHBOARD["templating"]["list"]]


def test_every_panel_reads_the_orbita_database():
    (source,) = SOURCES["datasources"]
    uids = {panel["datasource"]["uid"] for panel in DASHBOARD["panels"] if panel["type"] != "row"}

    assert uids == {source["uid"]}
    assert source["type"] == "grafana-postgresql-datasource"


def test_grafana_logs_in_as_the_reader_with_the_password_the_agent_sets():
    (source,) = SOURCES["datasources"]
    grafana = COMPOSE["grafana"]["environment"]

    assert source["user"] == db.READER
    assert source["secureJsonData"]["password"] == "$ORBITA_METRICS_DB_PASSWORD"
    assert grafana["ORBITA_METRICS_DB_PASSWORD"] == "${METRICS_DB_PASSWORD:-}"
    assert "postgres" in COMPOSE["grafana"]["depends_on"]


def test_the_dashboard_asks_only_for_tables_the_reader_may_read():
    readable = {table for table, _ in db.READER_GRANTS}
    # `extract(epoch FROM …)` — не таблица.
    used = {name for query in queries() for name in re.findall(r"(?<!epoch )\bFROM\s+(\w+)", query)}

    assert used, "в доске нет ни одного запроса"
    assert used <= readable, used - readable


def test_the_reader_does_not_see_owners_threads_or_reasons():
    """Зрителей Grafana пускает без входа: личное в оценках им не показывается."""
    grants = dict(db.READER_GRANTS)
    outcome_columns = {column.strip() for column in grants["run_outcomes"].split(",")}

    assert not outcome_columns & {"owner", "thread_id", "reason"}
    assert "jql" not in grants["flow_boards"] and "timeline" not in grants["flow_issues"]
