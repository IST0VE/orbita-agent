from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage

from agent import confluence, jira, materials, nodes, roles, sources
from agent.builder import build_graph


def test_analysis_reads_page_before_first_model_call(monkeypatch):
    monkeypatch.setenv("PUBLISH_TARGET", "file")
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://wiki.example.com")
    monkeypatch.setattr(confluence, "missing_vars", lambda: [])
    fetched = []

    def fetch(key):
        fetched.append(key)
        return {
            "id": key,
            "title": "Contract",
            "text": "Unique source requirement",
            "url": "https://wiki.example.com/pages/12345",
            "truncated": False,
        }

    monkeypatch.setattr(confluence, "fetch_page", fetch)
    calls = []

    class Model:
        def invoke(self, messages):
            calls.append(messages)
            return AIMessage(content="Validated analysis")

    result = build_graph(llm=Model()).compile().invoke(
        {"messages": [HumanMessage(content="Аналитика по https://wiki.example.com/pages/12345")]}
    )
    assert fetched == ["12345"]
    assert "Unique source requirement" in str(calls[0])
    assert "[WIKI 12345] Contract" in str(calls[0])
    # Ревьюер видит тот же первоисточник, что и аналитик; проектировщики — нет.
    assert "Unique source requirement" in str(calls[-1])
    assert all("Unique source requirement" not in str(call) for call in calls[1:-1])
    assert result["materials"]["links"][0]["id"] == "12345"
    assert "[WIKI 12345]" in result["artifacts"][roles.SOURCES]
    assert {t.name for t in roles.PIPELINE.tools} >= {
        "confluence_search",
        "confluence_page",
        "jira_issue",
    }


def test_mixed_links_and_bare_key_are_read_once_and_foreign_host_is_rejected(monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://jira.example.com")
    monkeypatch.setattr(jira, "missing_vars", lambda: [])
    fetched = []
    monkeypatch.setattr(jira, "fetch_issue", lambda key: fetched.append(key) or {"key": key})
    monkeypatch.setattr(jira, "format_issue", lambda issue: "Ticket " + issue["key"])
    items = sources.linked(
        "https://jira.example.com/browse/ORB-12 ORB-12 https://foreign.example.com/browse/ORB-13"
    )
    block = roles.links_block(items)
    assert fetched == ["ORB-12"]
    assert "Ticket ORB-12" in block
    assert "домен не совпадает" in block
    assert [(item["id"], bool(item.get("error"))) for item in items] == [
        ("ORB-12", False),
        ("ORB-13", True),
    ]


def test_unavailable_source_is_visible_and_naming_it_again_changes_nothing(monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://jira.example.com")
    monkeypatch.setattr(jira, "missing_vars", lambda: ["JIRA_TOKEN"])
    state = {"messages": [HumanMessage(content="ORB-12", id="m")]}
    result = materials.materials_node(state, {})
    assert "не настроена" in result["artifacts"][roles.LINKS]
    assert "Не прочитаны: [JIRA ORB-12]" in result["messages"][0].content
    # Названная снова, она пробуется ещё раз; трекер по-прежнему не настроен —
    # в сеть узел не ходит, блок не дублируется, и обновлять нечего. Удачная
    # повторная попытка — в `test_review_2026_09_28`.
    monkeypatch.setattr(jira, "fetch_issue", lambda key: (_ for _ in ()).throw(AssertionError))
    again = materials.materials_node(
        {**state, "materials": result["materials"], "artifacts": result["artifacts"]}, {}
    )
    assert again == {}


def test_context_node_no_longer_reads_links(monkeypatch):
    """Ссылки читает прелюдия; нода контекста в сеть за ними не ходит."""
    monkeypatch.setattr(jira, "fetch_issue", lambda key: (_ for _ in ()).throw(AssertionError))
    result = nodes.context_node({"messages": [HumanMessage(content="ORB-12", id="m")]}, {})
    assert result["task"] == "ORB-12"
    assert "messages" not in result


def test_analysis_can_search_then_read_confluence_before_writing(monkeypatch):
    monkeypatch.setattr(confluence, "missing_vars", lambda: [])
    searched = []
    read = []
    monkeypatch.setattr(confluence, "search", lambda query, **kwargs: searched.append(query) or [
        {"id": "12345", "title": "Payments", "url": "https://wiki.example.com/pages/12345", "excerpt": "Payments"}
    ])
    monkeypatch.setattr(confluence, "fetch_page", lambda key: read.append(key) or {
        "title": "Payments", "url": "https://wiki.example.com/pages/12345",
        "text": "Payments source evidence", "truncated": False,
    })
    model = GenericFakeChatModel(messages=iter([
        AIMessage(content="", tool_calls=[{"id": "search", "name": "confluence_search", "args": {"query": "payments"}}]),
        AIMessage(content="", tool_calls=[{"id": "read", "name": "confluence_page", "args": {"page_id": "12345"}}]),
        *[AIMessage(content="Analysis based on Payments") for _ in roles.ROLES],
    ]))
    result = build_graph(llm=model).compile().invoke({"messages": [HumanMessage(
        content="Найди документацию по платежам в Confluence и подготовь аналитику."
    )]})
    assert searched == ["payments"]
    assert read == ["12345"]
    assert "Payments source evidence" in str(result["messages"])
    assert all(key in result["artifacts"] for key in roles.KEYS)
    # Реестр собран кодом по следам инструментов: прочитанная страница и запрос.
    registry = result["artifacts"][roles.SOURCES]
    assert "[WIKI 12345]" in registry and "payments" in registry
