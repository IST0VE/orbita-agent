"""Метрики потока задач: какие доски, за какое окно и как часто собирать."""

from __future__ import annotations

from agent.config.env import (  # noqa: F401
    ConfigError,
    env_bool,
    env_float,
    env_int,
    env_opt,
    env_str,
)


def flow_boards() -> tuple[int, ...]:
    """
    Доски Jira, которые сервер собирает сам, раз в FLOW_SYNC_INTERVAL_H.

    Номер доски — число из её адреса (`rapidView=42`, `/boards/42`). Пусто —
    фоновый сбор выключен, доски собираются по запросу (граф `metrics`,
    `POST /api/flow/boards/{id}/sync`, `scripts/flow_sync.py`).
    """
    raw = env_str("FLOW_BOARDS")
    found: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit() or int(part) <= 0:
            raise ConfigError(f"FLOW_BOARDS={raw!r}: ожидаются номера досок через запятую")
        found.append(int(part))
    return tuple(dict.fromkeys(found))


def flow_history_days() -> int:
    """
    За сколько дней назад читать историю задач при первом сборе доски.

    Полгода — чтобы базовые показатели пилота были с первого дня, и чтобы у
    квартала в отчёте был предыдущий квартал для сравнения.
    """
    return env_int("FLOW_HISTORY_DAYS", 180, minimum=14, maximum=730)


def flow_sync_interval_h() -> int:
    """Раз в сколько часов фоновый сбор проходит по FLOW_BOARDS. 0 — не ходить."""
    return env_int("FLOW_SYNC_INTERVAL_H", 24, minimum=0, maximum=168)


def flow_max_issues() -> int:
    """
    Потолок задач на один сбор доски.

    Каждая страница поиска ждёт паузу ATLASSIAN_REQUEST_INTERVAL_S, и доска на
    сорок тысяч задач заняла бы трекер на час. Дойдя до потолка, сбор
    останавливается и отмечает доску как прочитанную не целиком.
    """
    return env_int("FLOW_MAX_ISSUES", 5000, minimum=100, maximum=50000)


def _names(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def flow_analysis_statuses() -> tuple[str, ...]:
    """
    Статусы аналитики и уточнения через запятую: «Анализ, Уточнение требований».

    Переход в такой статус из разработки или готовности считается возвратом в
    аналитику. Категория Jira этих статусов не отличает от разработки — они
    тоже «в работе», — поэтому команда называет их сама. Пусто — возвраты не
    считаются, и отчёт говорит об этом, а не показывает ноль.
    """
    return _names(env_str("FLOW_ANALYSIS_STATUSES"))


def flow_blocked_statuses() -> tuple[str, ...]:
    """Статусы блокировки через запятую; флаг задачи («Flagged») учитывается и без них."""
    return _names(env_str("FLOW_BLOCKED_STATUSES"))
