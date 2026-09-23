"""
Следующее сообщение в треде, где документы уже выпущены: правка, а не новая задача.

Раньше такое сообщение становилось задачей само по себе. «Поправь раздел про
коды ошибок» уезжало пяти ролям вместо исходной задачи, прежних документов
они не видели и писали пять новых по одной фразе — а публикация перезаписывала
ими страницы треда, заголовок которых по-прежнему назывался исходной задачей.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent import nodes, roles
from agent.graph import build_graph, page_title

CONFIG = {"configurable": {"thread_id": "followup-1"}}
TASK = "Спроектировать асинхронную выгрузку заказов через REST."
FIX = "Поправь раздел про коды ошибок в контракте API."
MORE = "И добавь лимит на размер выгрузки."


class Recorder:
    """Подделка модели: помнит весь запрос каждой роли, кроме системного префикса."""

    def __init__(self) -> None:
        self.requests: list[str] = []

    def invoke(self, messages: list) -> AIMessage:
        self.requests.append("\n".join(str(m.content) for m in messages[1:]))
        return AIMessage(content=f"Документ {len(self.requests)}")


@pytest.fixture(autouse=True)
def quiet(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")
    monkeypatch.setenv("MEMORY_ENABLED", "0")


def thread():
    model = Recorder()
    app = build_graph(llm=model).compile(checkpointer=InMemorySaver())
    first = app.invoke({"messages": [HumanMessage(TASK)]}, CONFIG)
    return app, model, first


def test_a_follow_up_revises_the_documents_of_the_same_task():
    app, model, first = thread()
    written = dict(first["artifacts"])
    model.requests.clear()

    second = app.invoke({"messages": [HumanMessage(FIX)]}, CONFIG)

    assert second["task"] == TASK  # задача треда не подменяется правкой
    assert [note["text"] for note in second["notes"]] == [FIX]
    assert len(model.requests) == len(roles.ROLES)
    for role, request in zip(roles.ROLES, model.requests, strict=True):
        assert TASK in request, role.key
        assert FIX in request, role.key
        # Своя прошлая версия — чтобы править, а не писать заново.
        assert "Предыдущая версия этого документа" in request, role.key
        assert written[role.key] in request, role.key


def test_instructions_pile_up_in_order():
    app, model, _ = thread()
    app.invoke({"messages": [HumanMessage(FIX)]}, CONFIG)
    model.requests.clear()

    third = app.invoke({"messages": [HumanMessage(MORE)]}, CONFIG)

    assert third["task"] == TASK
    assert [note["text"] for note in third["notes"]] == [FIX, MORE]
    last = model.requests[-1]
    assert last.index(FIX) < last.index(MORE)


def test_the_pages_keep_the_title_of_the_task():
    app, _, first = thread()
    second = app.invoke({"messages": [HumanMessage(FIX)]}, CONFIG)

    assert page_title(second, CONFIG) == page_title(first, CONFIG)
    assert FIX not in page_title(second, CONFIG)


def test_without_documents_the_next_message_is_a_new_task():
    """Править нечего — например, прошлый прогон остановили до первого этапа."""
    update = nodes.context_node(
        {"task": TASK, "artifacts": {}, "messages": [HumanMessage(FIX, id="m")]},
        {},
        revisions=True,
    )

    assert update["task"] == FIX
    assert "notes" not in update


def test_graphs_without_revisions_keep_their_behaviour():
    """
    Графы НТ зовут ноду контекста без флага: там следующее сообщение — новый
    анализ со своими параметрами, а не правка прошлого отчёта.
    """
    update = nodes.context_node(
        {"task": TASK, "artifacts": {"report": "отчёт"}, "messages": [HumanMessage(FIX, id="m")]},
        {},
    )

    assert update["task"] == FIX
    assert "notes" not in update
