"""
Подтверждение оператором перед публикацией.

Для проектной документации это естественный сценарий: документы уезжают на
общую wiki, и решение «показывать это всем» принимает человек. LangGraph умеет такое
из коробки — `interrupt()` замораживает тред, `Command(resume=...)` его
продолжает, — поэтому кода здесь мало, а проверять надо ровно две вещи: что до
подтверждения не публикуется ничего и что отказ действительно отказ.

Цель публикации в этих тестах — файл на диске (PUBLISH_DIR ведёт в tmp_path):
факт публикации виден как существующий файл, без подмены транспорта.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent import config as cfg
from agent import roles
from agent.graph import approval_of, build_graph

CONFIG = {"configurable": {"thread_id": "t-1"}}
QUESTION = "Что отвечать клиенту про экспорт?"

ANSWER = AIMessage(
    content="Экспорт больше 100000 строк приходит на почту.",
    response_metadata={
        "token_usage": {
            "prompt_cache_hit_tokens": 100,
            "prompt_cache_miss_tokens": 10,
            "completion_tokens": 5,
        }
    },
)


class Once:
    """Одна заготовка на все вызовы: проверяется публикация, а не тексты ролей."""

    def invoke(self, messages: list) -> AIMessage:
        return ANSWER


@pytest.fixture(autouse=True)
def approval_required(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PUBLISH_REQUIRE_APPROVAL", "1")
    monkeypatch.setenv("MEMORY_ENABLED", "0")


@pytest.fixture
def app():
    return build_graph(llm=Once()).compile(checkpointer=InMemorySaver())


def published_files() -> list[Path]:
    directory = Path(cfg.publish_dir())
    return sorted(directory.glob("*.md")) if directory.exists() else []


def start(app) -> dict:
    return app.invoke({"messages": [HumanMessage(QUESTION)]}, config=CONFIG)


# --------------------------------------------------------------------------
# Остановка
# --------------------------------------------------------------------------
def test_thread_stops_before_publishing_and_shows_the_document(app):
    result = start(app)

    payload = result["__interrupt__"][0].value
    assert payload["action"] == "publish"
    assert payload["target"] == "file"
    assert payload["title"].startswith("Orbita: ")
    assert "Экспорт больше 100000 строк" in payload["document"]
    assert published_files() == []


def test_interrupt_names_the_markup_of_the_document(app):
    """
    Разметку выбирает цель публикации, и payload обязан её называть: иначе
    интерфейсу пришлось бы знать, какой публикатор что рисует, и угадывать
    формат по имени цели.
    """
    payload = start(app)["__interrupt__"][0].value

    assert payload["format"] == "markdown"
    assert "<p>" not in payload["document"]


def test_nothing_is_published_until_the_operator_answers(app):
    start(app)
    assert published_files() == []


# --------------------------------------------------------------------------
# Черновики
#
# Сводка отвечает на вопрос «сколько», а решение принимается по вопросу «что
# именно». Поэтому остановка обязана показать каждую будущую страницу телом,
# в котором она уедет, и назвать её судьбу: создастся или перезапишет чужую.
# --------------------------------------------------------------------------
def test_interrupt_shows_a_draft_per_future_page(app):
    payload = start(app)["__interrupt__"][0].value

    drafts = payload["drafts"]
    assert [draft["id"] for draft in drafts] == list(roles.KEYS)
    assert [draft["title"] for draft in drafts] == payload["pages"]
    assert all(draft["kind"] == "page" for draft in drafts)
    assert all(draft["format"] == "markdown" for draft in drafts)
    assert all("Экспорт больше 100000 строк" in draft["document"] for draft in drafts)


def test_draft_says_the_page_does_not_exist_yet(app):
    payload = start(app)["__interrupt__"][0].value

    assert {draft["action"] for draft in payload["drafts"]} == {"create"}
    assert published_files() == []


def test_draft_warns_that_an_existing_page_will_be_overwritten(app):
    """
    Перезапись — единственное необратимое, что делает публикация: версия уедет
    всем подписчикам пространства. Увидеть это оператор обязан до ответа.
    """
    start(app)
    app.invoke(Command(resume=True), config=CONFIG)
    existing = published_files()[0]

    app.invoke({"messages": [HumanMessage("Ещё раз")]}, config=CONFIG)
    payload = app.get_state(CONFIG).tasks[0].interrupts[0].value

    files = {
        Path(field["value"]).name
        for draft in payload["drafts"]
        if draft["action"] == "update"
        for field in draft["fields"]
        if field["label"] == "Файл"
    }
    assert existing.name in files


# --------------------------------------------------------------------------
# Возобновление
# --------------------------------------------------------------------------
def test_approval_publishes_the_page(app):
    start(app)

    result = app.invoke(Command(resume=True), config=CONFIG)

    assert result["publication"]["status"] == "created"
    # Страниц столько, сколько этапов: подтверждение одно на весь конвейер.
    assert len(published_files()) == len(roles.ROLES)
    assert [page["role"] for page in result["publication"]["pages"]] == list(roles.KEYS)


def test_native_drafts_resume_returns_editor_links_without_publishing(app, monkeypatch):
    from agent import confluence, publishers

    monkeypatch.setattr(publishers, "current", publishers.ConfluencePublisher)
    monkeypatch.setattr(confluence, "missing_vars", lambda: [])
    monkeypatch.setattr(publishers.ConfluencePublisher, "preview", lambda self, title: {"action": "create"})
    monkeypatch.setattr(confluence, "publish_page", lambda *args: pytest.fail("must not publish"))
    monkeypatch.setattr(confluence, "create_draft", lambda title, body, draft_key: {
        "status": "draft", "title": title, "url": "https://wiki.example.com/pages/resumedraft.action?draftId=12345"
    })
    payload = start(app)["__interrupt__"][0].value
    assert payload["target"] == "confluence"
    result = app.invoke(Command(resume={"decision": "drafts"}), config=CONFIG)
    assert result["approval"]["decision"] == "drafts"
    assert result["publication"]["status"] == "drafts"
    assert len(result["publication"]["pages"]) == len(roles.KEYS)
    assert not result.get("document_hash")
    assert published_files() == []


def test_refusal_leaves_the_document_in_the_state_only(app):
    start(app)

    result = app.invoke(
        Command(resume={"decision": "rejected", "reason": "персональные данные"}),
        config=CONFIG,
    )

    assert result["publication"]["status"] == "rejected"
    assert result["publication"]["reason"] == "персональные данные"
    assert published_files() == []
    assert "Экспорт больше 100000 строк" in result["document"]


def test_refusal_does_not_remember_the_document_as_published(app):
    """Хеш описывает то, что лежит на странице; отказ ничего туда не положил."""
    start(app)

    result = app.invoke(Command(resume=False), config=CONFIG)

    assert result.get("document_hash") is None


# --------------------------------------------------------------------------
# Когда не спрашивают
# --------------------------------------------------------------------------
def test_without_the_flag_the_thread_goes_straight_through(
    monkeypatch: pytest.MonkeyPatch, app
):
    monkeypatch.setenv("PUBLISH_REQUIRE_APPROVAL", "0")

    result = start(app)

    assert "__interrupt__" not in result
    assert result["publication"]["status"] == "created"


def test_asking_is_the_default(monkeypatch: pytest.MonkeyPatch):
    """
    Умолчание — спрашивать. Любой прогон, который что-то создаёт снаружи,
    останавливается на верификацию; выключают это там, где оператора нет.
    """
    monkeypatch.delenv("PUBLISH_REQUIRE_APPROVAL", raising=False)

    assert cfg.publish_require_approval() is True


def test_operator_is_not_asked_when_publication_would_not_happen_anyway(
    monkeypatch: pytest.MonkeyPatch, app
):
    """Дёргать человека ради этапа, который и так выключен, — плохая манера."""
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")

    result = start(app)

    assert "__interrupt__" not in result
    assert result["publication"]["status"] == "disabled"


# --------------------------------------------------------------------------
# Разбор ответа оператора
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("answer", "decision"),
    [
        (True, "approved"),
        (False, "rejected"),
        ("да", "approved"),
        ("approve", "approved"),
        ("нет", "rejected"),
        ({"approved": True}, "approved"),
        ({"decision": "rejected", "reason": "не сейчас"}, "rejected"),
        (None, "rejected"),
    ],
)
def test_operator_answers_are_understood(answer, decision: str):
    """Studio отдаёт JSON, чат — строку, свой код — булево: понимаем всё."""
    assert approval_of(answer)["decision"] == decision


def test_reason_survives_the_parsing():
    assert approval_of({"decision": False, "reason": "нельзя"})["reason"] == "нельзя"
