"""
Задача 4.6: персистентность собственного рантайма.

Серверу LangGraph чекпоинтер не нужен — он держит хранилище сам. Модуль нужен
там, где граф запускает наш код: `run_demo.py`, скрипт по расписанию, контейнер
из docker-compose. Проверяется выбор бэкенда и внятность отказа: «поставьте
пакет» вместо ImportError из глубины импорта.
"""

from __future__ import annotations

import importlib.util

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from agent import config as cfg
from agent.checkpointer import open_checkpointer, open_store

_POSTGRES_INSTALLED = (
    importlib.util.find_spec("langgraph.checkpoint.postgres") is not None
)


def test_memory_is_the_default():
    with open_checkpointer() as saver:
        assert isinstance(saver, InMemorySaver)
    with open_store() as store:
        assert isinstance(store, InMemoryStore)


def test_unknown_backend_is_a_configuration_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHECKPOINT_BACKEND", "redis")

    with pytest.raises(cfg.ConfigError, match="CHECKPOINT_BACKEND"):
        with open_checkpointer():
            pass


@pytest.mark.skipif(_POSTGRES_INSTALLED, reason="пакет установлен, отказа не будет")
@pytest.mark.parametrize("opener", [open_checkpointer, open_store])
def test_postgres_without_the_package_says_what_to_install(
    opener, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("CHECKPOINT_BACKEND", "postgres")

    with pytest.raises(cfg.ConfigError, match="postgres"):
        with opener():
            pass


def test_connection_string_has_a_working_default():
    assert cfg.postgres_uri().startswith("postgresql://")
