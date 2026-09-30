"""Роли конвейера метрик потока: показатели доски -> сводка для команды."""

from __future__ import annotations

import re

from agent import flow_prompts
from agent.pipeline import Pipeline, Role, Stage

# Ключ в state["artifacts"], который пишет не роль, а нода сбора
# (`flow_graph.flow_node`): таблицы показателей. Подставляется роли тем же
# `brief()`, что и документы этапов, и уходит приложением на страницу.
FLOW = "flow"
FLOW_TITLE = "Показатели"

ROLES: tuple[Role, ...] = (
    Role(
        key="summary",
        number="01",
        title="Сводка по потоку",
        summary="Что показывают числа потока и о чём спросить команду",
        needs=(FLOW,),
    ),
)

#: Период по умолчанию, если в запросе его нет. Квартал — горизонт, на
#: котором у команды набирается больше десятка закрытых задач, а у
#: предыдущего квартала хватает истории в окне FLOW_HISTORY_DAYS.
DEFAULT_PERIOD = 90
MAX_PERIOD = 365

_BOARD = (
    re.compile(r"rapidView=(\d+)", re.IGNORECASE),
    re.compile(r"/boards/(\d+)", re.IGNORECASE),
    re.compile(r"\b(?:доск\w*|board)[\s:]*(?:№|#|номер)?\s*(\d+)", re.IGNORECASE),
)
_PERIOD_NUMBER = re.compile(
    r"\bза\s+(?:последн\w+\s+)?(\d{1,3})\s*(дн\w*|день|недел\w*|нед\.?|месяц\w*|мес\.?)",
    re.IGNORECASE,
)
_PERIOD_WORD = (
    (re.compile(r"\bза\s+(?:последн\w+\s+)?полгода\b", re.IGNORECASE), 182),
    (re.compile(r"\bза\s+(?:последн\w+\s+)?год\b", re.IGNORECASE), 365),
    (re.compile(r"\bза\s+(?:последн\w+\s+)?квартал\b", re.IGNORECASE), 91),
    (re.compile(r"\bза\s+(?:последн\w+\s+)?месяц\b", re.IGNORECASE), 30),
    (re.compile(r"\bза\s+(?:последн\w+\s+)?(?:две|2)\s+недели\b", re.IGNORECASE), 14),
    (re.compile(r"\bза\s+(?:последн\w+\s+)?неделю\b", re.IGNORECASE), 7),
)


def boards_in(text: str) -> list[int]:
    """Номера досок, названные в запросе: словом, адресом доски или `rapidView`."""
    found: dict[int, None] = {}
    for pattern in _BOARD:
        for match in pattern.finditer(text or ""):
            found.setdefault(int(match.group(1)), None)
    return [board for board in found if board > 0]


def period_in(text: str) -> int | None:
    """Период в днях, если запрос его называет: «за 60 дней», «за квартал»."""
    match = _PERIOD_NUMBER.search(text or "")
    if match:
        number, unit = int(match.group(1)), match.group(2).casefold()
        days = number * (7 if unit.startswith("нед") else 30 if unit.startswith("мес") else 1)
        return max(7, min(days, MAX_PERIOD))
    for pattern, days in _PERIOD_WORD:
        if pattern.search(text or ""):
            return days
    return None


def prompt_for(key: str) -> str:
    return flow_prompts.for_role(key)


def brief(role: Role, task: str, artifacts: dict | None) -> str:
    """Запрос оператора и таблицы показателей, посчитанные кодом."""
    artifacts = artifacts or {}
    found = (artifacts.get(FLOW) or "").strip()
    table = (
        f"# {FLOW_TITLE}\n\n{found}"
        if found
        else f"# {FLOW_TITLE}\n\nПоказатели не посчитаны. Не описывай поток по памяти "
        "и не придумывай числа: скажи, что сводки нет, и почему — если причина названа."
    )
    return f"# Запрос оператора\n\n{task.strip()}\n\n{table}"


def subject(state: dict) -> str:
    """Какая доска и какой период — строкой под задачей."""
    measured = state.get("flow") or {}
    if not measured.get("board_id"):
        return ""
    name = f" «{measured['name']}»" if measured.get("name") else ""
    source = (
        "сохранённая история доски" if measured.get("stored") else "свежее чтение Jira без сохранения"
    )
    return (
        f"Доска {measured['board_id']}{name}, период {measured.get('period')} дн. "
        f"({measured.get('since', '')} — {measured.get('until', '')}). "
        f"Данные: {source}, на {measured.get('synced', '')}."
    )


PIPELINE = Pipeline(
    key="metrics",
    title="Метрики потока",
    summary="Показатели потока задач доски Jira за период: считает код, сводку пишет модель.",
    byline="конвейером метрик потока",
    roles=ROLES,
    prompt_for=prompt_for,
    brief=brief,
    # Доска не названа — отчёта нет: выбирать доску за оператора нельзя, даже
    # если настроена одна (`FLOW_BOARDS`).
    admission=True,
    prelude=Stage(
        key="flow",
        title="Сбор и расчёт метрик",
        summary="Без вызова модели читает историю задач доски в Jira и считает показатели.",
    ),
    one_page=True,
    lead=("summary",),
    appendix=((FLOW, FLOW_TITLE),),
    subject=subject,
    tidy=True,
    russian=True,
)
