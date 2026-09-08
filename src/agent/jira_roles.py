"""Роли конвейера: документ аналитики -> проверенный backlog -> задачи в Jira."""

from __future__ import annotations

from agent import jira_prompts
from agent.pipeline import Pipeline, Role, Stage

# Ключ в state["artifacts"], который пишет не роль, а нода чтения источника
# (`jira_graph.source_node`). Лежит там же, где документы этапов, потому что
# подставляется ровно так же — и потому что `Pipeline.done()` перебирает роли,
# а не ключи, и в страницу публикации этот ключ не попадает.
SOURCE = "source"

ROLES: tuple[Role, ...] = (
    Role(
        key="scope",
        number="01",
        title="Карта реализации",
        summary="Границы, сервисы, компоненты, слои и сквозные зависимости",
        # Инструментов у карты больше нет. Раньше она сама искала аналитику по
        # папке задачи, потому что вход конвейера был «где-то тут документ».
        # Теперь документ читает код — названный файл или страницу Confluence
        # по ссылке, — и он один и тот же для всех этапов. Материал ровно один,
        # он нужен целиком, и читать его моделью значит платить за вызов,
        # который ничего не выбирает; тот же довод записан в `diagram_roles`.
        needs=(SOURCE,),
    ),
    Role(
        key="backlog",
        number="02",
        title="Jira-декомпозиция",
        summary="Epic, задачи, критерии приёмки, зависимости и покрытие",
        needs=(SOURCE, "scope"),
    ),
    Role(
        key="review",
        number="03",
        title="Ревью и финальный Jira backlog",
        summary="Проверенная и исправленная декомпозиция для refinement",
        needs=(SOURCE, "scope", "backlog"),
    ),
    Role(
        key="issues",
        number="04",
        title="Карточки для трекера",
        summary="Финальный backlog в машинной форме: то, что уедет в Jira",
        # Единственная роль, которой не показывают ни карту, ни черновик, и это
        # не экономия. Её работа — перенести ФИНАЛЬНЫЙ backlog в JSON без
        # потерь и без добавлений; черновик рядом с финалом даёт ровно одну
        # ошибку — карточку по задаче, которую ревью удалило, и заведётся она
        # уже в чужом проекте. Источник ей тоже не нужен: финальный документ
        # обязан быть самодостаточным, этого от него требует его же промпт.
        needs=("review",),
    ),
)

BY_KEY = {role.key: role for role in ROLES}
KEYS = tuple(role.key for role in ROLES)
FIRST = ROLES[0]
LAST = ROLES[-1]


def prompt_for(key: str) -> str:
    return jira_prompts.for_role(key)


def brief(role: Role, task: str, artifacts: dict | None) -> str:
    """Запрос оператора, прочитанный источник и неизменённые результаты этапов."""
    artifacts = artifacts or {}
    parts = [f"# Запрос оператора\n\n{task.strip()}"]
    for key in role.needs:
        # Источник пишет не роль, а нода чтения, поэтому ни номера этапа, ни
        # заголовка у неё нет. Стоит он перед документами этапов: внутри треда
        # документ неизменен, а результаты копятся от этапа к этапу —
        # стабильное ближе к началу, растущее в хвост.
        if key == SOURCE:
            found = (artifacts.get(SOURCE) or "").strip()
            parts.append(
                f"# Исходная аналитика\n\n{found}"
                if found
                else "# Исходная аналитика\n\nОтдельного документа нет: работай с тем, "
                "что написано в запросе оператора выше, и не выдавай за аналитику "
                "то, чего в нём нет."
            )
            continue
        source = BY_KEY[key]
        text = (artifacts.get(key) or "").strip()
        if text:
            parts.append(f"# Результат этапа {source.number}. {source.title}\n\n{text}")
        else:
            parts.append(
                f"# Результат этапа {source.number}. {source.title}\n\n"
                "Этап не выполнен. Не маскируй отсутствие данных: перечисли, "
                "что невозможно подтвердить, и пометь связанные поля TBD."
            )
    return "\n\n".join(parts)


PIPELINE = Pipeline(
    key="jira",
    title="Jira-декомпозиция",
    summary="Документ аналитики превращается в заведённые задачи Jira.",
    byline="конвейером Jira-декомпозиции",
    roles=ROLES,
    prompt_for=prompt_for,
    brief=brief,
    # Раскладывать нечего — прогон не начинается: этот конвейер разбирает
    # готовую аналитику, а не пишет её.
    admission=True,
    prelude=Stage(
        key="source",
        title="Чтение источника",
        summary="Без вызова модели читает страницу Confluence по ссылке или файлы задачи.",
    ),
    # Единственный конвейер, который заканчивается не документом: по нему
    # заводятся задачи в чужой системе.
    postlude=Stage(
        key="create",
        title="Заведение задач",
        summary="Заводит задачи в выбранном проекте Jira и возвращает ключи и ссылки.",
    ),
)
