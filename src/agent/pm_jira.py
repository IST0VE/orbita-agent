"""
Чтение доски Jira для проджект-менеджера: спринты, бэклог, эпики и релизы.

Метрикам потока (`flow_jira.py`) нужна история задач, а проджект-менеджеру —
нынешнее состояние доски в том порядке, в котором её видит команда: что в
идущем спринте, что уже набрано в следующий, что лежит в бэклоге и в каком
ранге. Порядок задач — главное, что отсюда уезжает: ранг доски выставил
владелец продукта, и план спринта, собранный в другом порядке, — это уже
чужое решение о приоритетах. Поэтому задачи читаются Agile API
(`/rest/agile/1.0`): его списки отдаются по рангу, а поиск `/rest/api` —
в порядке JQL.

Транспорт общий (`jira.call`): та же авторизация, та же пауза перед каждым
запросом и тот же разбор отказов. Пауза здесь ощутима — запрос на страницу
списка и по запросу на эпик, — поэтому число страниц и эпиков ограничено
настройками (PM_BACKLOG_LIMIT, PM_EPICS), а отчёт говорит, если упёрся.

Людей здесь нет, как и в метриках потока: исполнитель не запрашивается.
Ёмкость команды считается по её скорости, а не по сумме чьих-то часов.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agent import config as cfg
from agent import db, flow, flow_jira, flow_sync, jira

AGILE = flow_jira.AGILE

#: Задач на страницу списка Agile API. Больше Cloud не отдаёт.
PAGE = 50

#: Сколько будущих спринтов читать. Первый — тот, что планируется; следующие
#: нужны прогнозу релизов: их задач нет в бэклоге, и без них работа впереди
#: релиза оказалась бы меньше настоящей.
FUTURE = 3

#: Где задача на доске. Порядок — порядок работы: сначала идущий спринт,
#: потом следующий, потом остальные будущие, потом бэклог.
ACTIVE = "active"
NEXT = "next"
FUTURE_PLACE = "future"
BACKLOG = "backlog"
#: У канбан-доски спринтов нет: начатое — «в работе», остальное — очередь.
WORK = "work"
QUEUE = "queue"

#: Имена типа «эпик». Тип, который назвала команда (JIRA_EPIC_TYPE), к ним
#: добавляется: в русифицированных инстансах он свой.
_EPIC_TYPES = {"epic", "эпик"}

#: Имена приоритетов по уровню: 1 — самый высокий. Уровень нужен коду, чтобы
#: увидеть «высокий приоритет за чертой плана», а схемы приоритетов у Jira
#: свои; незнакомое имя уровня не получает, и сигнала по нему нет.
_PRIORITY_LEVELS = (
    (1, ("highest", "blocker", "critical", "наивысший", "высший", "блокирующий",
         "блокер", "критический", "критичный", "срочный")),
    (2, ("high", "major", "высокий", "важный", "серьезный", "серьёзный")),
    (3, ("medium", "normal", "средний", "обычный", "нормальный")),
    (4, ("low", "minor", "низкий", "незначительный")),
    (5, ("lowest", "trivial", "самый низкий", "наименьший", "тривиальный")),
)
_LEVEL = {name: level for level, names in _PRIORITY_LEVELS for name in names}


def priority_level(name: str) -> int | None:
    """Уровень приоритета по имени: 1 — высший, 5 — низший, None — имя незнакомо."""
    return _LEVEL.get((name or "").strip().casefold())


# --------------------------------------------------------------------------
# Спринты и версии
# --------------------------------------------------------------------------
def sprints(board_id: int, s: jira.Settings) -> list[dict] | None:
    """
    Идущие и будущие спринты доски с целями. None — доска спринтов не ведёт.

    Закрытые не нужны: их итоги считает сбор метрик потока по истории задач,
    и второй расчёт скорости здесь разошёлся бы с первым.
    """
    found: list[dict] = []
    start = 0
    while True:
        try:
            data = jira.call(
                "GET", f"{AGILE}/board/{int(board_id)}/sprint", s,
                params={"state": "active,future", "startAt": start, "maxResults": PAGE},
            )
        except jira.JiraError as exc:
            # Канбан-доска отвечает 400 «The board does not support sprints».
            if "HTTP 400" in str(exc) and not found:
                return None
            raise
        values = data.get("values") or []
        for item in values:
            if not isinstance(item, dict) or item.get("id") is None:
                continue
            found.append({
                "sprint_id": int(item["id"]),
                "name": str(item.get("name") or ""),
                "state": str(item.get("state") or "").lower(),
                "goal": str(item.get("goal") or "").strip(),
                "start_at": item.get("startDate"),
                "end_at": item.get("endDate"),
            })
        start += len(values)
        if data.get("isLast", True) or not values:
            return found


def versions(board_id: int, s: jira.Settings) -> list[dict]:
    """
    Невыпущенные версии проектов доски: id, имя и дата релиза, если она назначена.

    Имя версии уникально только внутри проекта, а доска бывает на несколько
    проектов: «2.4» платежей и «2.4» отчётов — разные релизы с разными
    датами. Поэтому задачи с версией сопоставляются по id.
    """
    found: list[dict] = []
    start = 0
    while True:
        data = jira.call(
            "GET", f"{AGILE}/board/{int(board_id)}/version", s,
            params={"released": "false", "startAt": start, "maxResults": PAGE},
        )
        values = data.get("values") or []
        for item in values:
            if not isinstance(item, dict) or item.get("id") is None or item.get("archived"):
                continue
            found.append({
                "id": str(item["id"]),
                "name": str(item.get("name") or ""),
                "project_id": str(item.get("projectId") or ""),
                "release_date": str(item.get("releaseDate") or "") or None,
            })
        start += len(values)
        if data.get("isLast", True) or not values:
            return found


def board_epics(board_id: int, s: jira.Settings) -> list[dict] | None:
    """
    Незакрытые эпики доски по рангу. None — доска эпиков не ведёт.

    Из задач доски видны только эпики с незакрытой работой на ней. Эпик, у
    которого закрыто всё, а сам он открыт, в бэклоге не появится вовсе — а
    именно его отчёт обязан назвать «готов к закрытию».
    """
    found: list[dict] = []
    start = 0
    while True:
        try:
            data = jira.call(
                "GET", f"{AGILE}/board/{int(board_id)}/epic", s,
                params={"done": "false", "startAt": start, "maxResults": PAGE},
            )
        except jira.JiraError as exc:
            # Доска без эпиков (канбан без панели эпиков) отвечает 400.
            if "HTTP 400" in str(exc) and not found:
                return None
            raise
        values = data.get("values") or []
        for item in values:
            if not isinstance(item, dict) or not item.get("key") or item.get("done"):
                continue
            found.append({
                "key": str(item["key"]),
                "name": str(item.get("name") or item.get("summary") or ""),
            })
        start += len(values)
        if data.get("isLast", True) or not values:
            return found


# --------------------------------------------------------------------------
# Задачи
# --------------------------------------------------------------------------
def fields_param(f: flow.Fields) -> str:
    """
    Поля задачи для списков Agile API.

    `epic` и `flagged` — поля самого Agile API: он отдаёт их одинаково на Cloud
    и Data Center, а кастомные id связи с эпиком и флага у каждой Jira свои.
    Те кастомные, что известны, просятся тоже — на случай инстанса, где Agile
    своих полей не добавил.
    """
    wanted = [
        "summary", "issuetype", "status", "priority", "labels", "duedate", "created",
        "updated", "issuelinks", "fixVersions", "parent", "epic", "flagged",
    ]
    wanted += [name for name in (f.estimate, f.flagged, cfg.jira_epic_link_field()) if name]
    return ",".join(dict.fromkeys(wanted))


def listing(path: str, s: jira.Settings, *, fields: str, limit: int,
            jql: str = "") -> tuple[list[dict], bool]:
    """
    Задачи списка Agile API по рангу, страница за страницей. Второе — упёрлись ли в `limit`.

    Списки Agile листаются по `startAt` и знают `total` и на Cloud, и на Data
    Center, поэтому разницы `/search` и `/search/jql` здесь нет. Страница
    просится не больше остатка до потолка: так прочитанное сверх потолка не
    выбрасывается молча, а `total` честно говорит, осталось ли что-то за ним.
    """
    params: dict[str, Any] = {"fields": fields}
    if jql:
        params["jql"] = jql
    found: list[dict] = []
    start = 0
    while True:
        page = min(PAGE, limit - len(found))
        data = jira.call("GET", path, s, params={**params, "startAt": start, "maxResults": page})
        issues = [item for item in data.get("issues") or [] if isinstance(item, dict)]
        found += issues[:page]
        start += len(issues)
        total = data.get("total")
        if not issues or (total is not None and start >= int(total)):
            return found, False
        if len(found) >= limit:
            return found, True


def sprint_issues(board_id: int, sprint_id: int, f: flow.Fields, s: jira.Settings,
                  *, limit: int) -> tuple[list[dict], bool]:
    """Задачи спринта в пределах фильтра доски, по рангу."""
    return listing(
        f"{AGILE}/board/{int(board_id)}/sprint/{int(sprint_id)}/issue", s,
        fields=fields_param(f), limit=limit,
    )


def backlog(board_id: int, f: flow.Fields, s: jira.Settings, *,
            limit: int) -> tuple[list[dict], bool]:
    """Бэклог доски по рангу: незакрытые задачи вне идущих и будущих спринтов."""
    return listing(
        f"{AGILE}/board/{int(board_id)}/backlog", s, fields=fields_param(f), limit=limit,
    )


def open_issues(board_id: int, f: flow.Fields, s: jira.Settings, *,
                limit: int) -> tuple[list[dict], bool]:
    """
    Незакрытые задачи канбан-доски по рангу.

    Бэклог у канбана бывает выключен — тогда `/backlog` отвечает отказом, —
    а список задач доски есть всегда. Готовые отсекает JQL: план строится
    по тому, что ещё предстоит.
    """
    return listing(
        f"{AGILE}/board/{int(board_id)}/issue", s, fields=fields_param(f), limit=limit,
        jql="statusCategory != Done",
    )


def epic_issues(key: str, f: flow.Fields, s: jira.Settings, *,
                limit: int) -> tuple[list[dict], bool]:
    """
    Все задачи эпика, на любых досках.

    Этап эпика — это доля сделанного во всём эпике, а не в его части на этой
    доске: работа соседней команды по тому же эпику тоже его двигает.
    """
    names = ["status", "issuetype", *([f.estimate] if f.estimate else [])]
    return listing(f"{AGILE}/epic/{key}/issue", s, fields=",".join(names), limit=limit)


# --------------------------------------------------------------------------
# Разбор задачи
# --------------------------------------------------------------------------
def _category(status: Any) -> str:
    if not isinstance(status, dict):
        return ""
    return str((status.get("statusCategory") or {}).get("key") or "")


def _is_epic_type(kind: Any) -> bool:
    if not isinstance(kind, dict):
        return False
    names = _EPIC_TYPES | {cfg.jira_epic_type().casefold()}
    return str(kind.get("name") or "").casefold() in names or kind.get("hierarchyLevel") == 1


def _blocking(kind: Any) -> bool:
    """Связь «блокирует»: по имени типа или по тексту его направления."""
    if not isinstance(kind, dict):
        return False
    words = " ".join(
        str(kind.get(name) or "") for name in ("name", "outward", "inward")
    ).casefold()
    return "block" in words or "блок" in words


def links(values: Any) -> tuple[list[str], list[str]]:
    """
    Кого задача блокирует и кем заблокирована — только незакрытые с другой стороны.

    У задачи X запись с `outwardIssue: Y` читается «X блокирует Y», запись
    с `inwardIssue: Y` — «X заблокирована Y». Закрытая блокирующая задача уже
    ничего не держит, а закрытую блокированную уже нечем задержать.
    """
    blocks: list[str] = []
    blocked_by: list[str] = []
    for link in values or []:
        if not isinstance(link, dict) or not _blocking(link.get("type")):
            continue
        for side, target in (("outwardIssue", blocks), ("inwardIssue", blocked_by)):
            other = link.get(side)
            if not isinstance(other, dict) or not other.get("key"):
                continue
            if _category((other.get("fields") or {}).get("status")) == flow.DONE:
                continue
            target.append(str(other["key"]))
    return sorted(set(blocks)), sorted(set(blocked_by))


def _epic(data: dict) -> tuple[str, str, bool]:
    """
    Ключ, имя эпика задачи и закрыт ли он сам.

    Источники по порядку: поле Agile, родитель-эпик, поле связи с эпиком. О
    закрытости последнее ничего не говорит — такой эпик считается открытым.
    """
    epic = data.get("epic")
    if isinstance(epic, dict) and epic.get("key"):
        return (str(epic["key"]), str(epic.get("name") or epic.get("summary") or ""),
                bool(epic.get("done")))
    parent = data.get("parent")
    if isinstance(parent, dict) and parent.get("key"):
        parent_fields = parent.get("fields") or {}
        if _is_epic_type(parent_fields.get("issuetype")):
            return (str(parent["key"]), str(parent_fields.get("summary") or ""),
                    _category(parent_fields.get("status")) == flow.DONE)
    link_field = cfg.jira_epic_link_field()
    if link_field and isinstance(data.get(link_field), str) and data[link_field].strip():
        return data[link_field].strip(), "", False
    return "", "", False


def _flagged(data: dict, f: flow.Fields) -> bool:
    if data.get("flagged") is True:
        return True
    return bool(f.flagged and data.get(f.flagged))


def issue_of(raw: dict, f: flow.Fields, *, place: str, sprint_id: int | None = None) -> dict:
    """
    Задача доски в виде, который читает расчёт (`pm.py`): только то, что ему нужно.

    Время и даты остаются строками: строка уезжает в состояние треда, и
    следующий ход пересчитывает план по ней, не перечитывая Jira.
    """
    data = raw.get("fields") or {}
    kind = data.get("issuetype") or {}
    status = data.get("status") or {}
    priority = data.get("priority") or {}
    epic_key, epic_name, epic_done = _epic(data)
    blocks, blocked_by = links(data.get("issuelinks"))
    estimate = flow.estimate_of(data.get(f.estimate), f) if f.estimate else None
    return {
        "key": str(raw.get("key") or ""),
        "summary": " ".join(str(data.get("summary") or "").split())[:200],
        "type": str(kind.get("name") or ""),
        "subtask": bool(kind.get("subtask")),
        "is_epic": _is_epic_type(kind),
        "status": str(status.get("name") or ""),
        "category": _category(status),
        "priority": str(priority.get("name") or ""),
        "level": priority_level(str(priority.get("name") or "")),
        "estimate": estimate,
        "flagged": _flagged(data, f),
        "due": str(data.get("duedate") or "") or None,
        "created": data.get("created"),
        "epic": epic_key,
        "epic_name": epic_name,
        "epic_done": epic_done,
        # Id, а не имена: имя версии уникально только внутри проекта (`versions`).
        "versions": [
            str(item.get("id")) for item in data.get("fixVersions") or []
            if isinstance(item, dict) and item.get("id") is not None
        ],
        "blocks": blocks,
        "blocked_by": blocked_by,
        "place": place,
        "sprint_id": sprint_id,
    }


def child_of(raw: dict, f: flow.Fields) -> dict:
    """Задача эпика: для этапа нужны только категория статуса и оценка."""
    data = raw.get("fields") or {}
    kind = data.get("issuetype") or {}
    return {
        "key": str(raw.get("key") or ""),
        "subtask": bool(kind.get("subtask")),
        "category": _category(data.get("status")),
        "estimate": flow.estimate_of(data.get(f.estimate), f) if f.estimate else None,
    }


# --------------------------------------------------------------------------
# Снимок доски
# --------------------------------------------------------------------------
#: Потолок задач одного спринта и одного эпика: дальше это не спринт и не эпик,
#: а второй бэклог, и читать его целиком незачем.
SPRINT_LIMIT = 300
EPIC_LIMIT = 500

#: За сколько дней брать историю спринтов у сбора метрик потока. Он считает
#: этот период и такой же до него, то есть полгода: при двухнедельных
#: спринтах это дюжина закрытых — с запасом на PM_VELOCITY_SPRINTS.
HISTORY_DAYS = 90


def _history(board_id: int, *, who: str, store_ok: bool) -> dict:
    """
    Закрытые спринты и недели из сбора метрик потока: скорость команды.

    Скорость здесь не считается второй раз: итог спринта (взято, сделано,
    перенесено) сбор метрик уже восстановил из истории задач, и вторая
    арифметика разошлась бы с первой. Отказ сбора — не отказ отчёта: без
    скорости нет ёмкости, но статус спринта и приоритеты остаются.
    """
    try:
        measured = flow_sync.measure(board_id, HISTORY_DAYS, who=who, store_ok=store_ok)
    except (jira.JiraError, db.DatabaseUnavailable, flow_sync.FlowBusy, ValueError) as exc:
        return {"read": False, "error": str(exc)}
    found: dict[int, dict] = {}
    weeks: dict[datetime, int] = {}
    periods = [summary for summary in (measured.previous, measured.current) if summary]
    since = flow.parse_time(periods[0].get("since"))
    for summary in periods:
        for sprint in summary.get("sprints") or []:
            found[int(sprint["sprint_id"])] = sprint
        # Граница периодов может разрезать неделю: в каждом отчёте только
        # его часть закрытых задач. Их надо сложить, а не выбрать одну.
        for row in summary.get("weeks") or []:
            week = flow.parse_time(row.get("week"))
            if week is not None and (since is None or week >= since):
                weeks[week] = weeks.get(week, 0) + int(row.get("done") or 0)
    return flow_sync.jsonable({
        "read": True,
        "stored": measured.stored,
        "sprints": list(found.values()),
        "weeks": [{"week": week, "done": done} for week, done in sorted(weeks.items())],
        "oldest": (measured.current.get("wip") or {}).get("oldest") or [],
        "notes": measured.notes,
    })


def epic_order(items: list[dict], listed: list[dict] | None) -> list[tuple[str, str]]:
    """
    Какие эпики проверять и в каком порядке: ключ и имя.

    Первыми — эпики с незакрытой работой на доске, по рангу их ближайшей
    задачи: их этап нужен плану. За ними — открытые эпики, незакрытой работы
    которых на доске не видно: задачи закрыты в идущем спринте, или эпик есть
    в списке эпиков доски (`board_epics`) без единой задачи в бэклоге. Это
    кандидаты в «готов к закрытию»; отбери их здесь — и этот вывод отчёт
    обещал бы, не имея из чего его сделать. Закрытые эпики не проверяются.
    """
    names: dict[str, str] = {}
    busy: list[str] = []
    quiet: list[str] = []
    for item in items:
        key = item.get("epic")
        if not key or item.get("epic_done"):
            continue
        names[key] = names.get(key) or item.get("epic_name") or ""
        target = busy if item.get("category") != flow.DONE else quiet
        if key not in target:
            target.append(key)
    for epic in listed or []:
        names[epic["key"]] = names.get(epic["key"]) or epic["name"]
        if epic["key"] not in quiet:
            quiet.append(epic["key"])
    return [(key, names[key]) for key in [*busy, *(key for key in quiet if key not in busy)]]


def _epics(order: list[tuple[str, str]], f: flow.Fields, s: jira.Settings) -> list[dict]:
    """Все задачи каждого эпика из `order`. Отказ одного эпика записывается в него."""
    found = []
    for key, name in order:
        try:
            raw, limited = epic_issues(key, f, s, limit=EPIC_LIMIT)
        except jira.JiraError as exc:
            found.append({"key": key, "name": name, "error": str(exc)})
            continue
        found.append({
            "key": key, "name": name, "limited": limited,
            "children": [child_of(item, f) for item in raw],
        })
    return found


def collect(board_id: int, *, who: str = "", store_ok: bool = True,
            now: datetime | None = None) -> dict:
    """
    Снимок доски для расчёта (`pm.build`): спринты, задачи по рангу, эпики, версии, история.

    Снимок — данные, а не выводы: он уезжает в состояние треда, и следующий
    ход («а если ёмкость 25?») пересчитывает план по нему, не перечитывая
    Jira. Отказ чтения доски поднимается исключением — без доски отчёта нет;
    отказ версий, эпиков или истории только записывается в заметки.
    """
    now = now or datetime.now(UTC)
    s = jira.load_settings()
    meta = flow_jira.board(board_id, s)
    f = flow_jira.fields(flow_jira.configuration(board_id, s), s)
    notes: list[str] = []
    items: list[dict] = []
    limit = cfg.pm_backlog_limit()

    listed = None if meta["kind"] == "kanban" else sprints(board_id, s)
    active: list[dict] = []
    upcoming: list[dict] = []
    if listed is None:
        mode = "kanban"
        raw, limited = open_issues(board_id, f, s, limit=limit)
        for issue in raw:
            started = _category((issue.get("fields") or {}).get("status")) == flow.WORK
            items.append(issue_of(issue, f, place=WORK if started else QUEUE))
        if limited:
            notes.append(f"Прочитаны первые {limit} незакрытых задач доски по рангу (PM_BACKLOG_LIMIT).")
    else:
        mode = "scrum"
        active = [sprint for sprint in listed if sprint["state"] == "active"]
        upcoming = [sprint for sprint in listed if sprint["state"] == "future"]
        for sprint in active:
            raw, cut = sprint_issues(board_id, sprint["sprint_id"], f, s, limit=SPRINT_LIMIT)
            items += [issue_of(issue, f, place=ACTIVE, sprint_id=sprint["sprint_id"]) for issue in raw]
            if cut:
                notes.append(f"Спринт «{sprint['name']}» прочитан не целиком: первые {SPRINT_LIMIT} задач.")
        for index, sprint in enumerate(upcoming[:FUTURE]):
            raw, cut = sprint_issues(board_id, sprint["sprint_id"], f, s, limit=SPRINT_LIMIT)
            place = NEXT if index == 0 else FUTURE_PLACE
            items += [issue_of(issue, f, place=place, sprint_id=sprint["sprint_id"]) for issue in raw]
            if cut:
                notes.append(f"Спринт «{sprint['name']}» прочитан не целиком: первые {SPRINT_LIMIT} задач.")
        raw, limited = backlog(board_id, f, s, limit=limit)
        items += [issue_of(issue, f, place=BACKLOG) for issue in raw]
        if limited:
            notes.append(f"Бэклог прочитан не целиком: первые {limit} задач по рангу (PM_BACKLOG_LIMIT).")

    try:
        found_versions = versions(board_id, s)
    except jira.JiraError as exc:
        found_versions = []
        notes.append(f"Версии доски не прочитаны: {exc}.")
    epics: list[dict] = []
    wanted = cfg.pm_epics()
    if wanted:
        try:
            listed = board_epics(board_id, s)
        except jira.JiraError as exc:
            listed = None
            notes.append(
                f"Список эпиков доски не прочитан: {exc}. Эпики без незакрытых задач на "
                "доске не проверены — «готов к закрытию» среди них не найти."
            )
        order = epic_order(items, listed)
        epics = _epics(order[:wanted], f, s)
        if len(order) > wanted:
            rest = [key for key, _ in order[wanted:]]
            notes.append(
                f"Этап посчитан для {wanted} эпиков из {len(order)} (PM_EPICS); не посчитаны: "
                + ", ".join(rest[:10]) + (f" и ещё {len(rest) - 10}" if len(rest) > 10 else "")
                + "."
            )
    history = _history(board_id, who=who, store_ok=store_ok)
    return {
        "board": {
            **meta,
            "estimate_field": f.estimate,
            "estimate_name": f.estimate_name,
            "hours": f.hours,
        },
        "mode": mode,
        "read_at": flow.iso(now),
        "active": active,
        "next": upcoming[:FUTURE],
        "future_total": len(upcoming),
        "items": items,
        "epics": epics,
        "versions": found_versions,
        "history": history,
        "notes": notes,
    }
