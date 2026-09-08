"""
Задача 4.2: база знаний вместо политики в промпте.

Проверяется не качество поиска (для десятка разделов на одном языке его хватает
с запасом), а два свойства, ради которых всё сделано именно так:

  * справка подставляется в КОНЕЦ вопроса оператора и записывается обратно
    в состояние — то есть история треда остаётся побайтово стабильной;
  * системный префикс в режиме базы знаний теряет справочные разделы и не
    теряет поведенческие: правило «не выдумывать факты» не должно зависеть от
    того, нашёлся ли документ.

Справочные разделы живут в общем начале префикса всех пяти ролей, поэтому
вынести их — значит укоротить префикс сразу всем, а не одной.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent import knowledge, prompts, roles
from agent.graph import build_graph

CONFIG = {"configurable": {"thread_id": "t-1"}}


class Recorder:
    """Модель-подделка, которая запоминает всё, что ей прислали."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers) or ["Ответ оператору"]
        self.requests: list[list] = []

    def invoke(self, messages: list) -> AIMessage:
        self.requests.append(list(messages))
        text = self.answers[min(len(self.requests), len(self.answers)) - 1]
        return AIMessage(
            content=text,
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
    monkeypatch.setenv("MEMORY_ENABLED", "0")  # память проверяется отдельно


def run(model: Recorder, question: str) -> dict:
    app = build_graph(llm=model).compile()
    return app.invoke({"messages": [HumanMessage(question)]}, config=CONFIG)


# --------------------------------------------------------------------------
# Источник документов
# --------------------------------------------------------------------------
def test_builtin_documents_come_from_the_prompt_itself():
    titles = [document.title for document in knowledge.builtin_documents()]

    assert titles == list(prompts.POLICY_SECTIONS)
    assert "Без эмодзи" in knowledge.builtin_documents()[0].text


def test_core_prompt_loses_the_reference_policy_and_keeps_the_rules():
    core = prompts.core_prompt(roles.FIRST.key)

    assert "Стек по умолчанию" not in core
    assert "Без эмодзи" not in core
    assert "Ты не выдумываешь факты" in core
    assert "Справка из базы знаний" in core  # объяснение, откуда возьмётся справка


def test_core_prompt_keeps_the_stage_instructions():
    """
    Справочными бывают стандарты, а не инструкции этапа: без них роль не знает,
    что делать, и вынести их в базу знаний нельзя ни при каком объёме.
    """
    core = prompts.core_prompt(roles.FIRST.key)

    assert "Ты Senior System Analyst" in core
    assert "Жизненный цикл ключевых сущностей" in core


def test_core_prompt_is_byte_stable():
    """Префикс обязан быть одинаковым на каждом запросе треда — иначе кеша нет."""
    assert prompts.core_prompt(roles.FIRST.key) == prompts.core_prompt(roles.FIRST.key)


def test_unknown_role_is_refused_loudly():
    """Опечатка в ключе роли — это молча пустой префикс, если её не поймать."""
    with pytest.raises(KeyError, match="неизвестная роль"):
        prompts.core_prompt("архитектор")


def test_documents_are_read_from_a_directory(monkeypatch: pytest.MonkeyPatch, tmp_path):
    (tmp_path / "sla.md").write_text(
        "# SLA по тарифам\n\nScale: 99.9 процента.", encoding="utf-8"
    )
    (tmp_path / "empty.md").write_text("   ", encoding="utf-8")
    monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path))

    found = knowledge.documents()

    assert [document.title for document in found] == ["SLA по тарифам"]
    assert found[0].text == "Scale: 99.9 процента."


def test_missing_directory_is_a_configuration_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KNOWLEDGE_DIR", "нет-такой-папки")

    with pytest.raises(Exception, match="KNOWLEDGE_DIR"):
        knowledge.documents()


# --------------------------------------------------------------------------
# Поиск
# --------------------------------------------------------------------------
def test_question_about_the_stack_finds_the_technology_standard():
    found = knowledge.search("Нужен ли Redis или хватит PostgreSQL для очереди?")

    assert found and found[0].title == "Технологический стандарт"


def test_question_about_layout_finds_the_formatting_standard():
    found = knowledge.search("Как оформить документ: заголовки, списки, эмодзи?")

    assert "Стандарт оформления результата" in [d.title for d in found]


def test_nothing_relevant_gives_nothing(monkeypatch: pytest.MonkeyPatch):
    """Пустая справка дешевле и честнее случайной."""
    monkeypatch.setenv("KNOWLEDGE_MIN_SCORE", "0.5")

    assert knowledge.search("погода в Тбилиси на выходных") == []


def test_top_k_limits_the_answer(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KNOWLEDGE_TOP_K", "1")
    monkeypatch.setenv("KNOWLEDGE_MIN_SCORE", "0")

    assert len(knowledge.search("Redis эмодзи таблица брокер")) == 1


# --------------------------------------------------------------------------
# Подстановка в тред
# --------------------------------------------------------------------------
def test_reference_is_appended_to_the_end_of_the_question(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("KNOWLEDGE_ENABLED", "1")
    question = "Спроектировать выгрузку: нужен ли Redis рядом с PostgreSQL?"
    model = Recorder()

    result = run(model, question)

    stored = result["messages"][0].content
    assert stored.startswith(question)  # задача осталась первой, справка — после
    assert knowledge.BLOCK_TITLE in stored
    assert "Redis только при доказанной необходимости" in stored


def test_the_model_sees_the_shortened_prefix_in_knowledge_mode(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("KNOWLEDGE_ENABLED", "1")
    model = Recorder()

    run(model, "Спроектировать выгрузку: нужен ли Redis рядом с PostgreSQL?")

    system = model.requests[0][0].content
    assert "Стек по умолчанию" not in system  # стандарт уехал в базу знаний
    assert "Ты не выдумываешь факты" in system


def test_without_the_flag_nothing_is_substituted():
    question = "Спроектировать выгрузку: нужен ли Redis рядом с PostgreSQL?"
    model = Recorder()

    result = run(model, question)

    assert result["messages"][0].content == question
    assert "Стек по умолчанию" in model.requests[0][0].content  # стандарт в префиксе


def test_substitution_happens_once_per_question(monkeypatch: pytest.MonkeyPatch):
    """
    Повторный вход в ноду не должен наращивать справку: сообщение заменяется
    по своему же id, а признак подстановки ищется в тексте.
    """
    monkeypatch.setenv("KNOWLEDGE_ENABLED", "1")
    from agent.graph import context_node

    first = context_node(
        {"messages": [HumanMessage("Нужен ли Redis для выгрузки", id="m-1")]}, CONFIG
    )
    again = context_node({"messages": first["messages"]}, CONFIG)

    assert first["messages"][0].id == "m-1"  # правка, а не новая реплика
    assert again == {}
