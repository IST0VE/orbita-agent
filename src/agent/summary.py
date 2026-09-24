"""
Итог хода: что получилось, что мешает и что делать дальше.

Три вопроса, ради которых оператор открывает экран. До этого их приходилось
собирать глазами по четырём блокам JSON: публикация отдельно, остановка
конвейера отдельно, заведённые задачи отдельно, стоимость отдельно. Каждый
блок честно отвечал на свой вопрос и ни один — на главный.

Считается здесь, а не в интерфейсе, по двум причинам. Правило одно на все
интерфейсы: Studio, собственный портал и консоль показывают один и тот же
итог, а не три похожих. И данные уже здесь: браузеру пришлось бы получить
всё состояние целиком и пересчитывать его на каждом кадре — вместе с историей
сообщений, которая к итогу отношения не имеет.

Функция чистая: ничего не читает и ничего не пишет.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.pipeline import Pipeline

#: Статусы публикации, которые означают «что-то пошло не так».
TROUBLE = {"failed", "partial", "skipped", "rejected", "stale"}


def summary_of(state: dict, pipeline: Pipeline | None = None) -> dict:
    """
    Итог, проблемы и следующее действие — по состоянию треда.

    pipeline — конвейер, чьи этапы считаются. Без него итог знает только
    выпущенные документы: этап, не выпустивший документа, в словаре не
    оставляет ничего, и «вышел один из пяти» выглядел бы как «вышел один».
    Так 23 сентября 2026 конвейер опубликовал один документ из пяти, и ни одна
    строка итога об этом не сказала.
    """
    artifacts = state.get("artifacts") or {}
    publication = state.get("publication") or {}
    halt = state.get("halt") or {}
    issues = state.get("issues") or {}
    cost = state.get("cost") or {}

    outcome: list[str] = []
    problems: list[str] = []
    next_steps: list[str] = []

    # При отказе по входу этапы не пропали, их не запускали: причина уже стоит
    # в треде, и перечень всех этапов подряд к ней ничего не добавит.
    missing = pipeline.pending(artifacts) if pipeline and not state.get("refused") else []

    if artifacts and pipeline:
        outcome.append(
            f"Документов этапов: {len(pipeline.done(artifacts))} из {len(pipeline.roles)}"
        )
    elif artifacts:
        outcome.append(f"Документов этапов: {len(artifacts)}")
    if state.get("execution_status"):
        outcome.append(f"Тест: {state['execution_status']}")
    if state.get("analysis_result"):
        outcome.append(f"SLA: {state['analysis_result']}")
    if publication.get("status"):
        pages = publication.get("pages") or []
        outcome.append(
            f"Публикация: {publication['status']}"
            + (f", страниц {len(pages)}" if pages else "")
        )
    if issues.get("status"):
        outcome.append(f"Jira: {issues['status']}" + (
            f", заведено {len(issues.get('created') or [])}" if issues.get("created") else ""
        ))

    if halt.get("stage"):
        problems.append(
            f"Конвейер остановлен на этапе {halt['stage']}: "
            + (halt.get("reason") or "без причины")
        )
        next_steps.append("Исправьте материалы или подтвердите этап заново.")

    # Та же строка, что в сообщении об остановке (`routes.halted_node`), но и
    # у дошедшего до конца прогона: этап, чья модель не выпустила документа,
    # иначе пропадал бы без следа — ворота пустой документ пропускают, а
    # публикация уходит страницами только тех этапов, что состоялись.
    if missing:
        problems.append("Не выполнены этапы: " + ", ".join(role.title for role in missing))
        if not halt.get("stage"):
            next_steps.append("Повторите ход в этом треде: этапы без документа будут написаны заново.")

    status = publication.get("status")
    if status in TROUBLE:
        problems.append("Публикация: " + (publication.get("reason") or status))
        if status == "stale":
            next_steps.append("Повторите ход и подтвердите публикацию по новому плану.")
        elif status == "skipped":
            next_steps.append("Заполните настройки цели публикации.")
        elif status == "rejected":
            next_steps.append("Документы остались в результатах хода.")
        else:
            next_steps.append("Проверьте перечисленные страницы и повторите публикацию.")

    unresolved = issues.get("unresolved") or []
    if unresolved:
        problems.append(f"Задач с неизвестным результатом: {len(unresolved)}")
        next_steps.append("Найдите их в трекере по метке операции и разрешите вручную.")
    if issues.get("failed"):
        problems.append(f"Задач не заведено: {len(issues['failed'])}")

    if cost.get("unpriced_calls"):
        problems.append(f"Вызовов по неизвестному тарифу: {cost['unpriced_calls']}")
        next_steps.append("Задайте тариф модели: иначе денежный лимит не проверяется.")

    if state.get("error"):
        problems.append(str(state["error"]))

    if not problems and not next_steps:
        next_steps.append(
            "Проверьте документы этапов и опубликованные страницы."
            if artifacts else "Результата ещё нет."
        )
    return {
        "outcome": outcome,
        "problems": problems,
        "next": list(dict.fromkeys(next_steps)),
    }
