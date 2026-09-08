"""
Персистентность собственного рантайма: чекпоинтер треда и store поверх тредов.

Серверу LangGraph это не нужно — `langgraph dev` и `langgraph up` держат
хранилище сами, и граф для них компилируется без чекпоинтера (см. конец
`graph.py`). Модуль нужен там, где граф запускает наш код: `run_demo.py`,
скрипт по расписанию, свой сервис в контейнере.

Два варианта:

    memory    (по умолчанию) всё живёт в процессе и умирает вместе с ним;
    postgres  чекпоинты и долгая память переживают перезапуск.

Postgres подключается пакетами `langgraph-checkpoint-postgres` и `psycopg` —
они не в основных зависимостях, потому что нужны не всем: `pip install ".[postgres]"`.
Строка подключения — POSTGRES_URI; готовый Postgres рядом с агентом поднимает
`docker compose up`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from agent import config as cfg

_MISSING_PACKAGE = (
    "CHECKPOINT_BACKEND=postgres: нужны пакеты langgraph-checkpoint-postgres "
    'и psycopg (pip install ".[postgres]")'
)


@contextmanager
def open_checkpointer() -> Iterator[Any]:
    """
    Чекпоинтер по CHECKPOINT_BACKEND. Контекстный менеджер, потому что у
    Postgres за ним стоит пул соединений, который надо закрыть.
    """
    if cfg.checkpoint_backend() == "memory":
        yield InMemorySaver()
        return

    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError as exc:
        raise cfg.ConfigError(_MISSING_PACKAGE) from exc

    with PostgresSaver.from_conn_string(cfg.postgres_uri()) as saver:
        # Таблицы создаются на месте: отдельный шаг миграции для двух таблиц
        # означал бы ещё одну инструкцию в README, которую забудут выполнить.
        saver.setup()
        yield saver


@contextmanager
def open_store() -> Iterator[Any]:
    """
    Store для долгой памяти между тредами (`memory.py`).

    На memory-бэкенде он живёт в процессе: демо покажет память в пределах
    одного прогона, но не между запусками. Чтобы второй тред помнил первый
    через сутки, нужен postgres.
    """
    if cfg.checkpoint_backend() == "memory":
        yield InMemoryStore()
        return

    try:
        from langgraph.store.postgres import PostgresStore
    except ImportError as exc:
        raise cfg.ConfigError(_MISSING_PACKAGE) from exc

    with PostgresStore.from_conn_string(cfg.postgres_uri()) as store:
        store.setup()
        yield store
