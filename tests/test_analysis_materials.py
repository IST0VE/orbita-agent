"""
Материалы конвейера аналитики, прочитанные кодом до первой роли.

Раньше выбранные оператором файлы аналитик открывал инструментом сам, а ссылки
из запроса нода контекста дописывала в сообщение оператора. Прочитанное видел
один аналитик и только на первом ходе треда; ревьюер не видел первоисточника
вовсе. Здесь проверяется то, что пришло на смену, — по образцу конвейера
подготовки задачи:

- выбранный файл читает код, и аналитик получает его брифом без вызова
  инструмента;
- ревьюер видит тот же файл и реестр источников, проектировщики — нет;
- на следующем ходе треда материалы остаются в брифе, хотя в сообщении
  оператора их уже нет;
- реестр источников и строка «что прочитано» стоят на странице аналитика.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent import documents, inputs, knowledge, memory, nodes, render, roles
from agent.builder import build_graph

MEETING = "На встрече решили: выгрузка идёт в Parquet, срок хранения 30 дней."


class Recording:
    """Подделка модели: помнит вход каждой роли и отвечает документом этапа."""

    def __init__(self) -> None:
        self.calls: list[list] = []

    def invoke(self, messages: list) -> AIMessage:
        self.calls.append(messages)
        return AIMessage(
            content=f"# Документ\n\nЭтап {len(self.calls)}.",
            response_metadata={
                "token_usage": {
                    "prompt_cache_hit_tokens": 100,
                    "prompt_cache_miss_tokens": 10,
                    "completion_tokens": 5,
                }
            },
        )

    def brief_of(self, index: int) -> str:
        """Вход роли: первое сообщение после системного префикса."""
        return str(self.calls[index][1].content)


@pytest.fixture(autouse=True)
def quiet(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")
    monkeypatch.setenv("MEMORY_ENABLED", "0")


@pytest.fixture
def folder():
    path = inputs.ensure_root() / "задача"
    path.mkdir(parents=True)
    (path / "встреча.md").write_text(MEETING, encoding="utf-8")
    (path / "заметки.md").write_text("Посторонние заметки.", encoding="utf-8")
    return path


def config(**extra) -> dict:
    return {"configurable": {"thread_id": "m-1", "input_dir": "задача", **extra}}


def test_a_picked_file_is_read_by_code_and_reaches_the_analyst(folder):
    model = Recording()
    result = build_graph(llm=model).compile().invoke(
        {"messages": [HumanMessage("Спроектировать выгрузку заказов.")]},
        config=config(input_file="встреча.md"),
    )

    analyst = model.brief_of(0)
    assert MEETING in analyst
    assert "`[ФАЙЛ имя]`" in analyst
    # Невыбранный файл назван, но не прочитан: выбирать за оператора код не должен.
    assert "заметки.md" in analyst and "Посторонние заметки." not in analyst
    # Ни одного вызова инструмента: пять ролей — пять вызовов модели.
    assert len(model.calls) == len(roles.ROLES)
    assert result["materials"]["files"] == ["встреча.md"]
    # Заметка о прочитанном видна оператору в треде до первой роли.
    notes = [m.content for m in result["messages"] if isinstance(m, AIMessage)]
    assert "Материалы оператора прочитаны: встреча.md." in notes[0]


def test_the_reviewer_sees_the_source_and_the_designers_do_not(folder):
    model = Recording()
    build_graph(llm=model).compile().invoke(
        {"messages": [HumanMessage("Спроектировать выгрузку заказов.")]},
        config=config(input_file="встреча.md"),
    )

    designers = [model.brief_of(index) for index in range(1, len(roles.ROLES) - 1)]
    assert all(MEETING not in text for text in designers)
    reviewer = model.brief_of(len(roles.ROLES) - 1)
    assert MEETING in reviewer
    assert "Реестр источников прогона" in reviewer
    assert "[ФАЙЛ встреча.md]" in reviewer


def test_the_next_turn_keeps_the_materials_in_the_brief(folder):
    """
    Указание «поправь раздел» — это новое сообщение без материалов. Раньше
    аналитик правил требования, не видя, по чему их писал.
    """
    model = Recording()
    app = build_graph(llm=model).compile(checkpointer=InMemorySaver())
    conf = config(input_file="встреча.md")
    app.invoke({"messages": [HumanMessage("Спроектировать выгрузку заказов.")]}, config=conf)
    model.calls.clear()

    app.invoke({"messages": [HumanMessage("Поправь раздел про хранение.")]}, config=conf)

    analyst = model.calls[0]
    assert MEETING in str(analyst[1].content)
    assert "# Задача\n\nСпроектировать выгрузку заказов." in str(analyst[1].content)
    # Указание оператора — последнее слово запроса.
    assert "Поправь раздел про хранение." in str(analyst[-1].content)


def test_the_analyst_page_carries_what_was_read_and_the_registry(folder):
    model = Recording()
    state = build_graph(llm=model).compile().invoke(
        {"messages": [HumanMessage("Спроектировать выгрузку заказов.")]},
        config=config(input_file="встреча.md"),
    )

    pages = {
        page["role"]: page["document"]
        for page in documents.stage_pages(state, {}, render.MARKDOWN, "", roles.PIPELINE)
    }
    assert "Материалы оператора: встреча.md" in pages["requirements"]
    assert "<summary>Источники прогона</summary>" in pages["requirements"]
    assert "[ФАЙЛ встреча.md]" in pages["requirements"]
    # Строка о прочитанном — на каждой странице, реестр — только у аналитика.
    assert "Материалы оператора: встреча.md" in pages["api"]
    assert "Источники прогона" not in pages["api"]


def test_nothing_picked_means_nothing_read_and_the_list_is_named(folder):
    model = Recording()
    build_graph(llm=model).compile().invoke(
        {"messages": [HumanMessage("Спроектировать выгрузку заказов.")]},
        config=config(),
    )

    analyst = model.brief_of(0)
    assert MEETING not in analyst
    assert "Файлы чата: встреча.md, заметки.md" in analyst
    assert "read_task_file" in analyst


# --------------------------------------------------------------------------
# Справка и память для роли с брифом
# --------------------------------------------------------------------------
def test_recalled_context_keeps_knowledge_and_memory_and_drops_the_file_list():
    text = (
        "Вопрос оператора"
        f"\n\n---\n{knowledge.BLOCK_TITLE} (подставлена автоматически):\n\nСтандарт A"
        f"\n\n---\n### {memory.BLOCK_TITLE} по аккаунту acc-1\n- факт"
        f"\n\n---\n{inputs.BLOCK_TITLE} «задача» (подставлен автоматически):\n\n- a.md"
    )
    turn = [HumanMessage(text), AIMessage("заметка")]

    recalled = nodes.recalled_context(turn)

    assert "Стандарт A" in recalled
    assert "- факт" in recalled
    assert "a.md" not in recalled
    assert "Вопрос оператора" not in recalled


def test_the_briefed_analyst_is_asked_with_the_brief_first():
    """Системный префикс, бриф, дальше — только собственная переписка роли."""
    model = Recording()
    build_graph(llm=model).compile().invoke({"messages": [HumanMessage("Задача")]})

    first = model.calls[0]
    assert isinstance(first[0], SystemMessage)
    assert str(first[1].content).startswith("# Задача\n\nЗадача")
