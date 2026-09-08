"""Роли конвейера: готовый пакет документов -> трассировка, расхождения, вердикт."""

from __future__ import annotations

from agent import audit_prompts
from agent.pipeline import Pipeline, Role, Stage

# Два ключа в state["artifacts"], которые пишет не роль, а нода чтения пакета
# (`audit_graph.package_node`). Лежат там же, где документы этапов, потому что
# подставляются ролям тем же `brief()`; в страницы публикации они не попадают —
# `Pipeline.done()` перебирает роли, а не ключи.
PACKAGE = "package"
CHECKS = "checks"

ROLES: tuple[Role, ...] = (
    Role(
        key="trace",
        number="01",
        title="Трассировка",
        summary="Что из требований во что превратилось и где связи нет",
        needs=(PACKAGE, CHECKS),
    ),
    Role(
        key="conflicts",
        number="02",
        title="Расхождения",
        summary="Что два документа пакета говорят по-разному об одном и том же",
        needs=(PACKAGE, CHECKS, "trace"),
    ),
    Role(
        key="verdict",
        number="03",
        title="Заключение по пакету",
        summary="Вердикт, что чинить до работы, что в процессе и что не проверялось",
        # Пакета здесь намеренно нет, хотя соблазн его дать велик. Сообщение
        # роли собирается заново на каждом этапе (`nodes.make_role_node`), и
        # пакет — самая большая его часть; заплатить за него третий раз значит
        # заплатить за то, что уже прочитано и разложено на находки с местами
        # в документах. Заключение пишется по находкам, а не по источнику:
        # если находка не позволяет принять решение, дефект в находке.
        needs=(CHECKS, "trace", "conflicts"),
    ),
)

BY_KEY = {role.key: role for role in ROLES}
KEYS = tuple(role.key for role in ROLES)
FIRST = ROLES[0]
LAST = ROLES[-1]

PACKAGE_TITLE = "Пакет документов"
CHECKS_TITLE = "Автоматическая сверка"


def prompt_for(key: str) -> str:
    return audit_prompts.for_role(key)


def brief(role: Role, task: str, artifacts: dict | None) -> str:
    """Запрос оператора, пакет, результат сверки кодом и документы этапов."""
    artifacts = artifacts or {}
    parts = [f"# Запрос оператора\n\n{task.strip()}"]

    for key in role.needs:
        if key == PACKAGE:
            found = (artifacts.get(PACKAGE) or "").strip()
            parts.append(
                f"# {PACKAGE_TITLE}\n\n{found}"
                if found
                else f"# {PACKAGE_TITLE}\n\nПакет не прочитан. Не проверяй по памяти "
                "и не описывай, каким он бывает: скажи, что проверять нечего."
            )
            continue
        if key == CHECKS:
            found = (artifacts.get(CHECKS) or "").strip()
            parts.append(
                f"# {CHECKS_TITLE}\n\n{found}"
                if found
                else f"# {CHECKS_TITLE}\n\nСверка кодом не выполнена. Считай, что "
                "формальные проверки не проводились, и скажи это в документе."
            )
            continue
        source = BY_KEY[key]
        text = (artifacts.get(key) or "").strip()
        if text:
            parts.append(f"# Результат этапа {source.number}. {source.title}\n\n{text}")
        else:
            # Этап мог не состояться: кончился бюджет, оператор остановил
            # конвейер. Молча отдать роли пустоту нельзя — вердикт «расхождений
            # нет» по невыполненному этапу выглядит точно так же, как вердикт
            # по выполненному.
            parts.append(
                f"# Результат этапа {source.number}. {source.title}\n\n"
                "Этап не выполнен, результата нет. Не выдавай его отсутствие за "
                "отсутствие находок: скажи прямо, что этот срез не проверялся."
            )
    return "\n\n".join(parts)


PIPELINE = Pipeline(
    key="audit",
    title="Сверка пакета",
    summary="Готовый комплект документов проверяется на трассируемость и расхождения.",
    byline="конвейером сверки пакета",
    roles=ROLES,
    prompt_for=prompt_for,
    brief=brief,
    # Сверять нечего — прогон не начинается: этот конвейер проверяет готовые
    # документы, а не пишет их.
    admission=True,
    # Пакет читает и проверяет код: имена файлов известны заранее, а формальные
    # дефекты — битый JSON, ссылка на несуществующее требование — находятся
    # арифметикой, одинаково на каждом прогоне и бесплатно.
    prelude=Stage(
        key="package",
        title="Чтение и сверка пакета",
        summary="Без вызова модели читает документы и считает трассировку и находки.",
    ),
    # Три документа сверки — один отчёт, и читают его подряд: вердикт без
    # находок, на которые он ссылается, не проверяется.
    one_page=True,
)
