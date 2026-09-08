"""
Запись в Jira: заведение задач по разобранному плану.

Обратное направление к `jira.py`, и оно живёт отдельным модулем не из
аккуратности. Чтение — операция без последствий: прочитали не ту задачу,
перечитали другую. Запись последствия оставляет в чужой системе, и стоят они
не столько, сколько прогон: тридцать задач, заведённых не в тот проект, кто-то
закрывает руками по одной, а уведомления о них уже ушли всей команде.

Поэтому здесь три границы, и ни одна из них не в промпте:

  что заводить   решает `jira_plan.py`, а не модель: в трекер уезжает
                 разобранная и проверенная структура, а не текст ответа;
  куда заводить  решает оператор: проект — единственное, чего в аналитике нет
                 и вывести его неоткуда (см. `jira_graph.create_node`);
  заводить ли    решает `JIRA_CREATE_ISSUES` и, по умолчанию, подтверждение
                 человека перед самим вызовом.

Диалектов, как и у чтения, два, и различает их `JIRA_API_PATH`:

    /rest/api/3   Cloud: описание отправляется ADF-документом
    /rest/api/2   Server / Data Center: описание отправляется строкой

Разница не косметическая: Cloud на строку в поле `description` отвечает 400,
и узнать об этом на середине пачки — значит завести половину.

Пачка заводится по одной задаче, а не bulk-эндпоинтом. Bulk у Jira есть, но он
отвечает одним общим отказом на весь список: одна карточка с типом, которого
в проекте нет, и не заведено ничего. Здесь наоборот: что прошло — прошло, что
не прошло — перечислено с причиной. Прогон, заведший восемь задач из десяти,
полезнее прогона, не заведшего ни одной.
"""

from __future__ import annotations

from typing import Any

from agent import config as cfg
from agent import jira, jira_fields, jira_plan

JiraError = jira.JiraError


def missing_vars() -> list[str]:
    return jira.missing_vars()


def is_configured() -> bool:
    return jira.is_configured()


def is_enabled() -> bool:
    """Рубильник записи. Выключен — конвейер выпускает план и не ходит в трекер."""
    return cfg.jira_create_issues()


def _cloud(s: jira.Settings) -> bool:
    """Cloud или Data Center: от этого зависит формат описания и путь метаданных."""
    return s.api_path.rstrip("/").endswith("3")


def _get(method: str, path: str, s: jira.Settings, **kwargs) -> Any:
    """
    Тот же транспорт, что у чтения.

    Отдельная обёртка нужна только из-за типа: часть эндпоинтов Data Center
    отвечает списком, а не объектом, и подпись `jira.call` про это не знает.
    """
    return jira.call(method, path, s, **kwargs)


# --------------------------------------------------------------------------
# Описание задачи
#
# ADF собирается минимальный: абзацы и маркированные списки. Полный конвертер
# Markdown тут был бы работой ради вёрстки — описание задачи читают ради
# содержания, а таблицы и вложенные списки в критериях приёмки не нужны.
# --------------------------------------------------------------------------
def text_to_adf(text: str) -> dict:
    """Плоский текст — в документ ADF: абзацы и строки списка."""
    content: list[dict] = []
    bullets: list[dict] = []

    def flush() -> None:
        if bullets:
            content.append({"type": "bulletList", "content": list(bullets)})
            bullets.clear()

    for block in (text or "").split("\n"):
        line = block.rstrip()
        if not line.strip():
            flush()
            continue
        if line.lstrip().startswith(("- ", "* ", "• ")):
            bullets.append(
                {
                    "type": "listItem",
                    "content": [_paragraph(line.lstrip()[2:].strip())],
                }
            )
            continue
        flush()
        content.append(_paragraph(line))
    flush()

    # Пустой `content` Jira не принимает: у документа обязан быть хотя бы один
    # узел. Пустое описание — обычное дело, поэтому отдаём пустой абзац.
    return {"type": "doc", "version": 1, "content": content or [_paragraph("")]}


def _paragraph(text: str) -> dict:
    node: dict[str, Any] = {"type": "paragraph"}
    if text:
        node["content"] = [{"type": "text", "text": text}]
    return node


# --------------------------------------------------------------------------
# Метаданные проекта
# --------------------------------------------------------------------------
def projects(settings: jira.Settings | None = None) -> list[dict]:
    """
    Проекты, доступные токену: ключ и название.

    Нужны интерфейсу: проект — единственное, что спрашивают у оператора, и
    выбирать его из списка вернее, чем вспоминать ключ по памяти. Cloud отдаёт
    страницу с `values`, Data Center — просто список.
    """
    s = settings or jira.load_settings()
    path = f"{s.api_path}/project/search" if _cloud(s) else f"{s.api_path}/project"
    data = _get("GET", path, s, params={"maxResults": 100} if _cloud(s) else None)
    rows = data.get("values") if isinstance(data, dict) else data
    found = []
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("key"):
            continue
        found.append({"key": str(row["key"]), "name": str(row.get("name") or row["key"])})
    return sorted(found, key=lambda item: item["key"])


def _createmeta_by_project(project: str, s: jira.Settings) -> list:
    """Типы отдельным эндпоинтом проекта: Cloud и Server / Data Center 9+."""
    data = _get("GET", f"{s.api_path}/issue/createmeta/{project}/issuetypes", s)
    return (data.get("values") if isinstance(data, dict) else data) or []


def _createmeta_legacy(project: str, s: jira.Settings) -> list:
    """Общий createmeta с фильтром по проекту: Server / Data Center до 9."""
    data = _get(
        "GET",
        f"{s.api_path}/issue/createmeta",
        s,
        params={"projectKeys": project, "expand": "projects.issuetypes"},
    )
    found = (data.get("projects") or [{}])[0] if isinstance(data, dict) else {}
    return found.get("issuetypes") or []


def _project_types(project: str, s: jira.Settings) -> list:
    """Типы из карточки проекта: последняя опора, отвечает на всех версиях."""
    data = _get("GET", f"{s.api_path}/project/{project}", s)
    return (data.get("issueTypes") if isinstance(data, dict) else None) or []


def _type_rows(project: str, s: jira.Settings) -> list[dict]:
    """Типы проекта как их отдал трекер: с именами и номерами."""
    for ask in (_createmeta_by_project, _createmeta_legacy, _project_types):
        try:
            rows = ask(project, s)
        except JiraError:
            continue
        if rows:
            return [row for row in rows if isinstance(row, dict) and row.get("name")]
    return []


def issue_types(project: str, settings: jira.Settings | None = None) -> list[str]:
    """
    Типы задач, которые проект принимает на создание.

    Спрашиваются у трекера, а не берутся из справочника: `Story` есть не в
    каждой схеме, а в русифицированном инстансе типы называются по-русски.
    Пустой список — метаданные недоступны; это не повод не заводить задачи,
    поэтому отказ здесь глушится, а типы уезжают как есть, и решает трекер.

    Спрашивается тремя способами подряд, и это не перестраховка. Jira Server 10
    убрала общий `createmeta` (404) и оставила эндпоинт проекта — тот самый,
    который до сих пор считался «облачным». Выбирать путь по `JIRA_API_PATH`
    нельзя: на живом Server с `/rest/api/2` работает как раз он. Пустой ответ
    здесь дороже отказа: без имён типов пачка уезжает с `Story` и `Task`,
    которых в русской схеме нет, и трекер отвечает 400 на каждую задачу.
    """
    return [str(row["name"]) for row in _type_rows(project, settings or jira.load_settings())]


# Как те же типы называются в русифицированной схеме. Это не догадка про
# конкретный инстанс: так их переводит сама Atlassian, и на русской Jira
# английских имён не остаётся вовсе. Без этой таблицы `Story` и `Task`
# сваливались в запасной вариант — первый тип списка, а первым в схеме
# бывает и `Epic`, и `Release`. Тихая подмена истории эпиком хуже отказа:
# отказ виден, а доска с двенадцатью эпиками выглядит как работа конвейера.
LOCALIZED: dict[str, tuple[str, ...]] = {
    jira_plan.EPIC: ("эпик", "эпика"),
    "Story": ("история", "пользовательская история"),
    "Task": ("задача",),
    jira_plan.SUBTASK: ("подзадача", "sub-task", "subtask"),
    "Bug": ("ошибка", "баг", "дефект"),
}


def _ordinary(available: list[str], lowered: dict[str, str]) -> str:
    """
    Обычный тип на самый крайний случай: не эпик и не подзадача.

    Крайний случай — это схема, в которой нет ни английских имён, ни русских:
    свой словарь типов у компании бывает. Ошибиться тут можно только в одну
    сторону, и дешевле промахнуться мимо `Задачи`, чем завести эпик.
    """
    epic = cfg.jira_epic_type().lower()
    for key, name in lowered.items():
        if key == epic or key in LOCALIZED[jira_plan.EPIC] or "sub" in key or "подзад" in key:
            continue
        return name
    return available[0]


def resolve_types(plan: jira_plan.Plan, available: list[str]) -> tuple[dict[str, str], list[str]]:
    """
    Сопоставить типы плана с типами проекта.

    Правил четыре, и все четыре — про то, что схема проекта чужая. `Epic` может
    называться `Эпик`; `Story` может не существовать вовсе, и тогда история
    заводится задачей; подзадача ищется по признаку, а не по имени, потому что
    называется она в каждой второй схеме по-своему; русские имена берутся из
    таблицы перевода, а не из порядка типов в схеме.
    """
    if not available:
        return {}, []

    lowered = {name.lower(): name for name in available}
    warnings: list[str] = []
    mapping: dict[str, str] = {}
    for kind in {item.type for item in plan.items}:
        if kind.lower() in lowered:
            mapping[kind] = lowered[kind.lower()]
            continue

        guess = ""
        if kind == jira_plan.EPIC:
            guess = lowered.get(cfg.jira_epic_type().lower(), "")
        elif kind == jira_plan.SUBTASK:
            guess = next(
                (name for key, name in lowered.items() if "sub" in key or "подзад" in key),
                "",
            )
        # Перевод точнее общего запасного типа: `JIRA_DEFAULT_ISSUE_TYPE` отвечает
        # за типы, которым соответствия нет, а у `Story` в русской схеме оно есть.
        if not guess:
            guess = next(
                (lowered[alias] for alias in LOCALIZED.get(kind, ()) if alias in lowered), ""
            )
        if not guess:
            guess = lowered.get(cfg.jira_default_issue_type().lower(), "") or _ordinary(
                available, lowered
            )
        mapping[kind] = guess
        warnings.append(f"типа {kind!r} в проекте нет, заводим как {guess!r}")
    return mapping, warnings


# --------------------------------------------------------------------------
# Кастомные поля эпика
#
# У эпика в Server / Data Center два поля, которых нет ни в одном справочнике
# типов: «Epic Name» — обязательное имя эпика, и «Epic Link» — связь ребёнка с
# ним. Оба кастомные, и номер у каждого инстанса свой: на одном 10103, на
# соседнем 11702. Без первого трекер отвечает 400 на КАЖДЫЙ эпик, без второго
# ребёнок уезжает с полем `parent`, которого у обычной задачи нет, и падает
# так же. Спрашивать номера у человека через .env — значит требовать, чтобы он
# сходил за ними в REST руками; справочник полей отдаёт их одним запросом.
#
# В Cloud этих полей нет: эпик там стал обычным родителем и связывается через
# `parent`. Поэтому справочник там не спрашивается вовсе.
# --------------------------------------------------------------------------
def _field_ids(s: jira.Settings) -> dict[str, str]:
    """Справочник «имя поля → идентификатор». Отказ здесь не отменяет заведение."""
    try:
        rows = _get("GET", f"{s.api_path}/field", s)
    except JiraError:
        return {}
    return {
        str(row["name"]).lower(): str(row["id"])
        for row in rows or []
        if isinstance(row, dict) and row.get("id") and row.get("name")
    }


# Поля, которые конвейер заполняет сам. Всё остальное, что экран создания
# требует обязательным и без значения по умолчанию, задачу отклонит — и
# узнать об этом до пачки дешевле, чем получить 400 на каждой карточке.
FILLED = frozenset({"project", "summary", "issuetype", "description", "labels", "parent"})


def _required(project: str, type_ids: tuple[str, ...], s: jira.Settings,
              metadata: dict | None = None) -> list[tuple[str, str]]:
    """
    Обязательные поля экрана создания, у которых нет значения по умолчанию.

    Спрашивается по одному разу на тип, а не на задачу: экран у типа один.
    Старые Data Center на этот эндпоинт отвечают 404 — тогда список пуст и
    поведение остаётся прежним, то есть решает сам трекер.
    """
    found: dict[str, str] = {}
    for type_id in type_ids:
        rows = metadata.get(type_id, {}) if metadata is not None else jira_fields.read(project, type_id, s)
        for field, row in rows.items():
            if not isinstance(row, dict) or not row.get("required") or row.get("hasDefaultValue"):
                continue
            if field:
                found.setdefault(field, str(row.get("name") or field))
    return sorted(found.items())


def _me(s: jira.Settings) -> dict[str, str]:
    """Текущая учётка значением поля «Автор»: Cloud узнаёт её по accountId."""
    try:
        me = _get("GET", f"{s.api_path}/myself", s)
    except JiraError:
        return {}
    if me.get("accountId"):
        return {"accountId": str(me["accountId"])}
    return {"name": str(me["name"])} if me.get("name") else {}


def _schema(s: jira.Settings, project: str = "", type_ids: tuple[str, ...] = ()) -> dict[str, Any]:
    """
    Что трекер потребует от каждой задачи — один опрос на пачку, а не на задачу.

    `JIRA_EPIC_LINK_FIELD` остаётся сильнее справочника: имя поля бывает
    переопределено в схеме, и тогда автоответ будет неверным, а настройка —
    верной.
    """
    found: dict[str, Any] = {"epic_link": "", "epic_name": "", "reporter": {}, "missing": []}
    if not _cloud(s):
        names = _field_ids(s)
        pick = lambda *aliases: next((names[a] for a in aliases if a in names), "")  # noqa: E731
        found["epic_link"] = cfg.jira_epic_link_field() or pick("epic link", "ссылка на эпик")
        found["epic_name"] = pick("epic name", "имя эпика", "название эпика")
    if not project or not type_ids:
        return found

    found["create_fields"] = {type_id: jira_fields.read(project, type_id, s) for type_id in type_ids}
    required = _required(project, type_ids, s, found["create_fields"])
    # Автор — единственное обязательное поле, которое конвейер может заполнить
    # сам: это тот, чьим токеном он ходит. На проектах, где у поля нет значения
    # по умолчанию, без него отклоняется КАЖДАЯ задача.
    if any(field == "reporter" for field, _ in required):
        found["reporter"] = _me(s)
    # Кастомные поля эпика заполняются по номерам из справочника, и в общий
    # список заполняемого их не впишешь: на соседнем инстансе номера другие.
    filled = FILLED | {found["epic_name"], found["epic_link"]}
    if found["reporter"]:
        filled = filled | {"reporter"}
    found["missing"] = [f"{name} ({field})" for field, name in required if field not in filled]
    return found


# --------------------------------------------------------------------------
# Заведение
# --------------------------------------------------------------------------
def create_issue(
    item: jira_plan.Item,
    project: str,
    *,
    settings: jira.Settings | None = None,
    parent_key: str = "",
    type_name: str = "",
    keys: dict[str, str] | None = None,
    source: str = "",
    schema: dict[str, Any] | None = None,
    extra_fields: dict | None = None,
    mapped: set[str] | None = None,
) -> dict:
    """Одна задача. Возвращает ключ и ссылку — то, за чем приходил оператор."""
    s = settings or jira.load_settings()
    schema = _schema(s) if schema is None else schema
    description = item.body(keys, source, mapped=mapped)
    fields: dict[str, Any] = {
        "project": {"key": project},
        "summary": item.summary,
        "issuetype": {"name": type_name or item.type},
        "description": text_to_adf(description) if _cloud(s) else description,
    }
    if item.labels:
        fields["labels"] = list(item.labels)
    for field, value in (extra_fields or {}).items():
        # Cloud multiline custom fields use the same document format as description.
        metadata = schema.get("field_metadata", {}).get(field, {})
        custom = (metadata.get("schema") or {}).get("custom", "")
        fields[field] = text_to_adf(value) if _cloud(s) and custom.endswith(":textarea") and isinstance(value, str) else value
    if schema.get("reporter"):
        fields["reporter"] = schema["reporter"]
    if item.type == jira_plan.EPIC and schema.get("epic_name"):
        # Имя эпика — не то же самое, что тема, но по умолчанию совпадает с ней:
        # человек переименует эпик на доске, а пустым это поле оставить нельзя.
        fields[schema["epic_name"]] = item.summary
    if parent_key:
        # Подзадача привязывается к родителю только так — и в Cloud, и в Data
        # Center. Эпик в Cloud стал обычным родителем и привязывается так же,
        # а в Data Center связь ведётся кастомным полем (см. `_schema`).
        epic_field = schema.get("epic_link", "")
        if epic_field and item.type != jira_plan.SUBTASK:
            fields[epic_field] = parent_key
        else:
            fields["parent"] = {"key": parent_key}

    data = _get("POST", f"{s.api_path}/issue", s, json={"fields": fields})
    key = str(data.get("key") or "")
    if not key:
        raise JiraError("трекер не вернул ключ созданной задачи")
    return {
        "local": item.local,
        "key": key,
        "url": jira.issue_url(key, s),
        "type": type_name or item.type,
        "summary": item.summary,
    }


def link(blocker: str, blocked: str, settings: jira.Settings | None = None) -> None:
    """
    Связь «блокирует» между двумя заведёнными задачами.

    Зависимости плана — половина его смысла: без них backlog это список, а не
    порядок работ. В описании они тоже остаются, но текст не показывается на
    доске и не участвует в фильтрах.
    """
    s = settings or jira.load_settings()
    _get(
        "POST",
        f"{s.api_path}/issueLink",
        s,
        json={
            "type": {"name": "Blocks"},
            "inwardIssue": {"key": blocked},
            "outwardIssue": {"key": blocker},
        },
    )


def create_issues(
    plan: jira_plan.Plan,
    project: str,
    *,
    settings: jira.Settings | None = None,
    source: str = "",
) -> dict:
    """
    Завести пачку и вернуть, что из неё получилось.

    Порядок берётся из плана: эпики первыми, потому что ключ родителя нужен
    настоящий. Отказ на одной карточке не отменяет остальные — он записывается
    в `failed` с причиной. Ребёнок, чей родитель не завёлся, всё равно
    заводится, но уже без родителя: задача без эпика полезнее отсутствующей
    задачи, а расхождение видно в предупреждениях.
    """
    s = settings or jira.load_settings()
    project = (project or "").strip().upper()
    if not project:
        raise JiraError("не указан проект: некуда заводить задачи")

    rows = _type_rows(project, s)
    types, warnings = resolve_types(plan, [str(row["name"]) for row in rows])
    numbers = {str(row["name"]): str(row["id"]) for row in rows if row.get("id")}
    used = {numbers.get(types.get(item.type, item.type), "") for item in plan.items}
    schema = _schema(s, project, tuple(sorted(number for number in used if number)))
    created: list[dict] = []
    failed: list[dict] = []
    keys: dict[str, str] = {}

    for item in plan.items:
        metadata = schema.get("create_fields", {}).get(numbers.get(types.get(item.type, item.type), ""), {})
        extra_fields, mapped, field_warnings = jira_fields.map_item(item, metadata)
        warnings.extend(field_warnings)
        filled = FILLED | set(extra_fields) | {schema.get("epic_name"), schema.get("epic_link")}
        if schema.get("reporter"):
            filled = filled | {"reporter"}
        missing = [f"{row.get('name', key)} ({key})" for key, row in metadata.items()
                   if row.get("required") and not row.get("hasDefaultValue") and key not in filled]
        if missing:
            warnings.append(f"{item.local}: экран создания требует незаполненные поля: " + ", ".join(missing))
        parent_key = keys.get(item.parent, "")
        if item.parent and not parent_key:
            warnings.append(f"{item.local}: родитель {item.parent} не заведён, задача без родителя")
        try:
            result = create_issue(
                item,
                project,
                settings=s,
                parent_key=parent_key,
                type_name=types.get(item.type, item.type),
                keys=keys,
                source=source,
                schema={**schema, "field_metadata": metadata},
                extra_fields=extra_fields,
                mapped=mapped,
            )
        except JiraError as exc:
            failed.append({"local": item.local, "summary": item.summary, "reason": str(exc)})
            continue
        keys[item.local] = result["key"]
        created.append(result)

    warnings += _link_dependencies(plan, keys, s)
    return {
        "status": _status(created, failed),
        "project": project,
        "created": created,
        "failed": failed,
        "warnings": warnings + plan.warnings,
    }


def _link_dependencies(plan: jira_plan.Plan, keys: dict[str, str], s: jira.Settings) -> list[str]:
    """
    Связать заведённое по зависимостям плана.

    Связь — отдельный запрос на каждую пару, и падает она чаще создания: имя
    типа связи зависит от схемы, а прав на связывание может не быть. Поэтому
    её отказ не портит результат и оседает предупреждением: задачи заведены,
    а порядок работ остался в описании и в документе.
    """
    warnings: list[str] = []
    for item in plan.items:
        blocked = keys.get(item.local)
        if not blocked:
            continue
        for local in item.depends_on:
            blocker = keys.get(local)
            if not blocker:
                continue
            try:
                link(blocker, blocked, s)
            except JiraError as exc:
                warnings.append(f"{blocker} → {blocked}: связь не создана ({exc})")
    return warnings


def _status(created: list[dict], failed: list[dict]) -> str:
    if failed and created:
        return "partial"
    if failed:
        return "failed"
    return "created"


def format_created(result: dict) -> str:
    """
    Что заведено — оператору в тред: ключи и ссылки, ради которых всё и затевалось.
    """
    lines = []
    for issue in result.get("created") or []:
        lines.append(f"- {issue['key']} — {issue['summary']} ({issue['type']}) {issue['url']}")
    for issue in result.get("failed") or []:
        lines.append(f"- [{issue['local']}] не заведена: {issue['reason']}")
    for warning in result.get("warnings") or []:
        lines.append(f"- внимание: {warning}")
    return "\n".join(lines)
