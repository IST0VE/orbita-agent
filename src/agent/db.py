"""
База приложения в Postgres: то, что принадлежит пользователям, а не процессу.

Треды и чекпоинты здесь не живут — их держит сам сервер LangGraph. Здесь то,
чего у него нет: личные подключения Jira и Confluence (`credentials.py`),
изменения с их требованиями и Evidence (`changes.py`), внешние действия с их
согласиями и журналом операций (`actions.py`) — всё, что должно пережить
пересоздание контейнера и принадлежать конкретному человеку.

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

import threading
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
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
)


class DatabaseUnavailable(RuntimeError):
    """База не отвечает или не принимает вход. Текст — для человека, без пароля."""


_pool: ConnectionPool | None = None
_lock = threading.Lock()


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
        for version, name, sql in MIGRATIONS:
            if version in done:
                continue
            conn.execute(sql)
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
