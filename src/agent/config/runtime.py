"""Рантайм: чекпоинты, имя агента, служебный API и демо."""

from __future__ import annotations

from agent.config.env import (  # noqa: F401
    ConfigError,
    env_bool,
    env_float,
    env_int,
    env_opt,
    env_str,
)

# --------------------------------------------------------------------------
# Персистентность собственного рантайма
#
# `langgraph dev` держит своё хранилище сам, и графу чекпоинтер не нужен —
# см. компиляцию в конце graph.py. Эти настройки нужны там, где граф
# запускается своим кодом: run_demo.py, скрипт в cron, свой сервис.
# --------------------------------------------------------------------------
CHECKPOINT_BACKENDS = ("memory", "postgres")


def checkpoint_backend() -> str:
    backend = env_str("CHECKPOINT_BACKEND", "memory").lower()
    if backend not in CHECKPOINT_BACKENDS:
        raise ConfigError(
            f"CHECKPOINT_BACKEND={backend!r}: поддерживаются " + ", ".join(CHECKPOINT_BACKENDS)
        )
    return backend


def postgres_uri() -> str:
    """Строка подключения для CHECKPOINT_BACKEND=postgres."""
    return env_str("POSTGRES_URI", "postgresql://orbita:orbita@localhost:5432/orbita")



# --------------------------------------------------------------------------
# Агент
# --------------------------------------------------------------------------
def agent_name() -> str:
    """Имя агента: идёт в заголовок страницы и в подпись под документацией."""
    return env_str("AGENT_NAME", "Orbita")



# --------------------------------------------------------------------------
# Локальный служебный HTTP API
# --------------------------------------------------------------------------
def api_admin_token() -> str | None:
    """Обязательный Bearer-токен для всего HTTP API; задаётся только на сервере."""
    return env_opt("API_ADMIN_TOKEN")


def api_max_request_bytes() -> int:
    """Жёсткий потолок JSON-тела служебного API."""
    return env_int("API_MAX_REQUEST_BYTES", 65536, minimum=1024, maximum=10 * 1024 * 1024)



# --------------------------------------------------------------------------
# Демо-скрипт
# --------------------------------------------------------------------------
def demo_thread_id() -> str:
    """
    Один thread_id на весь прогон: история дописывается в конец, префикс
    только растёт и остаётся закешированным.
    """
    return env_str("DEMO_THREAD_ID", "demo-thread-1")


def demo_answer_chars() -> int:
    """Сколько символов ответа модели печатать в консоль."""
    return env_int("DEMO_ANSWER_CHARS", 600, minimum=0)
