"""
Экраны создания Jira с заполненными полями — без единой заведённой задачи.

Черновиков у Jira нет: объект в трекере либо есть, либо его нет, и промежуточного
состояния, как у неопубликованной страницы Confluence, не существует. Ближайшее,
что есть, — форма создания с уже заполненными полями: ссылка открывает экран,
поля в нём стоят, «Создать» нажимает человек. Ни один запрос этого модуля ничего
не создаёт: только GET за метаданными.

Заполнять форму по ссылке умеет Server / Data Center — `CreateIssueDetails!init`.
Cloud этот экран убрал; там пробуется Embed Anywhere, чей контракт не обещан.

Отдельно важен третий случай: метаданные не прочитались совсем — отозван токен,
нет прав на проект, лежит сеть. Тогда ссылок не будет ни на какой версии, и
сказать об этом надо прямо. Карточка со ссылкой на проект выглядит ровно так же,
как подготовленная форма, и молчаливый откат к ней читается оператором как
«Orbita не умеет», хотя Jira просто ответила 401.
"""

from __future__ import annotations

import json
import re
from urllib.parse import quote, urlencode

from agent import jira, jira_fields, jira_plan, jira_writer

# Потолок на длину ссылки. Дальше начинается территория, где обрезает то прокси,
# то сам браузер, и обрезает молча — уехавшее наполовину описание хуже, чем
# честно не подставленное.
MAX_URL = 4000


def _capabilities(project: str, s: jira.Settings) -> tuple[str, dict]:
    """
    Чем инстанс заполняет форму и что он знает о проекте.

    Оба запроса — на чтение. Числовой идентификатор проекта и типа задачи
    берутся отсюда: по ключу проекта форму заполнить нельзя, `pid` у экрана
    создания только числовой.
    """
    info = jira.call("GET", f"{s.api_path}/serverInfo", s)
    mode = "cloud" if info.get("deploymentType") == "Cloud" else "legacy"
    return mode, jira.call("GET", f"{s.api_path}/project/{quote(project, safe='')}", s)


def _reporter(s: jira.Settings) -> str:
    """
    Логин для поля «Автор» — тот, чьим токеном ходит конвейер.

    На проектах, где у поля нет значения по умолчанию, форма открывается с
    красным «Автор обязательно» и ждёт, пока его выберут руками. Ссылку
    открывает тот же человек, чей токен в .env, так что подставить его —
    не догадка. Cloud опознаёт учётку по accountId и здесь пропускается.
    """
    try:
        me = jira.call("GET", f"{s.api_path}/myself", s)
    except jira.JiraError:
        return ""
    return str(me.get("name") or "")


def _link(
    item: jira_plan.Item,
    body: str,
    type_id: str,
    pid: str,
    mode: str,
    s: jira.Settings,
    reporter: str = "",
    fields: dict | None = None,
) -> tuple[str, str]:
    """Ссылка на заполненную форму и пояснение к ней — или пустая пара."""
    if mode == "cloud":
        path = "/jira/issues/create/embed"
        params = {"projectId": pid, "issueTypeId": type_id, "summary": item.summary}
        field, value = (
            "descriptionAdf",
            json.dumps(jira_writer.text_to_adf(body), ensure_ascii=False),
        )
    else:
        path = "/secure/CreateIssueDetails!init.jspa"
        params = {
            "pid": pid,
            "issuetype": type_id,
            "summary": item.summary,
            "labels": list(item.labels),
        }
        if reporter:
            params["reporter"] = reporter
        params.update(jira_fields.form_values(fields or {}))
        field, value = "description", body

    short = s.base_url + path + "?" + urlencode(params, doseq=True)
    full = short + "&" + urlencode({field: value})
    if len(full) <= MAX_URL:
        return full, "Проверьте поля в Jira и нажмите «Создать», когда закончите правки."
    if len(short) <= MAX_URL:
        return short, (
            "Описание слишком длинное для ссылки: форма откроется с заголовком и типом, "
            "описание вставьте кнопкой копирования."
        )
    return "", "Заголовок слишком длинный для ссылки. Перенесите поля кнопками копирования."


def prepare(
    plan: jira_plan.Plan, project: str, *, source: str = "", settings: jira.Settings | None = None
) -> dict:
    s = settings or jira.load_settings()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,39}", project):
        raise jira.JiraError("некорректный ключ проекта Jira")

    warnings = list(plan.warnings)
    mode, metadata, failure = "manual", {}, ""
    try:
        mode, metadata = _capabilities(project, s)
    except jira.JiraError as exc:
        failure = str(exc)
        warnings.append(
            f"Jira не ответила на запрос метаданных: {exc}. Формы не заполнены: без "
            "числовых идентификаторов проекта и типа задачи подставить поля в ссылку "
            "нечем. Проверьте JIRA_TOKEN и права токена на проект — "
            "`python scripts/smoke_check.py --skip-llm` отвечает на это одним запросом."
        )
    if mode == "cloud":
        warnings.append(
            "Форма Jira Cloud использует новый редактор, и её адрес Atlassian не обещает. "
            "Если поле не подставилось, перенесите его кнопкой копирования."
        )

    types = {
        str(t["name"]): str(t["id"])
        for t in metadata.get("issueTypes", [])
        if isinstance(t, dict) and t.get("name") and t.get("id")
    }
    resolved, type_warnings = jira_writer.resolve_types(plan, list(types))
    warnings.extend(type_warnings)
    if any(item.parent or item.depends_on for item in plan.items):
        warnings.append(
            "Сначала создайте родителей. Родителей и зависимости укажите в Jira "
            "по полученным ключам; Orbita не отслеживает ручное создание задач."
        )

    pid = str(metadata.get("id") or "")
    reporter = _reporter(s) if mode == "legacy" and pid else ""
    filled = 0
    cards = []
    create_fields = {}
    for item in plan.items:
        body = item.body(source=source)
        kind = resolved.get(item.type, item.type)
        extra_fields, mapped = {}, set()
        if mode == "legacy" and pid and types.get(kind):
            if kind not in create_fields:
                create_fields[kind] = jira_fields.read(project, types[kind], s)
            extra_fields, mapped, field_warnings = jira_fields.map_item(item, create_fields[kind])
            warnings.extend(field_warnings)
        url = ""
        note = ""
        # Подзадаче нужен настоящий родитель: заполненная форма создала бы её
        # задачей, и подмену никто бы не заметил.
        if item.type == jira_plan.SUBTASK:
            note = (
                f"Сначала создайте родителя {item.parent}; откройте его в Jira "
                "и выберите создание подзадачи."
            )
        elif pid and types.get(kind):
            url, note = _link(item, item.body(source=source, mapped=mapped), types[kind], pid, mode, s, reporter, extra_fields)
            filled += bool(url)
        elif failure:
            note = f"Форма не заполнена: {failure}."
        else:
            note = f"В схеме проекта не найден тип {kind!r}; заполнить форму по ссылке нечем."
        cards.append(
            {
                "id": item.local,
                "kind": "issue",
                "title": item.summary,
                "action": "form",
                "where": project,
                "format": "text",
                "document": body,
                "chars": len(body),
                # Без заполненной формы ссылка ведёт в проект: открыть «Создать»
                # руками всё равно придётся, и начинать это с поиска проекта в
                # трекере — лишний шаг.
                "url": url or f"{s.base_url}/browse/{quote(project, safe='')}",
                "note": note,
                "fields": [
                    {"label": "Тип", "value": kind},
                    {"label": "Родитель", "value": item.parent},
                    {"label": "Метки", "value": ", ".join(item.labels)},
                    {"label": "Компонент", "value": item.component},
                    {"label": "Оценка", "value": item.estimate},
                    {"label": "Сервис", "value": item.service},
                    {"label": "Слой", "value": item.layer},
                    {"label": "Критерии приёмки", "value": "\n".join(item.acceptance)},
                ],
            }
        )

    return {
        "status": "forms",
        "project": project,
        "drafts": cards,
        "created": [],
        "failed": [],
        "warnings": warnings,
        "reason": _reason(filled, len(cards), failure),
    }


def _reason(filled: int, total: int, failure: str) -> str:
    """Итог одной строкой: сколько форм заполнено и почему не все."""
    if filled == total:
        return "Подготовлены формы для ручного создания. Задачи в Jira ещё не созданы."
    if not filled:
        return (
            f"Формы не заполнены ({failure}). Ниже заголовки и описания для ручного "
            "переноса; задачи в Jira не созданы."
            if failure
            else "Формы не заполнены: заполнить поля по ссылке не удалось. Ниже заголовки "
            "и описания для ручного переноса; задачи в Jira не созданы."
        )
    return (
        f"Заполнено форм: {filled} из {total}; остальные перенесите кнопками копирования. "
        "Задачи в Jira не созданы."
    )
