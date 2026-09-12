"""
Сборка графа: одна на все конвейеры проекта.

Узлы порождаются циклом по описанию ролей, а не перечисляются руками, и
разница между конвейерами передаётся параметрами: своё описание ролей, своя
проверка входа, узел до первой роли и узел после последней. Шестой конвейер —
это ещё один `Pipeline`, а не ещё одна сборка; когда сборок было две, копия
успела разойтись с оригиналом и потерять ноду контекста.

Файл отделён от нод намеренно: здесь нет ни одного побочного эффекта и ни
одного обращения в сеть — только топология. Поэтому её можно прочитать
целиком за один экран и увидеть, что граф действительно делает.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from agent import roles
from agent.nodes import (
    approve_node,
    context_node,
    make_gate_node,
    make_role_node,
    publish_node,
    remember_node,
)
from agent.pipeline import Pipeline
from agent.routes import (
    Admission,
    budget_gate,
    halted_node,
    make_entry_router,
    make_gate_router,
    make_no_input_node,
    make_role_router,
    make_tools_router,
    over_budget_node,
)
from agent.state import Options, State
from agent.tools import FILE_TOOLS as TOOLS


def _require(pipeline: Pipeline, what: str, declared: bool, given: bool) -> None:
    """Описание конвейера обещало узел — сборщику обязаны передать функцию."""
    if declared == given:
        return
    raise ValueError(
        f"конвейер {pipeline.key!r}: {what} "
        + (
            "объявлен в Pipeline, но функция сборщику не передана"
            if declared
            else "передан сборщику, но в Pipeline не объявлен"
        )
    )


def build_graph(
    unstable_prefix: bool = False,
    llm: Any = None,
    pipeline: Pipeline = roles.PIPELINE,
    admission: Admission | None = None,
    prelude: Any = None,
    postlude: Any = None,
    state_schema: Any = None,
    options_schema: Any = None,
) -> StateGraph:
    """
    Собрать файловый конвейер по переданному описанию ролей.

    Узлы порождаются циклом, а не перечисляются руками: пять этапов отличаются
    только данными роли, и переписанный руками пятый раз узел — это пятый шанс
    ошибиться в одном и том же месте. Шестая роль добавляется строкой в
    `pipeline.roles` и своим префиксом; здесь не меняется ничего. По умолчанию
    собирается основной конвейер аналитики, поэтому старые вызовы совместимы.

    admission — необязательная проверка входа перед первым вызовом модели.
    Без неё вход в тред решают только ворота бюджета: у конвейера аналитики
    материалом может быть само сообщение оператора, и пустого входа у него
    не бывает.

    prelude — функция узла без модели между контекстом и первой ролью. Имя
    узла берётся из описания конвейера (`Pipeline.prelude`), а не приезжает
    рядом с функцией: имя нужно ещё и манифесту интерфейса, и второе место
    для него означало бы, что граф и его подпись могут разъехаться. Нужен конвейеру, у которого материал добывается кодом, а не
    моделью: адрес задачи известен заранее, и просить модель позвать инструмент
    с уже известным аргументом — это оплаченный вызов, который ничего не решает.
    Результат такой узел кладёт в `artifacts` под своим ключом: `Pipeline.done()`
    перебирает роли, а не ключи, поэтому в страницы публикации он не попадает,
    а в сообщение роли подставляется тем же `brief()`, что и документы этапов.

    postlude — функция узла без модели между последней ролью и записью
    в память; имя так же берётся из описания конвейера. Зеркало прелюдии на другом конце конвейера, и нужен он
    тому, у кого документ — не конечный результат, а заготовка внешнего
    действия: конвейер декомпозиции по нему заводит задачи в трекере
    (`jira_graph.create_node`). Стоит он до `remember` и до публикации
    намеренно: заведённые ключи должны попасть и в память хода, и в документ,
    который уедет на wiki, иначе о них будет знать только тот, кто смотрел
    в экран во время прогона.

    Ветки отказа — упор в бюджет и остановка оператором — ведут в `remember`
    мимо постлюдии: заводить задачи по недописанному backlog'у нельзя.

    state_schema и options_schema — расширенные описания состояния и настроек
    хода. Обязательны конвейеру, у которого есть свои поля: LangGraph молча
    выбрасывает из обновления всё, чего нет в схеме, и узел, положивший туда
    прочитанный источник, обнаружил бы на следующем ходе пустое поле и сходил
    в сеть заново.
    """
    # Описание конвейера и переданные сборщику функции обязаны совпасть.
    # Разойтись они могут молча и в обе стороны: объявленная в описании
    # проверка входа без функции — это обещанная в интерфейсе ветка, которой
    # в графе нет, а функция без объявления — узел без подписи, нарисованный
    # сырым именем. Оба случая доживают до экрана, потому что на экране их
    # никто не ждёт; поэтому здесь исключение, а не тест.
    _require(pipeline, "admission", pipeline.admission, admission is not None)
    _require(pipeline, "prelude", pipeline.prelude is not None, prelude is not None)
    _require(pipeline, "postlude", pipeline.postlude is not None, postlude is not None)

    builder = StateGraph(state_schema or State, context_schema=options_schema or Options)
    # Узел инструментов заводится только тогда, когда в конвейере есть кому в
    # него ходить. Инструменты привязываются к модели у всех ролей — иначе
    # менялся бы кешируемый префикс, — но переписку с ними ведут только
    # отмеченные роли, и у конвейера без единой такой роли узел остался бы
    # висеть в топологии без входящих и исходящих рёбер. В Studio и в
    # собственном интерфейсе это отдельная коробка, которая не может
    # выполниться никогда, — обещание вызова, которого граф не делает.
    builder.add_node("context", partial(context_node, external_sources=pipeline.key == "agent"))
    if pipeline.has_tools:
        builder.add_node("tools", ToolNode(list(pipeline.tools or TOOLS)))
    builder.add_node("over_budget", partial(over_budget_node, pipeline=pipeline))
    builder.add_node("halted", partial(halted_node, pipeline=pipeline))
    builder.add_node("remember", remember_node)
    builder.add_node("approve", partial(approve_node, pipeline=pipeline))
    builder.add_node("publish", partial(publish_node, pipeline=pipeline))
    for role in pipeline.roles:
        builder.add_node(
            role.key,
            make_role_node(role, unstable_prefix, llm, pipeline=pipeline),
        )

    # Ворота бюджета на входе в тред ведут через ноду контекста: справка и
    # список файлов подставляются один раз на задачу оператора. Конвейер
    # с проверкой входа получает здесь же третью ветку — отказ без вызова
    # модели, — и она стоит перед бюджетом: см. `make_entry_router`.
    if admission is None:
        builder.add_conditional_edges(
            START, budget_gate, {"agent": "context", "over_budget": "over_budget"}
        )
    else:
        builder.add_node("no_input", make_no_input_node(admission))
        builder.add_conditional_edges(
            START,
            make_entry_router(admission),
            {
                "agent": "context",
                "over_budget": "over_budget",
                "no_input": "no_input",
            },
        )
        builder.add_edge("no_input", "remember")
    if prelude is None:
        builder.add_edge("context", pipeline.first.key)
    else:
        # Узел стоит ПОСЛЕ ворот бюджета, хотя денег не тратит: ворота на входе
        # в тред решают, разговаривать ли вообще, и ходить в чужой трекер ради
        # треда, который всё равно упрётся в лимит, незачем.
        name = pipeline.prelude.key
        builder.add_node(name, prelude)
        builder.add_edge("context", name)
        builder.add_edge(name, pipeline.first.key)

    if postlude is not None:
        builder.add_node(pipeline.postlude.key, postlude)
        builder.add_edge(pipeline.postlude.key, "remember")
    last_target = pipeline.postlude.key if postlude is not None else "remember"

    for role in pipeline.roles:
        following = pipeline.after(role)
        target = f"gate_{following.key}" if following else last_target

        if role.reads_files:
            builder.add_conditional_edges(
                role.key,
                make_role_router(role, pipeline=pipeline, last_target=last_target),
                {"tools": "tools", target: target},
            )
        else:
            builder.add_edge(role.key, target)

        if following:
            builder.add_node(
                f"gate_{following.key}",
                make_gate_node(following, pipeline=pipeline),
            )
            builder.add_conditional_edges(
                f"gate_{following.key}",
                make_gate_router(following),
                {
                    "agent": following.key,
                    "over_budget": "over_budget",
                    "halted": "halted",
                },
            )

    # Возврат из инструментов — сразу в роль, минуя ноду контекста: подставлять
    # там уже нечего, последнее сообщение не человеческое. Узел инструментов
    # один на граф, и адресат возврата берётся из состояния, а не зашивается в
    # ребро: сейчас спрашивающая роль у каждого конвейера ровно одна, но вторая
    # добавляется флагом `reads_files`, и топология при этом не меняется.
    # Список назначений всё равно перечислен целиком: LangGraph рисует граф по
    # нему, а не по тому, что вернёт функция.
    readers = [role for role in pipeline.roles if role.reads_files]
    if readers:
        builder.add_conditional_edges(
            "tools",
            make_tools_router(pipeline),
            {**{role.key: role.key for role in readers}, "over_budget": "over_budget"},
        )

    # Прерванный конвейер публикует то, что успел: недописанная архитектура
    # полезнее пустой страницы, а причина остановки видна в самом документе.
    builder.add_edge("over_budget", "remember")
    builder.add_edge("halted", "remember")
    builder.add_edge("remember", "approve")
    builder.add_edge("approve", "publish")
    builder.add_edge("publish", END)
    return builder
