"""Роли конвейера проджект-менеджера: доска Jira -> где мы -> приоритеты -> план спринта."""

from __future__ import annotations

import re

from agent import pm_prompts
from agent.pipeline import Pipeline, Role, Stage

# Ключ в state["artifacts"], который пишет не роль, а нода чтения доски
# (`pm_graph.board_node`): таблицы, посчитанные кодом. Подставляется ролям тем
# же `brief()`, что и документы этапов, и уходит приложением на страницу.
BOARD = "board"
BOARD_TITLE = "Данные доски"

ROLES: tuple[Role, ...] = (
    Role(
        key="status",
        number="01",
        title="Где мы сейчас",
        summary="Этап спринта, эпиков и релизов, риски и вопросы к команде",
        needs=(BOARD,),
    ),
    Role(
        key="priorities",
        number="02",
        title="Приоритеты",
        summary="Что важнее всего сейчас и где ранг доски спорит со сроками и блокировками",
        needs=(BOARD, "status"),
    ),
    Role(
        key="plan",
        number="03",
        title="План спринта",
        summary="Цель, ёмкость и состав следующего спринта — предложение к планированию",
        needs=(BOARD, "status", "priorities"),
    ),
)

BY_KEY = {role.key: role for role in ROLES}

#: Между словом «ёмкость» и числом — только служебное: «следующего спринта»,
#: «команды», двоеточие, тире. Произвольный текст между ними означает, что
#: число уже про другое: «оцени ёмкость и план релиза 2.4» ёмкость не называет.
_FILLER = (
    r"(?:\s*[:=—–]"
    r"|\s+(?:следующ\w*|нов\w*|ближайш\w*|команд\w*|спринт\w*|на|в|будет|равна|составляет"
    r"|около|примерно|ставим|считай|возьми|возьм[её]м))*"
)
#: Число целиком и без знака: «-5» — не ёмкость, «10000» не режется до «1000»,
#: «2.4.1» — номер версии, а «30%» — доля, а не объём.
_AMOUNT = r"\s*(?<![-−\d.,])(\d+(?:[.,]\d+)?)(?![\d.,]*\d)(?!\s*%)"
_PERCENT = r"(?<![-−\d.,])(\d{1,3})(?![\d.,]*\d)\s*%"
_CAPACITY = re.compile(
    r"\b(?:[её]мкост\w*|capacity|вместимост\w*)\b" + _FILLER + _AMOUNT, re.IGNORECASE
)
_AVAILABLE = re.compile(
    r"\b(?:доступност\w*|availability|в\s+строю)\b" + _FILLER + r"\s*" + _PERCENT,
    re.IGNORECASE,
)
_AWAY = re.compile(
    _PERCENT
    + r"\s*(?:команды\s+)?(?:в\s+отпуск\w*|отсутству\w*|недоступн\w*|на\s+больничн\w*)",
    re.IGNORECASE,
)
_RESET = re.compile(r"\b[её]мкост\w*\s+(?:по\s+скорости|как\s+обычно|по\s+умолчанию)",
                    re.IGNORECASE)


def capacity_in(text: str) -> float | None:
    """
    Ёмкость, названная числом сразу за словом: «ёмкость 30», «capacity: 24,5».

    Ошибиться здесь дороже, чем не узнать: подхваченное чужое число молча
    становится чертой плана, а неузнанная ёмкость — это медиана скорости
    и строка «как посчитана» в отчёте, которую оператор увидит.
    """
    match = _CAPACITY.search(text or "")
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    return value if value > 0 else None


def availability_in(text: str) -> float | None:
    """
    Доля команды в строю: «доступность 80%» — 0.8, «20% в отпуске» — тоже 0.8.

    Вне 1–100% — не доступность, а опечатка, и подставлять её молча нельзя.
    """
    match = _AVAILABLE.search(text or "")
    if match and 0 < int(match.group(1)) <= 100:
        return int(match.group(1)) / 100
    match = _AWAY.search(text or "")
    if match and 0 <= int(match.group(1)) < 100:
        return (100 - int(match.group(1))) / 100
    return None


def capacity_reset(text: str) -> bool:
    """Просьба вернуть ёмкость к скорости команды: «ёмкость по скорости»."""
    return bool(_RESET.search(text or ""))


def prompt_for(key: str) -> str:
    return pm_prompts.for_role(key)


def brief(role: Role, task: str, artifacts: dict | None) -> str:
    """Запрос оператора, таблицы кода и документы предыдущих ролей."""
    artifacts = artifacts or {}
    parts = [f"# Запрос оператора\n\n{task.strip()}"]
    for key in role.needs:
        if key == BOARD:
            found = (artifacts.get(BOARD) or "").strip()
            parts.append(
                f"# {BOARD_TITLE}\n\n{found}"
                if found
                else f"# {BOARD_TITLE}\n\nДоска не прочитана. Не описывай спринт и бэклог по "
                "памяти и не придумывай задачи и числа: скажи, что отчёта нет, и почему — "
                "если причина названа."
            )
            continue
        source = BY_KEY[key]
        text = (artifacts.get(key) or "").strip()
        parts.append(
            f"# Результат этапа {source.number}. {source.title}\n\n{text}"
            if text
            else f"# Результат этапа {source.number}. {source.title}\n\nЭтап не выполнен, "
            "документа нет. Не ссылайся на него и не додумывай, что в нём было бы."
        )
    return "\n\n".join(parts)


def subject(state: dict) -> str:
    """Какая доска и когда прочитана — строкой под задачей."""
    found = state.get("pm") or {}
    if not found.get("board_id"):
        return ""
    name = f" «{found['name']}»" if found.get("name") else ""
    line = f"Доска {found['board_id']}{name}, прочитана {found.get('read_at', '')}."
    if found.get("capacity") is not None:
        line += f" Ёмкость названа оператором: {found['capacity']:g}."
    elif found.get("availability") is not None:
        line += f" Доступность команды: {round(found['availability'] * 100)}%."
    return line


PIPELINE = Pipeline(
    key="pm",
    title="Проджект-менеджер",
    summary=(
        "Этап спринта, эпиков и релизов, приоритеты и план следующего спринта по доске "
        "Jira: числа считает код, выводы и предложения пишет модель."
    ),
    byline="конвейером проджект-менеджера",
    roles=ROLES,
    prompt_for=prompt_for,
    brief=brief,
    # Доска не названа — отчёта нет: выбирать доску за оператора нельзя, даже
    # если сервер собирает ровно одну (`FLOW_BOARDS`).
    admission=True,
    prelude=Stage(
        key="board",
        title="Чтение доски и расчёт",
        summary=(
            "Без вызова модели читает спринты, бэклог по рангу, эпики и версии и считает "
            "статус, скорость, ёмкость, план и прогноз релизов."
        ),
    ),
    one_page=True,
    appendix=((BOARD, BOARD_TITLE),),
    subject=subject,
    tidy=True,
    russian=True,
)
