"""
Пауза оператора: остановить идущий конвейер, дописать и продолжить.

Ворота и подтверждение публикации — остановки запланированные: о них
договорились заранее, и случаются они в одном и том же месте. Пауза приходит
снаружи и посреди работы, поэтому проверять её надо в другом месте: не «умеет
ли граф остановиться», а «успевает ли он остановиться до следующего вызова
модели, доезжает ли дописанное до этого вызова и снимается ли заявка после
того, как её взяли».

Механика та же, что у остальных остановок: `interrupt()` замораживает тред на
чекпоинте, `Command(resume=...)` продолжает его с того же места — не с начала
и не с нового прогона.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from starlette.testclient import TestClient

from agent import api, pause, roles
from agent.graph import build_graph

THREAD = "pause-1"
CONFIG = {"configurable": {"thread_id": THREAD}}
TASK = "Спроектировать асинхронную выгрузку заказов."


def usage() -> dict:
    return {
        "token_usage": {
            "prompt_cache_hit_tokens": 100,
            "prompt_cache_miss_tokens": 10,
            "completion_tokens": 5,
        }
    }


class Recording:
    """Подделка модели, которая помнит, сколько раз её звали и с чем."""

    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[str] = []

    def invoke(self, messages: list) -> AIMessage:
        self.calls += 1
        # Указания оператора обязаны приезжать в КОНЕЦ запроса, поэтому
        # проверяется последнее сообщение, а не весь запрос целиком.
        self.prompts.append(str(messages[-1].content))
        return AIMessage(content=f"Документ этапа {self.calls}", response_metadata=usage())


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MEMORY_ENABLED", "0")
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")
    # Доска процессная и переживает тест: заявка, оставленная одним, обязана
    # исчезнуть до начала следующего.
    pause.board.cancel(THREAD)
    yield
    pause.board.cancel(THREAD)


def app_with(model):
    return build_graph(llm=model).compile(checkpointer=InMemorySaver())


def start(app):
    return app.invoke({"messages": [HumanMessage(TASK)]}, config=CONFIG)


def interrupt_of(result: dict) -> dict:
    return result["__interrupt__"][0].value


# --------------------------------------------------------------------------
# Заявки нет — паузы нет
# --------------------------------------------------------------------------
def test_pipeline_runs_through_when_nobody_asked_to_pause():
    """
    Остановка, о которой не просили, — это сломанный конвейер, а не
    осторожность. Узел смотрит на доску перед каждым вызовом модели, и цена
    пустой доски должна быть нулевой.
    """
    model = Recording()
    result = start(app_with(model))

    assert "__interrupt__" not in result
    assert model.calls == len(roles.ROLES)
    assert not result.get("notes")


def test_pause_is_addressed_to_its_own_thread():
    """Заявка на соседний тред этот прогон не трогает."""
    pause.board.request("another-thread")
    model = Recording()
    result = start(app_with(model))

    assert "__interrupt__" not in result
    assert model.calls == len(roles.ROLES)
    pause.board.cancel("another-thread")


# --------------------------------------------------------------------------
# Заявка есть
# --------------------------------------------------------------------------
def test_pause_stops_the_pipeline_before_the_next_call():
    """
    Пауза берётся ДО обращения к модели, а не после. Смысл в этом и есть:
    дописанное оператором должно попасть в следующий запрос, а не в
    следующий прогон.
    """
    pause.board.request(THREAD)
    model = Recording()
    result = start(app_with(model))

    assert model.calls == 0  # ни один этап не оплачен
    payload = interrupt_of(result)
    assert payload["action"] == "pause"
    assert payload["stage"] == roles.PIPELINE.first.key
    assert payload["pending"] == [role.title for role in roles.ROLES]


def test_pause_between_stages_keeps_what_is_already_written():
    """Заявка, оставленная во время прогона, останавливает на границе этапов."""
    model = Recording()
    app = app_with(model)
    # Первый этап уже написан: останавливаемся между ним и вторым.
    start(app)
    pause.board.request(THREAD)
    app.invoke({"messages": [HumanMessage("Продолжай.")]}, config=CONFIG)

    payload = interrupt_of(app.invoke(None, config=CONFIG))
    assert payload["action"] == "pause"
    assert payload["done"]


def test_addition_reaches_the_next_call_and_the_state():
    """
    Дописанное оператором приезжает в конец сообщения — и роли, перед которой
    остановились, и всем следующим. Это и есть «продолжить с добавкой»: иначе
    указание чинило бы один этап и терялось на следующем.
    """
    pause.board.request(THREAD)
    model = Recording()
    app = app_with(model)
    start(app)

    result = app.invoke(
        Command(resume={"decision": "continue", "note": "Считать только рублёвые заказы."}),
        config=CONFIG,
    )

    assert model.calls == len(roles.ROLES)
    assert all("Считать только рублёвые заказы." in prompt for prompt in model.prompts)
    assert [note["text"] for note in result["notes"]] == ["Считать только рублёвые заказы."]
    assert result["notes"][0]["stage"] == roles.PIPELINE.first.key


def test_empty_answer_just_continues():
    """Передумал дописывать — конвейер идёт дальше, как будто паузы не было."""
    pause.board.request(THREAD)
    model = Recording()
    app = app_with(model)
    start(app)

    result = app.invoke(Command(resume={"decision": "continue"}), config=CONFIG)

    assert model.calls == len(roles.ROLES)
    assert not result.get("notes")


def test_taken_pause_does_not_repeat_itself():
    """
    Заявка одноразовая. Иначе конвейер вставал бы перед каждым следующим
    этапом, а оператор просил остановиться один раз.
    """
    pause.board.request(THREAD)
    model = Recording()
    app = app_with(model)
    start(app)

    result = app.invoke(Command(resume={"decision": "continue"}), config=CONFIG)

    assert "__interrupt__" not in result
    assert not pause.board.pending(THREAD)


def test_stop_on_pause_halts_the_pipeline_and_publishes_what_is_ready(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    Остановка с паузы — это та же остановка оператором, что и отказ на
    воротах: оставшиеся роли не оплачиваются, готовые документы публикуются.
    Разница одна и она в словах: ворота останавливают ПОСЛЕ показанного
    этапа, пауза — ПЕРЕД этапом, которого ещё не было.
    """
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "1")
    model = Recording()
    app = app_with(model)
    start(app)
    pause.board.request(THREAD)
    app.invoke({"messages": [HumanMessage("Продолжай.")]}, config=CONFIG)
    paid = model.calls

    result = app.invoke(
        Command(resume={"decision": "stop", "note": "задача понята неверно"}),
        config=CONFIG,
    )

    assert model.calls == paid  # ни одного лишнего вызова после остановки
    assert result["halt"]["point"] == "before"
    assert "задача понята неверно" in result["messages"][-1].content
    assert "на паузе перед этапом" in result["messages"][-1].content
    assert result["publication"]["pages"]


# --------------------------------------------------------------------------
# Доска заявок
# --------------------------------------------------------------------------
def test_board_forgets_a_stale_request():
    """
    Заявку могли оставить и не дождаться: тред бросили, прогон не начали.
    Без срока такая заявка дожила бы в памяти процесса до перезапуска
    сервера и остановила бы совсем другой прогон через неделю.
    """
    board = pause.PauseBoard(ttl_seconds=0)
    board.request("t")

    assert board.pending("t") is False


def test_board_refuses_a_request_without_a_thread():
    """Пауза адресуется треду: остановить «вообще» нечего и некого."""
    with pytest.raises(ValueError):
        pause.PauseBoard().request("")


# --------------------------------------------------------------------------
# Роут заявки
# --------------------------------------------------------------------------
TOKEN = "test-only-auth-token-with-32-characters"


def pause_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("API_ADMIN_TOKEN", TOKEN)
    return TestClient(api.app, headers={"Authorization": f"Bearer {TOKEN}"})


def test_pause_route_leaves_and_removes_the_request(monkeypatch: pytest.MonkeyPatch):
    """POST просит паузу, GET о ней отчитывается, DELETE снимает."""
    client = pause_client(monkeypatch)

    left = client.post("/api/ui/pause", json={"thread_id": THREAD})
    seen = client.get("/api/ui/pause", params={"thread_id": THREAD})
    removed = client.request("DELETE", "/api/ui/pause", json={"thread_id": THREAD})

    assert left.json()["pending"] is True
    assert seen.json()["pending"] is True
    assert removed.json()["pending"] is False
    assert pause.board.pending(THREAD) is False


def test_pause_route_needs_a_thread(monkeypatch: pytest.MonkeyPatch):
    """Остановить «вообще» нечего: заявка адресуется треду."""
    client = pause_client(monkeypatch)

    response = client.post("/api/ui/pause", json={})

    assert response.status_code == 400
    assert response.json()["error_code"] == "ui_pause_context_missing"


def test_pause_route_is_closed_without_the_admin_token(monkeypatch: pytest.MonkeyPatch):
    """
    Заявка останавливает чужую работу, и роут у неё такой же закрытый, как
    у всех остальных: без токена — 401, без заявки.
    """
    monkeypatch.setenv("API_ADMIN_TOKEN", TOKEN)
    response = TestClient(api.app).post("/api/ui/pause", json={"thread_id": THREAD})

    assert response.status_code == 401
    assert pause.board.pending(THREAD) is False


def test_answer_forms_mean_the_same_thing():
    """
    На паузу отвечают не только кнопкой: из Studio и из теста продолжают
    голым `Command(resume=...)`, и строка там означает приписку, а не отказ.
    """
    assert pause.decision_of("допиши раздел про ретраи") == {
        "decision": "continue",
        "note": "допиши раздел про ретраи",
    }
    assert pause.decision_of(True)["decision"] == "continue"
    assert pause.decision_of(False)["decision"] == "stop"
    assert pause.decision_of({"decision": "stop"})["decision"] == "stop"
