"""
Чтение доски Jira для метрик потока: конфигурация, спринты, задачи с историей.

Только чтение и тем же транспортом, что у остального проекта (`jira.call`):
та же авторизация, та же пауза перед запросом против защитного шлюза, тот же
разбор отказов. Своего здесь — Agile API (`/rest/agile/1.0`): доски и спринты
живут там, а не в `/rest/api/2`.

JQL собирается кодом из фильтра доски и окна по дате обновления. Чужого JQL
этот модуль не принимает: фильтр доски составил её владелец в Jira, а окно —
число дней из настройки.

Поле оценки не угадывается по имени: его называет конфигурация доски
(`estimation.field`), и у каждой Jira это свой `customfield_…`. Поле спринта
и флаг блокировки находятся по типу поля (`schema.custom`), а не по названию:
название переводят и переименовывают, тип — нет.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from agent import jira
from agent.flow import Fields

AGILE = "/rest/agile/1.0"

SPRINT_TYPE = "com.pyxis.greenhopper.jira:gh-sprint"
FLAGGED_NAMES = {"flagged", "помечено", "флаг"}

#: Задач на страницу поиска. Больше Data Center не отдаёт, а у Cloud с историей
#: изменений потолок ниже — он вернёт столько, сколько может, и страниц станет больше.
PAGE = 100

_ORDER_BY = re.compile(r"\s+ORDER\s+BY\s+.*$", re.IGNORECASE | re.DOTALL)


class FlowJiraError(jira.JiraError):
    """Доску не прочитать: нет её, нет прав или это не доска Jira Software."""


def board(board_id: int, s: jira.Settings) -> dict:
    """Имя и тип доски: `scrum` или `kanban`."""
    data = jira.call("GET", f"{AGILE}/board/{int(board_id)}", s)
    location = data.get("location") or {}
    return {
        "board_id": int(board_id),
        "name": str(data.get("name") or ""),
        "kind": str(data.get("type") or ""),
        "project": str(location.get("projectKey") or ""),
    }


def configuration(board_id: int, s: jira.Settings) -> dict:
    """Фильтр доски и поле оценки. Без фильтра доски не бывает."""
    data = jira.call("GET", f"{AGILE}/board/{int(board_id)}/configuration", s)
    filter_id = str((data.get("filter") or {}).get("id") or "")
    if not filter_id:
        raise FlowJiraError(f"у доски {board_id} не найден фильтр задач")
    estimation = (data.get("estimation") or {}).get("field") or {}
    return {
        "filter_id": filter_id,
        "estimate": str(estimation.get("fieldId") or ""),
        "estimate_name": str(estimation.get("displayName") or ""),
    }


def board_jql(filter_id: str, s: jira.Settings) -> str:
    """JQL фильтра доски без сортировки: её не обернуть в `( … ) AND …`."""
    data = jira.call("GET", f"{s.api_path}/filter/{filter_id}", s)
    jql = _ORDER_BY.sub("", str(data.get("jql") or "")).strip()
    if not jql:
        raise FlowJiraError(f"фильтр {filter_id} доски пуст")
    return jql


def categories(s: jira.Settings) -> dict[str, str]:
    """Категория каждого статуса по его id: `new`, `indeterminate`, `done`."""
    data = jira.call("GET", f"{s.api_path}/status", s)
    return {
        str(item.get("id")): str((item.get("statusCategory") or {}).get("key") or "")
        for item in data or []
        if isinstance(item, dict) and item.get("id") is not None
    }


def fields(config: dict, s: jira.Settings) -> Fields:
    """Id и имена полей оценки, спринта и флага для этой Jira."""
    data = jira.call("GET", f"{s.api_path}/field", s)
    sprint = flagged = ""
    sprint_name, flagged_name, estimate_name = "Sprint", "Flagged", config.get("estimate_name", "")
    for item in data or []:
        if not isinstance(item, dict):
            continue
        schema = item.get("schema") or {}
        name = str(item.get("name") or "")
        if schema.get("custom") == SPRINT_TYPE and not sprint:
            sprint, sprint_name = str(item.get("id") or ""), name or sprint_name
        elif name.casefold() in FLAGGED_NAMES and not flagged:
            flagged, flagged_name = str(item.get("id") or ""), name
        if item.get("id") == config.get("estimate") and name:
            estimate_name = name
    return Fields(
        estimate=config.get("estimate", ""),
        estimate_name=estimate_name,
        sprint=sprint,
        sprint_name=sprint_name,
        flagged=flagged,
        flagged_name=flagged_name,
    )


def sprints(board_id: int, s: jira.Settings) -> list[dict]:
    """
    Спринты доски: идущие и закрытые. У канбан-доски спринтов нет — пустой список.

    Будущие не нужны: у них нет старта, и в показателях им места нет.
    """
    found: list[dict] = []
    start = 0
    while True:
        try:
            data = jira.call(
                "GET", f"{AGILE}/board/{int(board_id)}/sprint", s,
                params={"state": "active,closed", "startAt": start, "maxResults": 50},
            )
        except jira.JiraError as exc:
            # Канбан-доска отвечает 400 «The board does not support sprints».
            if "HTTP 400" in str(exc) and not found:
                return []
            raise
        values = data.get("values") or []
        for item in values:
            if not isinstance(item, dict) or item.get("id") is None:
                continue
            found.append({
                "sprint_id": int(item["id"]),
                "name": str(item.get("name") or ""),
                "state": str(item.get("state") or "").lower(),
                "start_at": item.get("startDate"),
                "end_at": item.get("endDate"),
                "complete_at": item.get("completeDate"),
            })
        start += len(values)
        if data.get("isLast", True) or not values:
            return found


def _fields_param(f: Fields) -> str:
    wanted = ["issuetype", "created", "updated", "status", "labels"]
    wanted += [name for name in (f.estimate, f.sprint, f.flagged) if name]
    return ",".join(dict.fromkeys(wanted))


def search(jql: str, f: Fields, s: jira.Settings, *, limit: int) -> Iterator[dict]:
    """
    Задачи по JQL с историей изменений, страница за страницей.

    Cloud (`/search/jql`) листает по `nextPageToken`, Data Center (`/search`) —
    по `startAt`. `limit` — потолок на весь сбор: дойдя до него, поиск
    останавливается, и сборщик сообщает, что задачи прочитаны не все.
    """
    params: dict[str, Any] = {
        "jql": jql, "fields": _fields_param(f), "expand": "changelog", "maxResults": PAGE,
    }
    path = f"{s.api_path}{s.search_path}"
    seen = 0
    start = 0
    token = None
    while seen < limit:
        page = dict(params)
        if s.search_path.endswith("/jql"):
            if token:
                page["nextPageToken"] = token
        else:
            page["startAt"] = start
        data = jira.call("GET", path, s, params=page)
        issues = [item for item in data.get("issues") or [] if isinstance(item, dict)]
        for issue in issues:
            if seen >= limit:
                return
            seen += 1
            yield issue
        start += len(issues)
        token = data.get("nextPageToken")
        if not issues:
            return
        if s.search_path.endswith("/jql"):
            if not token or data.get("isLast"):
                return
        elif data.get("total") is not None and start >= int(data["total"]):
            return


def truncated(issue: dict) -> bool:
    """Поиск отдал историю не целиком: у Cloud в поиске её потолок — 100 записей."""
    log = issue.get("changelog") or {}
    histories = log.get("histories") or []
    total = log.get("total")
    return isinstance(total, int) and total > len(histories)


def full_changelog(key: str, s: jira.Settings) -> list[dict]:
    """
    Вся история одной задачи — для тех, у кого поиск её обрезал.

    У Cloud для этого свой постраничный путь (`/issue/{key}/changelog`), у Data
    Center его нет, и история приходит целиком в `expand=changelog` задачи.
    """
    if s.search_path.endswith("/jql"):
        found: list[dict] = []
        start = 0
        while True:
            data = jira.call(
                "GET", f"{s.api_path}/issue/{key}/changelog", s,
                params={"startAt": start, "maxResults": 100},
            )
            values = data.get("values") or []
            found += [item for item in values if isinstance(item, dict)]
            start += len(values)
            if data.get("isLast", True) or not values:
                return found
    data = jira.call(
        "GET", f"{s.api_path}/issue/{key}", s, params={"fields": "created", "expand": "changelog"}
    )
    return list((data.get("changelog") or {}).get("histories") or [])
