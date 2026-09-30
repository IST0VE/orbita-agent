"""
Метрики потока в базе приложения: доски, их задачи и спринты (миграция 5).

Хранилище — только Postgres. Без базы метрики не копятся, но и не пропадают
совсем: граф `metrics` считает показатели по свежему чтению Jira прямо в
прогоне (`flow_sync.read`). Файлового запасного варианта, как у журнала
действий, здесь нет: метрики нужны доске Grafana, а она читает базу.

Задача хранится строкой с историей (`timeline`) и фактами рядом. Факты
пересчитываются из истории, когда меняются правила (`FLOW_ANALYSIS_STATUSES`),
без повторного чтения трекера (`flow_sync.recount`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from agent import config as cfg
from agent import db

_ISSUE_COLUMNS = (
    "board_id", "key", "issue_type", "subtask", "orbita", "status", "category", "estimate",
    "created_at", "started_at", "done_at", "cycle_days", "lead_days", "reopens",
    "analysis_returns", "blocked_hours", "timeline", "jira_updated",
)
_SPRINT_COLUMNS = (
    "board_id", "sprint_id", "name", "state", "start_at", "end_at", "complete_at",
    "committed_count", "committed_points", "completed_count", "completed_points",
    "completed_committed_count", "completed_committed_points", "added_count",
    "removed_count", "carried_count",
)
_BOARD_COLUMNS = (
    "board_id", "name", "kind", "project", "jql", "estimate_field", "hours", "state",
    "detail", "synced_by", "synced_at", "window_start", "issues", "truncated", "updated_at",
)

#: Первый ключ advisory-блокировки сбора; второй — номер доски.
_LOCK = 0x0F10

#: Состояния сбора доски.
RUNNING = "running"
OK = "ok"
FAILED = "failed"


def available() -> None:
    """Можно ли идти в базу. Нельзя — `db.DatabaseUnavailable` с причиной."""
    if not cfg.postgres_configured():
        raise db.DatabaseUnavailable("база не настроена (POSTGRES_URI не задан)")


def _upsert(table: str, columns: tuple[str, ...], keys: tuple[str, ...]) -> str:
    updates = ", ".join(f"{name} = excluded.{name}" for name in columns if name not in keys)
    return (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})"
        f" ON CONFLICT ({', '.join(keys)}) DO UPDATE SET {updates}"
    )


class PostgresFlow:
    """Доски, задачи и спринты в Postgres."""

    def _json(self, value: Any) -> Any:
        from psycopg.types.json import Jsonb

        return Jsonb(value)

    # ------------------------------------------------------------------ доски
    def boards(self) -> list[dict]:
        with db.connection() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(_BOARD_COLUMNS)} FROM flow_boards ORDER BY board_id"
            ).fetchall()
        return [dict(zip(_BOARD_COLUMNS, row, strict=True)) for row in rows]

    def board(self, board_id: int) -> dict | None:
        with db.connection() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_BOARD_COLUMNS)} FROM flow_boards WHERE board_id = %s",
                (int(board_id),),
            ).fetchone()
        return dict(zip(_BOARD_COLUMNS, row, strict=True)) if row else None

    def begin(self, meta: Mapping[str, Any], who: str) -> bool:
        """
        Отметить доску как собираемую. False — её уже собирают.

        Сбор идёт минутами (пауза перед каждым запросом к трекеру), и второй
        такой же параллельно удвоил бы нагрузку на Jira ради тех же строк.
        Отметка старше часа считается брошенной: процесс мог упасть посреди
        сбора, и доска не должна остаться запертой навсегда.
        """
        with db.connection() as conn, conn.transaction():
            # Строки доски может ещё не быть, и `FOR UPDATE` тогда ничего не
            # запирает: два первых сбора разом прошли бы оба.
            conn.execute("SELECT pg_advisory_xact_lock(%s, %s)", (_LOCK, int(meta["board_id"])))
            row = conn.execute(
                "SELECT state, updated_at > now() - interval '1 hour'"
                " FROM flow_boards WHERE board_id = %s",
                (int(meta["board_id"]),),
            ).fetchone()
            if row and row[0] == RUNNING and row[1]:
                return False
            conn.execute(
                "INSERT INTO flow_boards (board_id, name, kind, project, jql, estimate_field,"
                " hours, state, detail, synced_by, updated_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, '', %s, now())"
                " ON CONFLICT (board_id) DO UPDATE SET name = excluded.name,"
                " kind = excluded.kind, project = excluded.project, jql = excluded.jql,"
                " estimate_field = excluded.estimate_field, hours = excluded.hours,"
                " state = excluded.state, detail = '', synced_by = excluded.synced_by,"
                " updated_at = now()",
                (
                    int(meta["board_id"]), meta.get("name", ""), meta.get("kind", ""),
                    meta.get("project", ""), meta.get("jql", ""), meta.get("estimate_field", ""),
                    bool(meta.get("hours")), RUNNING, who,
                ),
            )
        return True

    def finish(self, board_id: int, *, state: str, detail: str = "",
               synced_at: datetime | None = None, window_start: datetime | None = None,
               truncated: bool = False) -> None:
        """
        Итог сбора. Время успешного сбора — отметка для следующего, инкрементного.

        Начало окна только отодвигается назад: сбор за неделю после сбора за
        полгода не делает историю короче. `LEAST` пропускает NULL, поэтому
        неудавшийся сбор (окна нет) прежнего окна не трогает.
        """
        with db.connection() as conn, conn.transaction():
            conn.execute(
                "UPDATE flow_boards SET state = %s, detail = %s,"
                " synced_at = COALESCE(%s, synced_at),"
                " window_start = LEAST(window_start, %s), truncated = %s,"
                " issues = (SELECT count(*) FROM flow_issues WHERE board_id = %s),"
                " updated_at = now() WHERE board_id = %s",
                (state, detail[:2000], synced_at, window_start, truncated,
                 int(board_id), int(board_id)),
            )

    # ----------------------------------------------------------------- задачи
    def save_issues(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """Задачи доски: новые добавляются, прочитанные заново — заменяются."""
        params = [
            tuple(
                self._json(row["timeline"]) if name == "timeline" else row.get(name)
                for name in _ISSUE_COLUMNS
            )
            for row in rows
        ]
        if not params:
            return 0
        statement = _upsert("flow_issues", _ISSUE_COLUMNS, ("board_id", "key"))
        statement = statement.replace(
            "jira_updated = excluded.jira_updated",
            "jira_updated = excluded.jira_updated, synced_at = now()",
        )
        with db.connection() as conn, conn.transaction(), conn.cursor() as cursor:
            cursor.executemany(statement, params)
        return len(params)

    def issues(self, board_id: int) -> list[dict]:
        with db.connection() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(_ISSUE_COLUMNS)} FROM flow_issues WHERE board_id = %s",
                (int(board_id),),
            ).fetchall()
        return [dict(zip(_ISSUE_COLUMNS, row, strict=True)) for row in rows]

    # ---------------------------------------------------------------- спринты
    def save_sprints(self, board_id: int, rows: Iterable[Mapping[str, Any]]) -> None:
        """
        Спринты доски целиком: их факты пересчитываются на каждом сборе.

        Строки заменяются, а не дописываются. Спринт, удалённый с доски в Jira,
        иначе остался бы в базе со старыми числами навсегда.
        """
        params = [tuple(row.get(name) for name in _SPRINT_COLUMNS) for row in rows]
        with db.connection() as conn, conn.transaction():
            conn.execute("DELETE FROM flow_sprints WHERE board_id = %s", (int(board_id),))
            if params:
                with conn.cursor() as cursor:
                    cursor.executemany(
                        f"INSERT INTO flow_sprints ({', '.join(_SPRINT_COLUMNS)})"
                        f" VALUES ({', '.join(['%s'] * len(_SPRINT_COLUMNS))})",
                        params,
                    )

    def sprints(self, board_id: int) -> list[dict]:
        with db.connection() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(_SPRINT_COLUMNS)} FROM flow_sprints WHERE board_id = %s"
                " ORDER BY start_at",
                (int(board_id),),
            ).fetchall()
        return [dict(zip(_SPRINT_COLUMNS, row, strict=True)) for row in rows]


store = PostgresFlow()
