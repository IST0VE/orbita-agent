"""
Черновики: то, что появится в чужой системе, показанное до того, как появится.

Остановка перед публикацией и перед заведением задач была и раньше, но
показывала она сводку: склеенный документ треда и список заголовков, строку
«эпиков 3, задач 12». По такому решение принимается вслепую — оператор видит,
что конвейер что-то собрал, и не видит, что именно уедет на страницу и в
карточку. А уезжает не документ этапа: страница проходит через рендерер цели,
описание задачи собирается из полей плана, тип карточки подменяется на тот,
который есть в схеме проекта. Разница между прочитанным и отправленным —
ровно то место, где потом обнаруживают, что заведено не то.

Поэтому остановка отдаёт наружу черновики: по одному объекту на каждый
будущий объект, с готовым телом и с теми же полями, которые уйдут в запрос.
Открыть, прочитать, отклонить — до сети, а не после.

Черновик — это словарь, и его форма здесь общая для страниц и задач: интерфейс
рисует их одним виджетом (`web/src/engine/widgets/builtins`), а различает по
`kind`.

    id        локальный ключ: ключ роли у страницы, ключ карточки у задачи
    kind      "page" | "issue"
    title     заголовок страницы или summary задачи
    action    "create" — объекта ещё нет; "update" — существующий перезапишут;
              "unknown" — спросить не удалось; "none" — цель ничего не сделает
    where     куда: цель публикации или ключ проекта Jira
    format    чем рисовать тело: "markdown", "storage" или "text"
    document  тело как оно уедет, а не документ этапа
    chars     длина тела: страница на сорок килобайт видна по числу
    url       ссылка на существующий объект, если он уже есть
    note      пояснение к `action`, когда оно нужно
    fields    поля запроса парами «подпись — значение»

Сеть здесь трогается только на чтение и только терпимо к отказу: узнать, что
страница уже существует, а тип `Story` в проекте называется «Задача», — это
польза для решения, но не условие его принять. Всякий отказ превращается в
`note`, а не в исключение: остановка обязана открыться даже тогда, когда
трекер лежит.
"""

from __future__ import annotations

from agent import jira_plan, jira_writer, outgoing, publishers


def page(
    *,
    role: str,
    title: str,
    document: str,
    fmt: str,
    where: str,
    preview: dict,
    diff: dict | None = None,
) -> dict:
    """
    Черновик страницы: тело после рендерера цели плюс судьба заголовка.

    Для обновления рядом идут различия с тем, что лежит сейчас. «Перезапишет
    существующую» без них — предупреждение без содержания: перезапись бывает
    уточнением абзаца и бывает потерей чужой работы, и решают их по-разному.
    """
    title = outgoing.sanitize(title, "title")
    document = outgoing.sanitize(document, "document")
    return {
        "diff": diff or {"available": False, "reason": "документ создаётся заново"},
        "id": role or "document",
        "kind": "page",
        "title": title,
        "action": str(preview.get("action") or "unknown"),
        "where": where,
        "format": fmt,
        "document": document,
        "chars": len(document),
        "url": str(preview.get("url") or ""),
        "note": str(preview.get("reason") or ""),
        "fields": [
            {"label": "Заголовок", "value": title},
            *(
                [{"label": "Версия", "value": f"{preview['version']} → {preview['version'] + 1}"}]
                if isinstance(preview.get("version"), int)
                else []
            ),
            *([{"label": "Файл", "value": str(preview["path"])}] if preview.get("path") else []),
        ],
    }


def pages(plan: dict, previews: list[dict] | None = None) -> list[dict]:
    """
    Черновики страниц по плану публикации из `graph.publish_plan`.

    Судьба заголовка спрашивается у цели по одной странице: их столько,
    сколько состоялось этапов, — пять запросов в худшем случае, и делаются
    они один раз за прогон, на остановке, а не на каждом ходе.

    Готовые ответы цели можно передать: тот же опрос нужен одобряемому набору
    (`nodes.publish_commitment`), и спрашивать wiki дважды за одну остановку —
    это те же пять запросов ещё раз и риск показать одно, а зафиксировать другое.
    """
    publisher = plan["publisher"]
    items = plan["pages"]
    if previews is None:
        previews = [publisher.preview(item["title"]) for item in items]
    return [
        page(
            role=item.get("role", ""),
            title=item["title"],
            document=item["document"],
            fmt=publisher.renderer.name,
            where=publisher.name,
            preview=preview,
            diff=publishers.diff_of(
                publishers.current_text(publisher, item["title"], preview),
                outgoing.sanitize(item["document"], "document"),
            ) if preview.get("action") == "update" else None,
        )
        for item, preview in zip(items, previews, strict=True)
    ]


def issues(plan: jira_plan.Plan, project: str, *, source: str = "") -> tuple[list[dict], list[str]]:
    """
    Черновики задач: карточка за карточкой в том порядке, в каком заведутся.

    Описание собирается тем же `Item.body`, что и при заведении, и с тем же
    пустым `keys`: зависимости на этот момент — ещё локальные ключи, потому
    что настоящих не существует. Тип берётся из схемы проекта, если её удалось
    спросить: подмена `Story` на `Task` должна быть видна до заведения, а не в
    предупреждениях после.
    """
    types: dict[str, str] = {}
    warnings: list[str] = []
    if project:
        try:
            types, warnings = jira_writer.resolve_types(
                plan, jira_writer.issue_types(project)
            )
        except jira_writer.JiraError as exc:
            # Трекер недоступен или не настроен. План от этого не портится:
            # типы уедут как есть, и решать будет сама Jira.
            warnings = [f"типы проекта не прочитаны ({exc})"]

    drafts = []
    for item in plan.items:
        kind = types.get(item.type, item.type)
        # Ровно то, что уедет: та же проверка исходящего текста, что и в
        # `jira_writer.create_issue`. Показывать оператору непроверенное тело,
        # а отправлять проверенное — значит спрашивать согласие не на то.
        body = outgoing.sanitize(item.body(source=source), "description")
        drafts.append(
            {
                "id": item.local,
                "kind": "issue",
                "title": outgoing.sanitize(item.summary, "summary"),
                "action": "create",
                "where": project or "проект не выбран",
                "format": "text",
                "document": body,
                "chars": len(body),
                "url": "",
                "note": f"тип плана {item.type} заводится как {kind}"
                if kind != item.type
                else "",
                "fields": _issue_fields(item, kind),
            }
        )
    return drafts, warnings


def _issue_fields(item: jira_plan.Item, kind: str) -> list[dict]:
    """Поля карточки для окна: то же, что уедет в запросе, теми же словами."""
    pairs = [("Тип", kind), ("Родитель", item.parent), ("Оценка", item.estimate)]
    where = " / ".join(part for part in (item.service, item.component, item.layer) if part)
    pairs.append(("Область", where))
    pairs.append(("Метки", ", ".join(item.labels)))
    pairs.append(("Зависит от", ", ".join(item.depends_on)))
    return [
        {"label": label, "value": outgoing.sanitize(value, label)}
        for label, value in pairs
        if value
    ]
