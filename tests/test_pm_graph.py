"""
Конвейер «Проджект-менеджер» на подделке вместо модели и вместо чтения доски.

Проверяется четыре вещи. Доска — вход, и выбирать её за оператора нельзя:
не названа — отказ до первого вызова модели. Таблицы считает код и кладёт
ролям до их вызова, и каждая следующая роль видит документы предыдущих.
Следующий ход с другой ёмкостью пересчитывает план по прочитанному снимку,
не перечитывая Jira. Отказ чтения доски доходит до ролей запиской, а не
пустотой, которую можно принять за «всё хорошо».
"""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from test_pm import NOW, snapshot

from agent import jira, pm_graph, pm_jira, pm_prompts, pm_roles


def answer(text: str = "## Коротко\n\nСпринт в графике.") -> AIMessage:
    return AIMessage(
        content=text,
        response_metadata={"token_usage": {"prompt_cache_hit_tokens": 100,
                                           "prompt_cache_miss_tokens": 10,
                                           "completion_tokens": 20}},
    )


def three() -> list[AIMessage]:
    return [answer("## Коротко\n\nСпринт в графике."), answer("## Главное сейчас\n\n1. N-1."),
            answer("## Предлагаемая цель\n\nДовести оплату частями.")]


class Recorder:
    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.fail = fail

    def __call__(self, board_id, *, who="", store_ok=True, now=None):
        self.calls.append({"board_id": board_id, "who": who, "store_ok": store_ok})
        if self.fail:
            raise self.fail
        found = snapshot()
        found["board"] = {**found["board"], "board_id": board_id}
        found["read_at"] = NOW.isoformat()
        return found


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")
    monkeypatch.setenv("JIRA_BASE_URL", "https://jira.test")
    monkeypatch.setenv("JIRA_TOKEN", "t")


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    found = Recorder()
    monkeypatch.setattr(pm_jira, "collect", found)
    return found


def run(question: str, *answers, conf: dict | None = None) -> dict:
    app = pm_graph.build_graph(llm=GenericFakeChatModel(messages=iter(answers))).compile()
    return app.invoke({"messages": [HumanMessage(question)]},
                      config=conf or {"configurable": {"thread_id": "pm-1"}})


# --------------------------------------------------------------------------
# Разбор запроса
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "value"),
    [("доска 42, ёмкость 30", 30.0), ("емкость следующего спринта — 24,5 SP", 24.5),
     ("capacity: 18", 18.0), ("ёмкость команды на спринт 26", 26.0),
     ("ёмкость 10000", 10000.0), ("ёмкость по скорости", None), ("доска 42", None),
     # Чужое число после произвольного текста, знак, доля и номер версии — не ёмкость.
     ("оцени ёмкость и план релиза 2.4", None), ("ёмкость -5", None), ("ёмкость — -5", None),
     ("ёмкость 30%", None), ("ёмкость 2.4.1", None), ("ёмкость: 0", None)],
)
def test_capacity_is_read_from_the_request(text, value):
    assert pm_roles.capacity_in(text) == value


@pytest.mark.parametrize(
    ("text", "share"),
    [("доступность 80%", 0.8), ("20% команды в отпуске", 0.8), ("10 % на больничном", 0.9),
     ("доступность команды 75 %", 0.75), ("доступность 180%", None), ("доска 42", None),
     ("120% в отпуске", None), ("доступность -80%", None),
     ("доступность и релиз 2.4 через 30%", None)],
)
def test_availability_is_read_from_the_request(text, share):
    assert pm_roles.availability_in(text) == share


def test_the_roles_share_one_stable_prefix():
    prefixes = [pm_prompts.for_role(role.key) for role in pm_roles.ROLES]

    assert all(prefix.startswith(pm_prompts.COMMON) for prefix in prefixes)
    assert len(set(prefixes)) == 3
    with pytest.raises(KeyError):
        pm_prompts.for_role("нет такой")


# --------------------------------------------------------------------------
# Проверка входа
# --------------------------------------------------------------------------
def test_no_board_no_run_and_no_money(recorder, monkeypatch):
    """Модели не передано ответов: обращение к ней уронило бы подделку."""
    monkeypatch.setenv("FLOW_BOARDS", "42")

    state = run("Что брать в следующий спринт?")

    message = state["messages"][-1].content
    assert "Не названа доска Jira" in message
    # Настроенная доска подсказывается, но не берётся сама.
    assert "42" in message
    assert recorder.calls == []
    assert not state.get("usage")


def test_two_boards_are_refused_rather_than_one_picked(recorder):
    state = run("Спланируй доски 4 и доску 5")

    assert "несколько досок" in state["messages"][-1].content
    assert recorder.calls == []


def test_without_jira_the_board_cannot_be_read(recorder, monkeypatch):
    monkeypatch.delenv("JIRA_TOKEN")

    state = run("Доска 42: где мы?")

    assert "Читать доску нечем" in state["messages"][-1].content
    assert recorder.calls == []


# --------------------------------------------------------------------------
# Прогон
# --------------------------------------------------------------------------
def test_the_tables_reach_every_role_and_each_role_sees_the_previous(recorder):
    state = run("Доска 42: где мы и что брать в следующий спринт?", *three())

    assert recorder.calls == [{"board_id": 42, "who": "", "store_ok": True}]
    table = state["artifacts"][pm_roles.BOARD]
    assert "## План спринта «S-31»" in table and "[N-1](https://jira.test/browse/N-1)" in table
    assert [key for key in ("status", "priorities", "plan") if state["artifacts"].get(key)] == [
        "status", "priorities", "plan",
    ]
    plan_brief = pm_roles.brief(pm_roles.BY_KEY["plan"], "Доска 42", state["artifacts"])
    assert table in plan_brief
    assert "# Результат этапа 01. Где мы сейчас\n\n## Коротко" in plan_brief
    assert "# Результат этапа 02. Приоритеты\n\n## Главное сейчас" in plan_brief
    assert state["pm"]["read"] is True and state["pm"]["capacity"] is None
    assert state["pm_view"]["Ёмкость"] == "22 SP, медиана за 6 посл. спринтов"
    assert "Доска 42 «Платежи», прочитана 02.10.2026 12:00 UTC." == pm_roles.subject(state)


def test_a_missing_stage_is_named_rather_than_left_empty():
    brief = pm_roles.brief(pm_roles.BY_KEY["plan"], "Доска 42", {pm_roles.BOARD: "таблицы"})

    assert "Этап не выполнен, документа нет" in brief
    assert "Доска не прочитана" in pm_roles.brief(pm_roles.ROLES[0], "Доска 42", {})


def test_blocked_statuses_of_the_team_reach_the_plan(recorder, monkeypatch):
    monkeypatch.setenv("FLOW_BLOCKED_STATUSES", "Blocked")
    original = Recorder.__call__

    def with_blocked(self, board_id, **kwargs):
        found = original(self, board_id, **kwargs)
        found["items"] = [{**item, "status": "Blocked"} if item["key"] == "N-1" else item
                          for item in found["items"]]
        return found

    monkeypatch.setattr(Recorder, "__call__", with_blocked)

    state = run("Доска 42", *three())

    assert "вынести: заблокирована (статус «Blocked»)" in state["artifacts"][pm_roles.BOARD]


def test_a_named_capacity_reaches_the_plan(recorder):
    state = run("Доска 42, ёмкость 30", *three())

    assert state["pm"]["capacity"] == 30.0
    assert state["pm_view"]["Ёмкость"] == "30 SP, названа в запросе"
    assert "Ёмкость названа оператором: 30." in pm_roles.subject(state)


def test_a_follow_up_recomputes_the_plan_without_reading_jira(recorder):
    app = pm_graph.build_graph(
        llm=GenericFakeChatModel(messages=iter(three() * 6))
    ).compile(checkpointer=InMemorySaver())
    conf = {"configurable": {"thread_id": "pm-2"}}

    app.invoke({"messages": [HumanMessage("Доска 42: что брать в спринт?")]}, config=conf)
    first = app.get_state(conf).values["artifacts"][pm_roles.BOARD]

    state = app.invoke({"messages": [HumanMessage("а если ёмкость 30?")]}, config=conf)
    assert len(recorder.calls) == 1
    assert state["pm"]["capacity"] == 30.0
    assert state["artifacts"][pm_roles.BOARD] != first
    assert "Ёмкость | 30 SP |" in state["artifacts"][pm_roles.BOARD]

    # Ёмкость треда держится, пока её не назвали заново.
    state = app.invoke({"messages": [HumanMessage("перепиши план короче")]}, config=conf)
    assert len(recorder.calls) == 1 and state["pm"]["capacity"] == 30.0

    state = app.invoke({"messages": [HumanMessage("ёмкость по скорости, 20% в отпуске")]},
                       config=conf)
    # Сброс сильнее: «по скорости» возвращает медиану без поправок.
    assert state["pm"]["capacity"] is None and state["pm"]["availability"] is None

    app.invoke({"messages": [HumanMessage("обнови данные")]}, config=conf)
    state = app.invoke({"messages": [HumanMessage("а теперь доска 57")]}, config=conf)
    assert [call["board_id"] for call in recorder.calls] == [42, 42, 57]
    assert state["pm"]["board_id"] == 57


def test_a_named_capacity_stays_with_its_board(recorder):
    app = pm_graph.build_graph(
        llm=GenericFakeChatModel(messages=iter(three() * 2))
    ).compile(checkpointer=InMemorySaver())
    conf = {"configurable": {"thread_id": "pm-4"}}

    app.invoke({"messages": [HumanMessage("Доска 42, 20% команды в отпуске")]}, config=conf)
    state = app.invoke({"messages": [HumanMessage("а теперь доска 57")]}, config=conf)

    assert state["pm"]["board_id"] == 57
    assert state["pm"]["availability"] is None and state["pm"]["capacity"] is None


def test_a_nested_run_does_not_write_to_the_database(recorder):
    run("Доска 42", *three(), conf={"configurable": {"thread_id": "pm-3", "nested": True}})

    assert recorder.calls[0]["store_ok"] is False


def test_a_failed_read_is_said_to_the_roles_instead_of_numbers(monkeypatch):
    failing = Recorder(fail=jira.JiraError("GET /rest/agile/1.0/board/42: объект не найден (HTTP 404)"))
    monkeypatch.setattr(pm_jira, "collect", failing)

    state = run("Доска 42", *three())

    assert state["pm"]["read"] is False
    assert "HTTP 404" in state["pm_view"]["Не прочитано"]
    assert "Доска не прочитана" in state["artifacts"][pm_roles.BOARD]
    assert "HTTP 404" in state["messages"][1].content
