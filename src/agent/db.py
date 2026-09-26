"""
База приложения в Postgres: то, что принадлежит пользователям, а не процессу.

Треды и чекпоинты здесь не живут — их держит сам сервер LangGraph. Здесь то,
чего у него нет: личные подключения Jira и Confluence (`credentials.py`), дальше —
всё, что должно пережить пересоздание контейнера и принадлежать конкретному
человеку.

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
