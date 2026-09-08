"""
Задача 4.4: долгая память между тредами.

Критерий приёмки — второй тред по тому же аккаунту цитирует факт из первого.
Здесь он проверяется буквально: модель-подделка отвечает тем, что ей прислали,
поэтому факт из первого треда обязан оказаться в тексте ответа второго.

Остальное — про аккуратность: память не должна ни ломать тред при отсутствии
store, ни расти без предела, ни ехать в промпт впереди стабильного префикса.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from agent import memory
from agent.graph import build_graph

FIRST = "Клиент acc-1024 жалуется на экспорт 250 тысяч строк. Что отвечать?"
SECOND = "Снова обращение по acc-1024. Что у него было в прошлый раз?"


class Echo:
    """Подделка модели: возвращает то, что получила, — видно, что уехало в промпт."""

    def invoke(self, messages: list) -> AIMessage:
        return AIMessage(
            content=f"Экспорт больше 100000 строк уходит на почту. Вход: {messages[-1].content}",
            response_metadata={
                "token_usage": {
                    "prompt_cache_hit_tokens": 100,
                    "prompt_cache_miss_tokens": 10,
                    "completion_tokens": 5,
                }
            },
        )


@pytest.fixture(autouse=True)
def no_publishing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> InMemoryStore:
    """Store, видимый и графу, и прямым вызовам функций памяти."""
    active = InMemoryStore()
    monkeypatch.setattr(memory, "store", lambda: active)
    return active


# --------------------------------------------------------------------------
# Критерий приёмки
# --------------------------------------------------------------------------
def test_second_thread_quotes_a_fact_from_the_first():
    shared = InMemoryStore()
    app = build_graph(llm=Echo()).compile(checkpointer=InMemorySaver(), store=shared)

    app.invoke(
        {"messages": [HumanMessage(FIRST)]},
        config={"configurable": {"thread_id": "t-1"}},
    )
    second = app.invoke(
        {"messages": [HumanMessage(SECOND)]},
        config={"configurable": {"thread_id": "t-2"}},
    )

    answer = second["messages"][-1].content
    assert memory.BLOCK_TITLE in answer
    assert "экспорт 250 тысяч строк" in answer.lower()


def test_memory_is_appended_after_the_question_not_before():
    """Тот же канал, что и у справки: всё переменное — строго в конец."""
    shared = InMemoryStore()
    app = build_graph(llm=Echo()).compile(checkpointer=InMemorySaver(), store=shared)

    app.invoke(
        {"messages": [HumanMessage(FIRST)]},
        config={"configurable": {"thread_id": "t-1"}},
    )
    second = app.invoke(
        {"messages": [HumanMessage(SECOND)]},
        config={"configurable": {"thread_id": "t-2"}},
    )

    assert second["messages"][0].content.startswith(SECOND)


def test_another_account_does_not_see_the_memory():
    shared = InMemoryStore()
    app = build_graph(llm=Echo()).compile(checkpointer=InMemorySaver(), store=shared)

    app.invoke(
        {"messages": [HumanMessage(FIRST)]},
        config={"configurable": {"thread_id": "t-1"}},
    )
    other = app.invoke(
        {"messages": [HumanMessage("Обращение по acc-2048, что с оплатой?")]},
        config={"configurable": {"thread_id": "t-3"}},
    )

    assert memory.BLOCK_TITLE not in other["messages"][0].content


def test_switch_stops_both_writing_and_reading(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MEMORY_ENABLED", "0")
    shared = InMemoryStore()
    app = build_graph(llm=Echo()).compile(checkpointer=InMemorySaver(), store=shared)

    app.invoke(
        {"messages": [HumanMessage(FIRST)]},
        config={"configurable": {"thread_id": "t-1"}},
    )

    assert shared.search(memory.namespace()) == []


def test_thread_works_without_a_store():
    """Store — вспомогательная инфраструктура: без него агент отвечает как раньше."""
    app = build_graph(llm=Echo()).compile(checkpointer=InMemorySaver())

    result = app.invoke(
        {"messages": [HumanMessage(FIRST)]},
        config={"configurable": {"thread_id": "t-1"}},
    )

    assert result["messages"][-1].content.startswith("Экспорт больше 100000 строк")


# --------------------------------------------------------------------------
# Опознание аккаунта
# --------------------------------------------------------------------------
def test_memory_does_not_remember_what_we_appended_ourselves(
    store, monkeypatch: pytest.MonkeyPatch
):
    """
    В память едет вопрос оператора, а не наш же блок справки, дописанный
    к нему нодой контекста. Разделитель у обоих блоков общий — на нём и режем.
    """
    monkeypatch.setenv("KNOWLEDGE_ENABLED", "1")
    from agent import knowledge
    from agent.graph import CONTEXT_SEPARATOR, operator_question

    assert knowledge.as_block(list(knowledge.builtin_documents()[:1])).startswith(
        CONTEXT_SEPARATOR
    )
    assert memory.as_block([("acc-1024", ["факт"])]).startswith(CONTEXT_SEPARATOR)
    assert operator_question(f"Вопрос{CONTEXT_SEPARATOR}Справка") == "Вопрос"


def test_accounts_are_found_once_and_in_order():
    found = memory.accounts_in("По acc-2048 и acc-1024, снова ACC-2048")

    assert found == ["acc-2048", "acc-1024"]


def test_account_pattern_is_configurable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MEMORY_ACCOUNT_PATTERN", r"CL[0-9]{4}")

    assert memory.accounts_in("Клиент CL7788 просит счёт") == ["cl7788"]


def test_broken_pattern_is_a_configuration_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MEMORY_ACCOUNT_PATTERN", "acc-[0-9")

    with pytest.raises(Exception, match="MEMORY_ACCOUNT_PATTERN"):
        memory.accounts_in("acc-1024")


# --------------------------------------------------------------------------
# Запись
# --------------------------------------------------------------------------
def test_fact_carries_the_question_and_the_first_line_of_the_answer():
    fact = memory.fact_from("Что с экспортом?", "Уходит на почту.\nПодробности ниже.")

    assert "Что с экспортом?" in fact
    assert "Уходит на почту." in fact
    assert "Подробности ниже" not in fact


def test_only_the_last_facts_are_kept(store, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MEMORY_MAX_FACTS", "2")

    for number in range(4):
        memory.remember("acc-1024", f"факт {number}")

    assert memory.facts("acc-1024") == ["факт 2", "факт 3"]


def test_the_same_fact_is_not_written_twice(store):
    assert memory.remember("acc-1024", "один и тот же факт") is True
    assert memory.remember("acc-1024", "один и тот же факт") is False
    assert memory.facts("acc-1024") == ["один и тот же факт"]


def test_without_a_store_writing_is_a_no_op():
    assert memory.remember("acc-1024", "факт") is False
    assert memory.facts("acc-1024") == []


def test_broken_store_does_not_break_the_answer(monkeypatch: pytest.MonkeyPatch):
    """Ответ оператору уже готов — падать из-за вспомогательного хранилища нельзя."""

    class Broken:
        def get(self, *args, **kwargs):
            raise RuntimeError("хранилище недоступно")

        def put(self, *args, **kwargs):
            raise RuntimeError("хранилище недоступно")

    monkeypatch.setattr(memory, "store", Broken)

    assert memory.facts("acc-1024") == []
    assert memory.remember("acc-1024", "факт") is False
