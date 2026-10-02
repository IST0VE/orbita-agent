"""
Конвейер «Метрики потока» на подделке вместо модели и вместо сбора доски.

Проверяется три вещи. Доска — вход, и выбирать её за оператора нельзя: не
названа — отказ до первого вызова модели. Числа считает код и кладёт роли
таблицами до её вызова. Следующий ход без новой доски и периода не собирает
доску заново — «перепиши короче» работает по уже посчитанному.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent import flow, flow_graph, flow_roles, flow_sync, jira

NOW = datetime.now(UTC)


def answer() -> AIMessage:
    return AIMessage(
        content="## Главное\n\n- Закрыто 3 задачи.",
        response_metadata={"token_usage": {"prompt_cache_hit_tokens": 100,
                                           "prompt_cache_miss_tokens": 10,
                                           "completion_tokens": 20}},
    )


def measured(board_id: int = 42, period: int = 90) -> flow_sync.Measured:
    rows = [
        {"key": f"PAY-{n}", "subtask": False, "orbita": n == 1, "category": flow.DONE,
         "done_at": NOW - timedelta(days=n * 5), "cycle_days": float(n), "lead_days": n + 1.0,
         "reopens": 0, "blocked_hours": 0.0, "analysis_returns": None}
        for n in range(1, 4)
    ]
    since = NOW - timedelta(days=period)
    current = flow.summary(rows, [], since=since, until=NOW, now=NOW, analysis_configured=False)
    return flow_sync.Measured(
        board={"board_id": board_id, "name": "Платежи", "hours": False, "synced_at": NOW},
        current=current, previous=None, notes=["Предыдущего периода нет."], stored=True,
    )


class Recorder:
    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.fail = fail

    def __call__(self, board_id, period, *, who="", store_ok=True, refresh=True):
        self.calls.append({"board_id": board_id, "period": period, "who": who, "store_ok": store_ok})
        if self.fail:
            raise self.fail
        return measured(board_id, period)


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")
    monkeypatch.setenv("JIRA_BASE_URL", "https://jira.test")
    monkeypatch.setenv("JIRA_TOKEN", "t")


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    found = Recorder()
    monkeypatch.setattr(flow_sync, "measure", found)
    return found


def run(question: str, *answers, conf: dict | None = None) -> dict:
    app = flow_graph.build_graph(llm=GenericFakeChatModel(messages=iter(answers))).compile()
    return app.invoke({"messages": [HumanMessage(question)]},
                      config=conf or {"configurable": {"thread_id": "m-1"}})


# --------------------------------------------------------------------------
# Разбор запроса
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "boards"),
    [
        ("Метрики доски 42 за квартал", [42]),
        ("board 7", [7]),
        ("доска №15", [15]),
        ("https://jira.test/secure/RapidBoard.jspa?rapidView=31&projectKey=PAY", [31]),
        ("https://jira.test/jira/software/projects/PAY/boards/9", [9]),
        ("сравни доски 4 и доску 5", [4, 5]),
        ("как у нас с потоком за 30 дней", []),
    ],
)
def test_boards_are_found_by_word_address_and_rapid_view(text, boards):
    assert flow_roles.boards_in(text) == boards


@pytest.mark.parametrize(
    ("text", "days"),
    [
        ("за квартал", 91), ("за последний месяц", 30), ("за полгода", 182),
        ("за 60 дней", 60), ("за 3 недели", 21), ("за 2 месяца", 60), ("за 900 дней", 365),
        ("доска 42", None),
    ],
)
def test_the_period_is_read_from_the_request(text, days):
    assert flow_roles.period_in(text) == days


# --------------------------------------------------------------------------
# Проверка входа
# --------------------------------------------------------------------------
def test_no_board_no_run_and_no_money(recorder, monkeypatch):
    """Модели не передано ответов: обращение к ней уронило бы подделку."""
    monkeypatch.setenv("FLOW_BOARDS", "42")

    state = run("Как у нас с потоком за квартал?")

    message = state["messages"][-1].content
    assert "Не названа доска Jira" in message
    # Настроенная доска подсказывается, но не берётся сама.
    assert "42" in message
    assert recorder.calls == []
    assert not state.get("usage")


def test_two_boards_are_refused_rather_than_one_picked(recorder):
    state = run("Метрики доски 4 и доски 5")

    assert "несколько досок" in state["messages"][-1].content
    assert recorder.calls == []


def test_without_jira_the_board_cannot_be_read(recorder, monkeypatch):
    monkeypatch.delenv("JIRA_TOKEN")

    state = run("Метрики доски 42")

    assert "Читать доску нечем" in state["messages"][-1].content
    assert recorder.calls == []


# --------------------------------------------------------------------------
# Прогон
# --------------------------------------------------------------------------
def test_the_numbers_reach_the_role_as_tables_before_it_speaks(recorder):
    state = run("Метрики доски 42 за 60 дней", answer())

    assert recorder.calls == [{"board_id": 42, "period": 60, "who": "", "store_ok": True}]
    table = state["artifacts"][flow_roles.FLOW]
    assert "Доска 42 «Платежи»" in table and "| Закрыто задач | 3 |" in table
    brief = flow_roles.brief(flow_roles.ROLES[0], "Метрики доски 42", state["artifacts"])
    assert table in brief
    assert state["artifacts"]["summary"].startswith("## Главное")
    assert state["flow"]["period"] == 60 and state["flow"]["read"] is True
    assert state["flow_view"]["Закрыто задач"] == 3
    assert "Доска 42 «Платежи», период 60 дн." in flow_roles.subject(state)


def test_a_missing_period_is_a_quarter_and_the_report_says_so(recorder):
    state = run("Метрики доски 42", answer())

    assert recorder.calls[0]["period"] == flow_roles.DEFAULT_PERIOD
    assert "Период в запросе не назван" in state["artifacts"][flow_roles.FLOW]


def test_a_follow_up_works_on_the_numbers_already_counted(recorder):
    app = flow_graph.build_graph(
        llm=GenericFakeChatModel(messages=iter([answer(), answer(), answer(), answer()]))
    ).compile(checkpointer=InMemorySaver())
    conf = {"configurable": {"thread_id": "m-2"}}

    app.invoke({"messages": [HumanMessage("Метрики доски 42 за квартал")]}, config=conf)
    app.invoke({"messages": [HumanMessage("перепиши сводку короче")]}, config=conf)
    assert len(recorder.calls) == 1

    app.invoke({"messages": [HumanMessage("обнови цифры")]}, config=conf)
    state = app.invoke({"messages": [HumanMessage("а теперь доска 57")]}, config=conf)
    assert [call["board_id"] for call in recorder.calls] == [42, 42, 57]
    # Период треда сохраняется, пока его не назвали заново.
    assert recorder.calls[-1]["period"] == 91
    assert state["flow"]["board_id"] == 57


def test_a_nested_run_does_not_write_to_the_database(recorder):
    run("Метрики доски 42", answer(), conf={"configurable": {"thread_id": "m-3", "nested": True}})

    assert recorder.calls[0]["store_ok"] is False


def test_a_failed_read_is_said_to_the_role_instead_of_numbers(monkeypatch):
    failing = Recorder(fail=jira.JiraError("GET /rest/agile/1.0/board/42: объект не найден (HTTP 404)"))
    monkeypatch.setattr(flow_sync, "measure", failing)

    state = run("Метрики доски 42", answer())

    assert state["flow"]["read"] is False
    assert "HTTP 404" in state["flow_view"]["Не посчитано"]
    assert "не посчитаны" in state["artifacts"][flow_roles.FLOW]
    assert "HTTP 404" in state["messages"][1].content
