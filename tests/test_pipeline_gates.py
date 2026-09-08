"""
Ворота между этапами: PIPELINE_REQUIRE_APPROVAL.

Здесь LangGraph делает то, чего последовательный запуск ролей не умеет в
принципе: между этапами можно вклиниться. Требования, написанные по неверно
понятой задаче, стоят не одного вызова модели, а четырёх — по одному на каждый
следующий этап, — и ворота дают остановиться на первой странице.

Механика та же, что у подтверждения публикации: `interrupt()` замораживает
тред, `Command(resume=...)` его продолжает. Проверять надо три вещи: что по
умолчанию ворота молчат, что подтверждение пропускает дальше и что отказ —
это действительно отказ, после которого оставшиеся роли не оплачиваются.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent import config as cfg
from agent import roles
from agent.graph import build_graph

CONFIG = {"configurable": {"thread_id": "t-1"}}
TASK = "Спроектировать асинхронную выгрузку заказов."


def usage() -> dict:
    return {
        "token_usage": {
            "prompt_cache_hit_tokens": 100,
            "prompt_cache_miss_tokens": 10,
            "completion_tokens": 5,
        }
    }


class Counting:
    """Подделка модели, которая помнит, сколько раз её позвали."""

    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, messages: list) -> AIMessage:
        self.calls += 1
        return AIMessage(content=f"Документ этапа {self.calls}", response_metadata=usage())


class Silent:
    """Модель, которая ничего не написала: этап не состоялся."""

    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, messages: list) -> AIMessage:
        self.calls += 1
        text = "" if self.calls == 1 else f"Документ этапа {self.calls}"
        return AIMessage(content=text, response_metadata=usage())


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MEMORY_ENABLED", "0")
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")


@pytest.fixture
def gates_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PIPELINE_REQUIRE_APPROVAL", "1")


def app_with(model):
    return build_graph(llm=model).compile(checkpointer=InMemorySaver())


def start(app):
    return app.invoke({"messages": [HumanMessage(TASK)]}, config=CONFIG)


def interrupt_of(result: dict) -> dict:
    """Значение `interrupt()` из ответа графа."""
    return result["__interrupt__"][0].value


def published_files() -> list[Path]:
    directory = Path(cfg.publish_dir())
    return sorted(directory.glob("*.md")) if directory.exists() else []


# --------------------------------------------------------------------------
# По умолчанию ворот не видно
# --------------------------------------------------------------------------
def test_pipeline_runs_through_without_the_flag():
    """
    Выключено по умолчанию не из осторожности: конвейер задуман как один запуск
    от задачи до пяти документов, и остановка на каждом этапе превращает его
    в переписку.
    """
    model = Counting()
    result = start(app_with(model))

    assert "__interrupt__" not in result
    assert model.calls == len(roles.ROLES)
    assert list(result["artifacts"]) == list(roles.KEYS)


# --------------------------------------------------------------------------
# Ворота включены
# --------------------------------------------------------------------------
def test_first_gate_stops_right_after_the_analyst(gates_on):
    model = Counting()
    result = start(app_with(model))

    assert model.calls == 1  # четыре оставшиеся роли не оплачены
    payload = interrupt_of(result)
    assert payload["action"] == "stage"
    assert payload["stage"] == "requirements"
    assert payload["next"] == "api"
    assert payload["document"] == "Документ этапа 1"


def test_approval_lets_the_next_stage_run(gates_on):
    model = Counting()
    app = app_with(model)
    start(app)

    result = app.invoke(Command(resume=True), config=CONFIG)

    # Следующий этап отработал и упёрся в следующие ворота.
    assert model.calls == 2
    assert interrupt_of(result)["stage"] == "api"


def test_walking_every_gate_finishes_the_pipeline(gates_on):
    model = Counting()
    app = app_with(model)

    result = start(app)
    while "__interrupt__" in result:
        result = app.invoke(Command(resume=True), config=CONFIG)

    assert model.calls == len(roles.ROLES)
    assert list(result["artifacts"]) == list(roles.KEYS)


def test_refusal_stops_the_pipeline_and_names_the_reason(gates_on):
    model = Counting()
    app = app_with(model)
    start(app)

    result = app.invoke(
        Command(resume={"decision": "rejected", "reason": "задача понята неверно"}),
        config=CONFIG,
    )

    assert model.calls == 1
    assert result["halt"]["stage"] == "requirements"
    assert "задача понята неверно" in result["messages"][-1].content
    assert "Не выполнены этапы" in result["messages"][-1].content


def test_refusal_still_publishes_what_is_ready(gates_on, monkeypatch: pytest.MonkeyPatch):
    """
    Недописанная документация полезнее пустой страницы, а причина остановки
    видна в самом документе: он собирается из состояния, включая сообщение
    ноды `halted`.
    """
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "1")
    app = app_with(Counting())
    start(app)

    result = app.invoke(
        Command(resume={"decision": "rejected", "reason": "нужны уточнения"}),
        config=CONFIG,
    )

    pages = result["publication"]["pages"]
    assert [page["role"] for page in pages] == ["requirements"]
    assert len(published_files()) == 1


def test_gate_stays_silent_when_the_previous_stage_produced_nothing(gates_on):
    """
    Подтверждать нечего: спрашивать оператора про пустой документ значит
    будить его зря. Конвейер идёт дальше и останавливается на следующих
    воротах, где документ уже есть.
    """
    model = Silent()
    result = start(app_with(model))

    assert model.calls == 2  # первые ворота пропустили, вторые остановили
    assert interrupt_of(result)["stage"] == "api"
