"""
Пятый конвейер: готовый пакет документов -> трассировка, расхождения, вердикт.

Остальные четыре графа пишут документы. Этот их проверяет — и первым делом
проверяет тем, что не стоит денег: битый пример JSON, ссылка на требование,
которого нет ни в одном документе, маршрут, о котором знает архитектура и не
знает контракт API, находятся арифметикой (`checks.py`), одинаково на каждом
прогоне и до первого вызова модели. Модель получает результат этой сверки
разделом сообщения и занимается тем, чего арифметика не умеет: смыслом.

Граф:

  START -> context -> package -> trace -> gate_conflicts -> conflicts
                                                         -> gate_verdict -> verdict
        -> remember -> approve -> publish -> END

  START -> no_input -> remember      (сверять нечего)

Что делает каждая нода:

  package    читает документы и считает по ним трассировку и находки. Без
             модели и без денег: имена файлов известны заранее, а формальные
             дефекты не требуют мнения. Результат кладётся двумя ключами —
             сам пакет и отчёт сверки, — потому что роли нужны оба и по
             отдельности: пакет читают, отчёт цитируют;
  <роль>     вызов модели со стабильным префиксом (`audit_prompts.py`);
  gate_*     ворота перед этапом: бюджет и подтверждение оператором;
  no_input   честный ответ вместо прогона: проверять нечего;
  publish    один документ на прогон (`one_page`), а не три страницы.

Вход у конвейера строгий: документы. Пересказ пакета в сообщении оператора
входом не считается — сверять его не с чем, а отчёт по пересказу выглядит
ровно так же, как отчёт по документам. Этим он отличается от конвейера
декомпозиции, которому вставленная в сообщение аналитика — законный вход:
там документ раскладывают, здесь его проверяют против других документов.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph

from agent import audit_roles, checks, confluence, inputs, sources
from agent import graph as common_graph

PIPELINE = audit_roles.PIPELINE


class State(common_graph.State, total=False):
    """Состояние треда плюс то, что известно о прочитанном пакете."""

    # Что прочитано и что нашлось: имена документов, число требований и
    # находок. Сами тексты лежат в `artifacts` — сюда попадает только сводка,
    # по которой интерфейс показывает источник, а нода понимает, что на этом
    # ходе читать заново уже нечего.
    package: dict


def missing_package(state: State, config: RunnableConfig) -> str:
    """
    Причина не начинать прогон, или пустая строка.

    Условие одно: есть ли документы. Их даёт либо папка задачи, либо ссылка на
    страницу Confluence в запросе. Выбор оператора в интерфейсе отвечает на
    вопрос раньше ссылки — по той же причине, что и в конвейере декомпозиции:
    попутная ссылка на wiki в тексте не должна перебивать документы, на которые
    человек показал руками.

    Следующий ход уже начатого треда проверку проходит: пакет прочитан на
    прошлом ходе и лежит в артефактах, а «перепиши вердикт короче» — нормальное
    сообщение, а не пустой вход.
    """
    if state.get("artifacts"):
        return ""

    task = sources.task_dir(config)
    wanted = sources.picked_files(config)
    if wanted:
        names = inputs.readable_files(task)
        lost = [name for name in wanted if name not in names]
        if not lost:
            return ""
        many = len(lost) > 1
        return (
            f"{'Выбраны файлы' if many else 'Выбран файл'} "
            + ", ".join(lost)
            + f", но в папке задачи {'их' if many else 'его'} нет или "
            + ("они не читаются" if many else "он не читается")
            + ". Выберите другие файлы или снимите выбор, чтобы сверить папку "
            "целиком. Прогон остановлен до первого вызова модели — деньги "
            "не потрачены."
        )

    if inputs.readable_files(task):
        return ""

    if confluence.find_page_ids(sources.question_of(state)):
        absent = confluence.missing_vars()
        if not absent:
            return ""
        return (
            "В запросе есть ссылка на страницу Confluence, но читать её нечем: не "
            "заданы " + ", ".join(absent) + ". Заполните переменные в .env и "
            "перезапустите сервер — или положите документы файлами в папку задачи. "
            "Прогон остановлен до первого вызова модели — деньги не потрачены."
        )

    return (
        "Сверять нечего: в папке задачи нет текстовых файлов и в запросе нет ссылки "
        "на страницу Confluence. Этот конвейер проверяет готовые документы друг "
        "против друга, поэтому пересказ пакета в сообщении ему не годится: сверять "
        "его будет не с чем. Положите документы в папку задачи или дайте ссылку на "
        "страницу. Прогон остановлен до первого вызова модели — деньги не потрачены."
    )


# --------------------------------------------------------------------------
# Чтение и сверка пакета
#
# Единственная нода конвейера, которая добывает материал и единственная, которая
# считает. Почему без модели, сказано в `sources.py` и `checks.py`; здесь
# остаётся то, чего в общих модулях быть не может, — как сказать о прочитанном
# именно ролям сверки.
# --------------------------------------------------------------------------
def _read(state: State, config: RunnableConfig) -> dict | None:
    """
    Документы пакета по именам. None — читать нечего, dict с `error` — отказ.

    Порядок тот же, что в проверке входа, и это не совпадение: разойтись им
    нельзя. Прогон, который проверка пустила по файлам, а нода прочитала по
    ссылке, сверил бы не то, что обещал оператору.
    """
    task = sources.task_dir(config)
    wanted = sources.picked_files(config)
    if wanted or inputs.readable_files(task):
        return sources.from_files(sources.question_of(state), task, wanted)
    return sources.from_confluence(sources.question_of(state))


def _documents(picked: dict) -> dict[str, str]:
    """Пакет как имя -> текст. У страницы Confluence имя одно — её заголовок."""
    if picked["kind"] == "files":
        return dict(picked.get("each") or {})
    return {picked.get("title") or picked.get("id") or "страница": picked["text"]}


def package_node(state: State, config: RunnableConfig) -> dict:
    """
    Прочитать документы, сверить их кодом и положить оба результата в артефакты.

    Повторный ход треда пакет не перечитывает и не пересчитывает: документы у
    треда одни, находки по ним детерминированы, и второй прогон сверки дал бы
    ровно то же самое ценой похода в сеть. За свежестью следит оператор, начиная
    новый тред.

    Отказ не останавливает конвейер: роли получат честное «пакет не прочитан»
    (`audit_roles.brief`) и обязаны сказать это в документе, а не выдать пустоту
    за отсутствие находок.
    """
    known = state.get("package") or {}
    if known.get("read"):
        return {}

    picked = _read(state, config)
    if picked is None or picked.get("error"):
        reason = (picked or {}).get("error") or "документы не найдены"
        note = f"Пакет не прочитан: {reason}."
        return {
            "package": {"read": False, "error": reason},
            "artifacts": {audit_roles.PACKAGE: note},
            "messages": [AIMessage(content=note)],
            "stage": "package",
        }

    documents = _documents(picked)
    report = checks.run(documents)
    return {
        "package": {
            "read": True,
            "kind": picked["kind"],
            "names": sorted(documents),
            "requirements": len(report["requirements"]),
            "findings": len(report["findings"]),
            "truncated": bool(picked.get("truncated")),
        },
        "artifacts": {
            audit_roles.PACKAGE: _block(picked, documents),
            audit_roles.CHECKS: checks.as_block(report),
        },
        "messages": [AIMessage(content=_note(picked, documents, report))],
        "stage": "package",
    }


def _block(picked: dict, documents: dict[str, str]) -> str:
    """
    Пакет в том виде, в каком его увидят роли: каждый документ под своим именем.

    Имена обязаны быть видны. Находка «в требованиях сказано одно, а в контракте
    другое» проверяется по имени документа, и роль, которая видела пакет одним
    полотном, назовёт вместо имени пересказ раздела.
    """
    parts = []
    if picked["kind"] == "confluence":
        parts.append(
            f"Источник: страница Confluence «{picked.get('title', '')}» — {picked.get('url', '')}. "
            "Пакет состоит из одного документа: всё, что можно проверить, проверяется "
            "внутри него, и об этом ограничении надо сказать в отчёте."
        )
    else:
        parts.append("Источник: файлы папки задачи. Документов в пакете: "
                     f"{len(documents)}.")
        if picked.get("chosen"):
            parts.append(
                "Эти файлы выбраны оператором как пакет целиком. Отсутствие документа "
                "в списке — это отсутствие документа, а не повод искать его в других."
            )
    if picked.get("skipped"):
        parts.append("Не прочитаны: " + ", ".join(picked["skipped"]))
    if picked.get("truncated"):
        parts.append(
            "ВНИМАНИЕ: пакет обрезан по потолку чтения. Не выдавай «этого нет в "
            "документах» за факт там, где текст мог не поместиться, — скажи, где обрыв."
        )
    body = "\n\n".join(f"## Документ {name}\n\n{text}" for name, text in documents.items())
    return "\n\n".join(parts) + "\n\n---\n\n" + body


def _note(picked: dict, documents: dict[str, str], report: dict) -> str:
    """Что прочитано и что нашлось — оператору в тред, до первого вызова модели."""
    where = (
        f"страница Confluence «{picked.get('title', '')}»"
        if picked["kind"] == "confluence"
        else f"файлы папки задачи ({', '.join(sorted(documents))})"
    )
    by_severity: dict[str, int] = {}
    for finding in report["findings"]:
        by_severity[finding["severity"]] = by_severity.get(finding["severity"], 0) + 1
    counted = ", ".join(f"{name} {number}" for name, number in sorted(by_severity.items()))
    tail = f"находок сверки: {counted}" if counted else "формальных дефектов не найдено"
    return (
        f"Прочитан пакет: {where} — документов {len(documents)}, "
        f"требований с идентификаторами {len(report['requirements'])}, {tail}. "
        "Дальше работает модель."
    )


def build_graph(llm: Any = None) -> StateGraph:
    """
    Собрать конвейер по описанию из `audit_roles.py`.

    Сборка общая; своего у этого конвейера — описание ролей, проверка входа и
    узел чтения с сверкой. Схема состояния обязательна: без неё LangGraph
    выбросит из обновления поле `package`, и нода читала бы документы заново
    на каждом ходе треда.

    llm — готовая модель вместо собранной из окружения. Нужна тестам, чтобы
    прогнать граф целиком на подделке, без ключей и без сети.
    """
    return common_graph.build_graph(
        llm=llm,
        pipeline=PIPELINE,
        admission=missing_package,
        prelude=package_node,
        state_schema=State,
    )


# Для Studio / langgraph dev: компилируем БЕЗ чекпоинтера, как и остальные графы.
graph = build_graph().compile()
