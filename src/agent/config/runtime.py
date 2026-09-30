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
    """База приложения (личные подключения) и CHECKPOINT_BACKEND=postgres."""
    return env_str("POSTGRES_URI", "postgresql://orbita:orbita@localhost:5432/orbita")


def postgres_configured() -> bool:
    """
    POSTGRES_URI задан явно.

    Адрес по умолчанию годится личным подключениям: без базы их нет вовсе, и
    попытка соединиться — единственный способ сказать почему. Изменения
    (`changes.py`) — другое дело: прогон подготовки обязан идти и без базы, и
    ждать таймаута соединения на каждом ходе там, где базы не заводили, незачем.
    """
    return env_opt("POSTGRES_URI") is not None


# --------------------------------------------------------------------------
# Личные подключения пользователей (credentials.py)
# --------------------------------------------------------------------------
def user_secrets_key() -> str | None:
    """Ключ шифрования личных токенов: случайная строка от 32 символов."""
    return env_opt("USER_SECRETS_KEY")


def user_secrets_old_keys() -> tuple[str, ...]:
    """Прежние ключи через запятую: ими читается то, что ещё не перешифровано."""
    return tuple(part.strip() for part in env_str("USER_SECRETS_OLD_KEYS").split(",") if part.strip())



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


def metrics_token() -> str | None:
    """
    Токен Prometheus: открывает только `GET /metrics`, от 16 символов.

    Отдельный, а не API_ADMIN_TOKEN: сборщику метрик незачем уметь править
    `.env` и читать чужие треды. Короче 16 символов — не принимается вовсе.
    """
    value = env_opt("METRICS_TOKEN")
    return value if value and len(value) >= 16 else None


def api_max_request_bytes() -> int:
    """Жёсткий потолок JSON-тела служебного API."""
    return env_int("API_MAX_REQUEST_BYTES", 65536, minimum=1024, maximum=10 * 1024 * 1024)


# --------------------------------------------------------------------------
# Вход пользователей через OIDC (Keycloak)
#
# Без OIDC_ISSUER API принимает только API_ADMIN_TOKEN. С ним — ещё и
# access-токены пользователей этого realm; админ-токен остаётся для
# healthcheck, скриптов и CI.
# --------------------------------------------------------------------------
def oidc_issuer() -> str | None:
    """Issuer realm, как его видит браузер: `iss` в токене обязан совпасть."""
    value = env_opt("OIDC_ISSUER")
    return value.rstrip("/") if value else None


def oidc_client_id() -> str:
    """Клиент realm, которым входит веб-интерфейс; токены других клиентов не принимаются."""
    return env_str("OIDC_CLIENT_ID", "orbita-web")


def oidc_jwks_url() -> str | None:
    """Ключи подписи, если серверу issuer виден по другому адресу, чем браузеру."""
    return env_opt("OIDC_JWKS_URL")


def oidc_required_role() -> str | None:
    """Роль realm или клиента, без которой вошедшему пользователю API закрыт."""
    return env_opt("OIDC_REQUIRED_ROLE")


def oidc_admin_role() -> str:
    """Роль, с которой пользователь правит настройки сервера и читает его журнал."""
    return env_str("OIDC_ADMIN_ROLE", "orbita-admin")



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
