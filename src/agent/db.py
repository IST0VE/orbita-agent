"""
База приложения в Postgres: то, что принадлежит пользователям, а не процессу.

Треды и чекпоинты здесь не живут — их держит сам сервер LangGraph. Здесь то,
чего у него нет: личные подключения Jira и Confluence (`credentials.py`),
изменения с их требованиями и Evidence (`changes.py`), внешние действия с их
согласиями и журналом операций (`actions.py`) — всё, что должно пережить
пересоздание контейнера и принадлежать конкретному человеку. Здесь же метрики
потока задач (`flow_store.py`) и оценки результатов прогонов (`outcomes.py`):
их читает ещё и Grafana, своей ролью только на чтение (`grant_reader`).

Схема заводится на месте, при первом обращении: список `MIGRATIONS` проходит
по порядку под advisory-блокировкой, сделанное отмечается в `schema_migrations`.
Отдельный шаг «накатите миграции» был бы ещё одной инструкцией, которую
забудут выполнить. Миграция только добавляется: правка уже выпущенной ничего
не изменит в базах, где она прошла.

Пул открывается лениво. Сервер без базы поднимается и работает, пока никому не
нужны личные подключения: отказ приходит тому, кто до них дошёл, и говорит,
что недоступно, — а не роняет весь процесс на старте.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg import sql
from psycopg_pool import ConnectionPool, PoolTimeout

from agent import config as cfg

#: Номер advisory-блокировки миграций: два процесса не накатывают схему разом.
_MIGRATION_LOCK = 0x0B17A
_TIMEOUT_S = 5.0

MIGRATIONS: tuple[tuple[int, str, str], ...] = (
    (
        1,
        "личные подключения",
        """
        CREATE TABLE user_secrets (
            subject     text        NOT NULL,
            name        text        NOT NULL,
            key_id      text        NOT NULL,
            nonce       bytea       NOT NULL,
            ciphertext  bytea       NOT NULL,
            updated_at  timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (subject, name)
        );
        CREATE TABLE user_connection_checks (
            subject     text        NOT NULL,
            system      text        NOT NULL,
            ok          boolean     NOT NULL,
            detail      text        NOT NULL,
            checked_at  timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (subject, system)
        );
        """,
    ),
    (
        2,
        "изменения, требования и Evidence",
        # Изменение живёт дольше треда (`changes.py`). Номер требования
        # устойчив: строка не удаляется, выбывшее получает статус `dropped`, и
        # его номер не занимается. Evidence — без текста источника: текст
        # прочитан чьим-то токеном и остаётся в треде того, кто читал; здесь
        # версия, хеш и читатель — чтобы узнать, изменился ли источник.
        """
        CREATE TABLE changes (
            id          text        PRIMARY KEY,
            owner       text        NOT NULL,
            key         text        NOT NULL,
            title       text        NOT NULL DEFAULT '',
            created_at  timestamptz NOT NULL DEFAULT now(),
            updated_at  timestamptz NOT NULL DEFAULT now(),
            UNIQUE (owner, key)
        );
        CREATE TABLE change_threads (
            change_id   text        NOT NULL REFERENCES changes (id) ON DELETE CASCADE,
            thread_id   text        NOT NULL,
            graph       text        NOT NULL,
            first_at    timestamptz NOT NULL DEFAULT now(),
            last_at     timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (change_id, thread_id)
        );
        CREATE TABLE requirements (
            change_id   text        NOT NULL REFERENCES changes (id) ON DELETE CASCADE,
            number      integer     NOT NULL,
            text        text        NOT NULL,
            fingerprint text        NOT NULL,
            status      text        NOT NULL CHECK (status IN ('active', 'dropped')),
            revision    integer     NOT NULL DEFAULT 1,
            evidence    text[]      NOT NULL DEFAULT '{}',
            thread_id   text        NOT NULL DEFAULT '',
            created_at  timestamptz NOT NULL DEFAULT now(),
            updated_at  timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (change_id, number)
        );
        CREATE TABLE requirement_history (
            change_id   text        NOT NULL REFERENCES changes (id) ON DELETE CASCADE,
            number      integer     NOT NULL,
            revision    integer     NOT NULL,
            text        text        NOT NULL,
            status      text        NOT NULL,
            thread_id   text        NOT NULL DEFAULT '',
            saved_at    timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX requirement_history_change ON requirement_history (change_id, number);
        CREATE TABLE evidence (
            change_id    text        NOT NULL REFERENCES changes (id) ON DELETE CASCADE,
            id           text        NOT NULL,
            system       text        NOT NULL,
            source_id    text        NOT NULL,
            title        text        NOT NULL DEFAULT '',
            url          text        NOT NULL DEFAULT '',
            version      text        NOT NULL DEFAULT '',
            location     text        NOT NULL DEFAULT '',
            content_hash text        NOT NULL,
            reader       text        NOT NULL,
            own          boolean     NOT NULL DEFAULT false,
            fetched_at   timestamptz NOT NULL,
            thread_id    text        NOT NULL DEFAULT '',
            PRIMARY KEY (change_id, id)
        );
        """,
    ),
    (
        3,
        "действия, согласования и журнал операций",
        # Единый порядок внешних записей (`actions.py`). Действие — одно
        # предложение с отпечатком содержимого, согласие привязано к отпечатку.
        # Операции живут дольше действия: их ключ — область (тред и цель), и
        # следующий ход того же треда по ним узнаёт, что уже сделано и с каким
        # содержимым. Тел документов и описаний задач здесь нет — только
        # заголовки, отпечатки и ключи на той стороне.
        """
        CREATE TABLE actions (
            id          text        PRIMARY KEY,
            kind        text        NOT NULL,
            graph       text        NOT NULL DEFAULT '',
            thread_id   text        NOT NULL DEFAULT '',
            owner       text        NOT NULL,
            scope       text        NOT NULL DEFAULT '',
            digest      text        NOT NULL,
            target      jsonb       NOT NULL DEFAULT '{}',
            operations  jsonb       NOT NULL DEFAULT '[]',
            status      text        NOT NULL,
            result      jsonb       NOT NULL DEFAULT '{}',
            created_at  timestamptz NOT NULL DEFAULT now(),
            updated_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX actions_owner ON actions (owner, updated_at DESC);
        CREATE INDEX actions_thread ON actions (thread_id, updated_at DESC);
        CREATE TABLE action_approvals (
            action_id   text        NOT NULL REFERENCES actions (id) ON DELETE CASCADE,
            decision    text        NOT NULL,
            digest      text        NOT NULL,
            actor       text        NOT NULL,
            reason      text        NOT NULL DEFAULT '',
            decided_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX action_approvals_action ON action_approvals (action_id, decided_at);
        CREATE TABLE action_operations (
            scope        text        NOT NULL,
            op           text        NOT NULL,
            key          text        NOT NULL,
            action_id    text        NOT NULL DEFAULT '',
            state        text        NOT NULL
                         CHECK (state IN ('pending', 'completed', 'failed', 'unknown')),
            label        text        NOT NULL,
            title        text        NOT NULL DEFAULT '',
            remote       text        NOT NULL DEFAULT '',
            url          text        NOT NULL DEFAULT '',
            detail       text        NOT NULL DEFAULT '',
            content_hash text        NOT NULL DEFAULT '',
            remote_hash  text        NOT NULL DEFAULT '',
            verified     text        NOT NULL DEFAULT '',
            updated_at   timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (scope, op, key)
        );
        CREATE INDEX action_operations_action ON action_operations (action_id);
        """,
    ),
    (
        4,
        "версия удалённой задачи для защиты дополнительных полей",
        "ALTER TABLE action_operations ADD COLUMN remote_version text NOT NULL DEFAULT '';",
    ),
    (
        5,
        "метрики потока задач",
        # Доска, её задачи и спринты (`flow_store.py`). История задачи лежит
        # целиком (`timeline`), а факты рядом — колонками: их считает код при
        # сборе, а Grafana читает простым SQL, без разбора JSON в запросе.
        # Людей здесь нет: исполнитель не читается и не хранится, названий
        # задач тоже нет — ключ, тип, статусы и даты.
        """
        CREATE TABLE flow_boards (
            board_id       integer     PRIMARY KEY,
            name           text        NOT NULL DEFAULT '',
            kind           text        NOT NULL DEFAULT '',
            project        text        NOT NULL DEFAULT '',
            jql            text        NOT NULL DEFAULT '',
            estimate_field text        NOT NULL DEFAULT '',
            hours          boolean     NOT NULL DEFAULT false,
            state          text        NOT NULL DEFAULT '',
            detail         text        NOT NULL DEFAULT '',
            synced_by      text        NOT NULL DEFAULT '',
            synced_at      timestamptz,
            window_start   timestamptz,
            issues         integer     NOT NULL DEFAULT 0,
            truncated      boolean     NOT NULL DEFAULT false,
            updated_at     timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE flow_issues (
            board_id         integer     NOT NULL REFERENCES flow_boards (board_id) ON DELETE CASCADE,
            key              text        NOT NULL,
            issue_type       text        NOT NULL DEFAULT '',
            subtask          boolean     NOT NULL DEFAULT false,
            orbita           boolean     NOT NULL DEFAULT false,
            status           text        NOT NULL DEFAULT '',
            category         text        NOT NULL DEFAULT '',
            estimate         double precision,
            created_at       timestamptz NOT NULL,
            started_at       timestamptz,
            done_at          timestamptz,
            cycle_days       double precision,
            lead_days        double precision,
            reopens          integer     NOT NULL DEFAULT 0,
            analysis_returns integer,
            blocked_hours    double precision NOT NULL DEFAULT 0,
            timeline         jsonb       NOT NULL DEFAULT '{}',
            jira_updated     timestamptz,
            synced_at        timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (board_id, key)
        );
        CREATE INDEX flow_issues_done ON flow_issues (board_id, done_at);
        CREATE TABLE flow_sprints (
            board_id                   integer     NOT NULL REFERENCES flow_boards (board_id) ON DELETE CASCADE,
            sprint_id                  integer     NOT NULL,
            name                       text        NOT NULL DEFAULT '',
            state                      text        NOT NULL DEFAULT '',
            start_at                   timestamptz,
            end_at                     timestamptz,
            complete_at                timestamptz,
            committed_count            integer     NOT NULL DEFAULT 0,
            committed_points           double precision NOT NULL DEFAULT 0,
            completed_count            integer     NOT NULL DEFAULT 0,
            completed_points           double precision NOT NULL DEFAULT 0,
            completed_committed_count  integer     NOT NULL DEFAULT 0,
            completed_committed_points double precision NOT NULL DEFAULT 0,
            added_count                integer     NOT NULL DEFAULT 0,
            removed_count              integer     NOT NULL DEFAULT 0,
            carried_count              integer     NOT NULL DEFAULT 0,
            computed_at                timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (board_id, sprint_id)
        );
        """,
    ),
    (
        6,
        "оценка результата прогона",
        # Одна оценка на тред (`outcomes.py`): последняя правка побеждает, а
        # время первой оценки остаётся — от него считается время до принятого
        # результата. Рядом снимок треда на момент оценки: сколько было ходов,
        # сколько стоило, что сказала проверка ссылок.
        """
        CREATE TABLE run_outcomes (
            thread_id         text        PRIMARY KEY,
            owner             text        NOT NULL,
            graph             text        NOT NULL DEFAULT '',
            verdict           text        NOT NULL
                              CHECK (verdict IN ('accepted', 'edited', 'reworked', 'rejected')),
            minutes           integer     CHECK (minutes IS NULL OR minutes BETWEEN 0 AND 10000),
            reason            text        NOT NULL DEFAULT '',
            thread_created_at timestamptz,
            turns             integer,
            cost_usd          double precision,
            llm_calls         integer,
            refs              integer,
            verified          integer,
            findings          integer,
            jira_created      integer,
            created_at        timestamptz NOT NULL DEFAULT now(),
            updated_at        timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX run_outcomes_owner ON run_outcomes (owner, updated_at DESC);
        CREATE INDEX run_outcomes_created ON run_outcomes (created_at);
        """,
    ),
    (
        7,
        "личные настройки",
        # Не секреты, а выбор человека: какой моделью он работает
        # (`llm_choice.py`). Значение — JSON, одна строка на настройку: подключение
        # и модель меняются вместе и читаются вместе.
        """
        CREATE TABLE user_preferences (
            subject     text        NOT NULL,
            name        text        NOT NULL,
            value       text        NOT NULL,
            updated_at  timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (subject, name)
        );
        """,
    ),
)

#: Роль Grafana: читает метрики и ничего больше (`grant_reader`).
READER = "orbita_metrics"

#: Что роли читателя видно. У оценок прогона нет владельца, треда и текста
#: причины: Grafana пускает зрителей без входа, а источник данных у неё один
#: на всех, и любой зритель может отправить в него свой SELECT.
READER_GRANTS = (
    ("flow_boards", "board_id, name, kind, project, estimate_field, hours, state, "
                    "synced_at, window_start, issues, truncated, updated_at"),
    ("flow_issues", "board_id, key, issue_type, subtask, orbita, status, category, estimate, "
                    "created_at, started_at, done_at, cycle_days, lead_days, reopens, "
                    "analysis_returns, blocked_hours, jira_updated"),
    ("flow_sprints", "*"),
    ("run_outcomes", "graph, verdict, minutes, thread_created_at, turns, cost_usd, llm_calls, "
                     "refs, verified, findings, jira_created, created_at, updated_at"),
)


class DatabaseUnavailable(RuntimeError):
    """База не отвечает или не принимает вход. Текст — для человека, без пароля."""


_pool: ConnectionPool | None = None
_lock = threading.Lock()
_LOG = logging.getLogger(__name__)


def scram_verifier(password: str, *, salt: bytes | None = None, iterations: int = 4096) -> str:
    """
    Пароль роли в виде, в котором его хранит Postgres (SCRAM-SHA-256).

    `ALTER ROLE … PASSWORD 'открытый текст'` оставляет пароль в журнале сервера
    при `log_statement=ddl` и в `pg_stat_activity` на время запроса. Готовый
    верификатор Postgres принимает как есть, и открытого пароля в базе не бывает.
    """
    salt = salt or os.urandom(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", "sha256").digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", "sha256").digest()
    encode = lambda raw: base64.b64encode(raw).decode("ascii")  # noqa: E731
    return f"SCRAM-SHA-256${iterations}:{encode(salt)}${encode(stored_key)}:{encode(server_key)}"


def grant_reader(conn: psycopg.Connection, password: str, role: str = READER) -> None:
    """
    Завести роль читателя метрик для Grafana и дать ей ровно `READER_GRANTS`.

    Права выдаются заново на каждом старте: сначала всё отнимается, потом
    выдаётся список. Колонка, убранная из списка, иначе осталась бы открытой
    навсегда. `statement_timeout` — потому что запросы в эту роль шлёт любой
    зритель доски, и тяжёлый SELECT не должен держать базу приложения.

    `role` — имя роли; другое, чем `READER`, нужно только проверке на живой
    базе: роль общая на весь сервер Postgres, и тест не должен менять пароль
    той, которой ходит настоящая Grafana.
    """
    name = sql.Identifier(role)
    with conn.transaction():
        conn.execute(
            sql.SQL(
                "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {literal}) "
                "THEN CREATE ROLE {role} LOGIN; END IF; END $$"
            ).format(literal=sql.Literal(role), role=name)
        )
        conn.execute(
            sql.SQL("ALTER ROLE {role} WITH LOGIN PASSWORD {verifier}").format(
                role=name, verifier=sql.Literal(scram_verifier(password))
            )
        )
        conn.execute(sql.SQL("ALTER ROLE {role} SET statement_timeout = '30s'").format(role=name))
        database = conn.execute("SELECT current_database()").fetchone()[0]
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {db} TO {role}").format(
                db=sql.Identifier(database), role=name
            )
        )
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {role}").format(role=name))
        conn.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}").format(role=name))
        for table, columns in READER_GRANTS:
            what = (
                sql.SQL("SELECT")
                if columns == "*"
                else sql.SQL("SELECT ({})").format(
                    sql.SQL(", ").join(sql.Identifier(column.strip()) for column in columns.split(","))
                )
            )
            conn.execute(
                sql.SQL("GRANT {what} ON {table} TO {role}").format(
                    what=what, table=sql.Identifier(table), role=name
                )
            )


def _migrate(conn: psycopg.Connection) -> None:
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK,))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version     integer     PRIMARY KEY,
                name        text        NOT NULL,
                applied_at  timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        for version, name, statement in MIGRATIONS:
            if version in done:
                continue
            conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)", (version, name)
            )


def _reason(exc: Exception) -> str:
    # Сообщение psycopg называет хост и пользователя, но не пароль; строку
    # подключения целиком не показываем никогда — в ней пароль есть.
    text = str(exc).strip().splitlines()
    return text[0] if text else type(exc).__name__


def _open() -> ConnectionPool:
    pool = ConnectionPool(
        cfg.postgres_uri(), min_size=1, max_size=4, open=False, timeout=_TIMEOUT_S,
        kwargs={"connect_timeout": int(_TIMEOUT_S)},
    )
    try:
        pool.open(wait=True, timeout=_TIMEOUT_S)
        with pool.connection() as conn:
            _migrate(conn)
    except (PoolTimeout, psycopg.Error) as exc:
        pool.close()
        raise DatabaseUnavailable(f"база Orbita недоступна (POSTGRES_URI): {_reason(exc)}") from exc
    password = cfg.metrics_db_password()
    if password:
        # Без роли читателя доска метрик пуста, но приложение работает: отказ
        # здесь (нет права CREATEROLE у пользователя базы) — повод написать в
        # журнал, а не останавливать личные подключения и журнал действий.
        try:
            with pool.connection() as conn:
                grant_reader(conn, password)
        except psycopg.Error as exc:
            _LOG.warning("роль %s для Grafana не заведена: %s", READER, _reason(exc))
    return pool


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """Соединение из пула; схема к этому моменту уже накатана."""
    global _pool
    with _lock:
        if _pool is None:
            _pool = _open()
        pool = _pool
    try:
        with pool.connection() as conn:
            yield conn
    except PoolTimeout as exc:
        raise DatabaseUnavailable(f"база Orbita недоступна (POSTGRES_URI): {_reason(exc)}") from exc
    except psycopg.OperationalError as exc:
        raise DatabaseUnavailable(f"база Orbita не ответила: {_reason(exc)}") from exc


def close() -> None:
    """Для тестов и скриптов: следующий `connection()` откроет пул заново."""
    global _pool
    with _lock:
        if _pool is not None:
            _pool.close()
            _pool = None
