"""
Находки ревью 28 сентября 2026 по переносу приёмов подготовки задачи на другие
конвейеры.

Материалы треда: файл, названный словами в первом запросе, пропадал на
следующем ходе; непрочитанная ссылка больше никогда не читалась; потолок
ссылок считал удачи, а не обращения в сеть; чужая ссылка вытесняла свою с тем
же ключом. Форма документа: сверка и декомпозиция на языке источника
переводились на русский, а корректные теги файлов со скобками ломались.
Задача треда теряла всё после Markdown-черты `---`. Производный конвейер
аналитики — так собирает граф smoke-проверка инструментов — не собирался.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent import (
    audit_roles,
    documents,
    inputs,
    jira,
    jira_roles,
    knowledge,
    materials,
    memory,
    nodes,
    roles,
    sources,
    tidy,
)
from agent.builder import build_graph
from agent.pipeline import Role

MEETING = "На встрече решили: выгрузка идёт в Parquet, срок хранения 30 дней."


class Recording:
    """Подделка модели: помнит вход каждой роли и отвечает документом этапа."""

    def __init__(self, *answers: str) -> None:
        self.calls: list[list] = []
        self.answers = list(answers)

    def invoke(self, messages: list) -> AIMessage:
        self.calls.append(messages)
        content = self.answers.pop(0) if self.answers else f"# Документ\n\nЭтап {len(self.calls)}."
        return AIMessage(content=content)

    def brief_of(self, index: int) -> str:
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
    return {"configurable": {"thread_id": "r-1", "input_dir": "задача", **extra}}


@pytest.fixture
def jira_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://jira.example.com")
    monkeypatch.setattr(jira, "missing_vars", lambda: [])


# --------------------------------------------------------------------------
# Материалы треда
# --------------------------------------------------------------------------
def test_a_file_named_in_the_first_request_stays_on_the_next_turn(folder):
    model = Recording()
    app = build_graph(llm=model).compile(checkpointer=InMemorySaver())
    conf = config()
    app.invoke({"messages": [HumanMessage("Спроектировать выгрузку по встреча.md")]}, config=conf)
    model.calls.clear()

    state = app.invoke({"messages": [HumanMessage("Поправь раздел про хранение.")]}, config=conf)

    assert MEETING in model.brief_of(0)
    assert MEETING in model.brief_of(len(roles.ROLES) - 1)
    assert state["materials"]["files"] == ["встреча.md"]
    assert "[ФАЙЛ встреча.md]" in state["artifacts"][roles.SOURCES]


def test_another_file_named_on_the_next_turn_replaces_the_remembered_one(folder):
    app = build_graph(llm=Recording()).compile(checkpointer=InMemorySaver())
    conf = config()
    app.invoke({"messages": [HumanMessage("Спроектировать выгрузку по встреча.md")]}, config=conf)

    state = app.invoke({"messages": [HumanMessage("Учти заметки.md")]}, config=conf)

    assert state["materials"]["files"] == ["заметки.md"]


def test_remembered_files_are_read_only_when_nothing_else_is_named(folder):
    conf = config()
    found, _ = sources.materials("поправь раздел", conf, remembered=["встреча.md"])
    assert found["names"] == ["встреча.md"]

    found, _ = sources.materials("по заметки.md", conf, remembered=["встреча.md"])
    assert found["names"] == ["заметки.md"]

    # Удалённый из чата файл не превращается в отказ всего набора.
    found, _ = sources.materials("поправь", conf, remembered=["удалён.md"])
    assert found is None


def test_a_failed_link_is_tried_again_when_it_is_named_again(jira_on, monkeypatch):
    calls: list[str] = []

    def fetch(key):
        calls.append(key)
        if len(calls) == 1:
            raise jira.JiraError("503 Service Unavailable")
        return {"key": key, "summary": "Выгрузка", "url": f"https://jira.example.com/browse/{key}"}

    monkeypatch.setattr(jira, "fetch_issue", fetch)
    monkeypatch.setattr(jira, "format_issue", lambda issue: "Текст задачи " + issue["key"])
    first = materials.materials_node({"messages": [HumanMessage("ORB-12")]}, {})
    assert "503" in first["artifacts"][roles.LINKS]

    again = materials.materials_node(
        {
            "messages": [HumanMessage("Ещё раз ORB-12")],
            "materials": first["materials"],
            "artifacts": first["artifacts"],
        },
        {},
    )

    assert calls == ["ORB-12", "ORB-12"]
    block = again["artifacts"][roles.LINKS]
    assert "Текст задачи ORB-12" in block
    assert "503" not in block
    assert [item.get("error", "") for item in again["materials"]["links"]] == [""]

    # Прочитанная ссылка третий раз в сеть не ходит и ничего не меняет.
    third = materials.materials_node(
        {
            "messages": [HumanMessage("ORB-12")],
            "materials": again["materials"],
            "artifacts": {**first["artifacts"], **again["artifacts"]},
        },
        {},
    )
    assert calls == ["ORB-12", "ORB-12"]
    assert third == {}


def test_the_link_ceiling_counts_attempts_not_successes(jira_on, monkeypatch):
    """Двадцать недоступных задач — не двадцать обращений в сеть."""
    calls: list[str] = []

    def fetch(key):
        calls.append(key)
        raise jira.JiraError("таймаут")

    monkeypatch.setattr(jira, "fetch_issue", fetch)

    items = sources.linked(" ".join(f"ORB-{number}" for number in range(1, 21)))

    assert len(calls) == sources.LINKED_MAX
    assert len(items) == 20
    limited = [item for item in items if item["error"] == "достигнут лимит контекста"]
    assert len(limited) == 20 - sources.LINKED_MAX


def test_a_foreign_link_does_not_take_the_place_of_ours(jira_on, monkeypatch):
    monkeypatch.setattr(jira, "fetch_issue", lambda key: {"key": key, "summary": "Своя"})
    monkeypatch.setattr(jira, "format_issue", lambda issue: "Своя задача")

    items = sources.linked(
        "https://foreign.example.com/browse/ORB-12 https://jira.example.com/browse/ORB-12"
    )

    assert len(items) == 1
    assert not items[0].get("error")
    assert items[0]["text"] == "Своя задача"
    assert items[0]["url"] == "https://jira.example.com/browse/ORB-12"


def test_a_bare_key_also_wins_over_a_foreign_link(jira_on, monkeypatch):
    monkeypatch.setattr(jira, "fetch_issue", lambda key: {"key": key})
    monkeypatch.setattr(jira, "format_issue", lambda issue: "Своя задача")

    [item] = sources.linked("https://foreign.example.com/browse/ORB-12 и ORB-12")

    assert not item.get("error")


def test_a_moved_issue_is_recognised_by_the_key_the_operator_used(jira_on, monkeypatch):
    """ORB-12 перенесли, и Jira отвечает NEW-5. Названная снова, она не читается дважды."""
    calls: list[str] = []

    def fetch(key):
        calls.append(key)
        return {"key": "NEW-5", "summary": "Выгрузка"}

    monkeypatch.setattr(jira, "fetch_issue", fetch)
    monkeypatch.setattr(jira, "format_issue", lambda issue: "Текст NEW-5")
    first = materials.materials_node({"messages": [HumanMessage("ORB-12")]}, {})

    again = materials.materials_node(
        {
            "messages": [HumanMessage("Поправь с учётом ORB-12")],
            "materials": first["materials"],
            "artifacts": first["artifacts"],
        },
        {},
    )

    assert calls == ["ORB-12"]
    assert again == {}
    assert first["artifacts"][roles.LINKS].count("Текст NEW-5") == 1


def test_a_thread_started_before_the_prelude_keeps_its_materials(folder, jira_on, monkeypatch):
    """
    У старого треда сводки нет, а ссылки и имена файлов — в задаче треда.
    Первое «поправь раздел» после обновления обязано их прочитать.
    """
    monkeypatch.setattr(jira, "fetch_issue", lambda key: {"key": key, "summary": "Выгрузка"})
    monkeypatch.setattr(jira, "format_issue", lambda issue: "Текст задачи " + issue["key"])
    state = {
        "task": "Спроектировать выгрузку по ORB-12 и встреча.md",
        "artifacts": {"requirements": "# Требования"},
        "messages": [HumanMessage("Поправь раздел про хранение.")],
    }

    update = materials.materials_node(state, config())

    assert "Текст задачи ORB-12" in update["artifacts"][roles.LINKS]
    assert MEETING in update["artifacts"][roles.FILES]
    assert update["materials"]["files"] == ["встреча.md"]


# --------------------------------------------------------------------------
# Форма документа
# --------------------------------------------------------------------------
CHINESE = "# 结论\n\n| 编号 | 状态 |\n| --- | --- |\n| R1 | 已覆盖 | 备注 |"


@pytest.mark.parametrize(
    "role,pipeline",
    [
        (audit_roles.BY_KEY["verdict"], audit_roles.PIPELINE),
        (jira_roles.BY_KEY["scope"], jira_roles.PIPELINE),
    ],
)
def test_a_report_in_the_language_of_its_source_is_not_translated(role, pipeline):
    """
    Сверка и декомпозиция пишут на языке источника: китайский пакет — вход, а
    не сбой. Таблица при этом выравнивается, а платной правки языка нет.
    """
    model = Recording(CHINESE)
    node = nodes.make_role_node(role, llm=model, pipeline=pipeline)

    update = node({"messages": [HumanMessage("проверь")], "artifacts": {}}, {})

    assert len(model.calls) == 1
    assert "| 编号 | 状态 |  |" in update["artifacts"][role.key]
    assert "已覆盖" in update["artifacts"][role.key]


def test_a_russian_pipeline_still_repairs_a_foreign_line():
    broken = "# Контракт API\n\nСделать服务端-часть."
    model = Recording(broken, '["Сделать серверную часть."]')
    node = nodes.make_role_node(roles.BY_KEY["api"], llm=model, pipeline=roles.PIPELINE)

    update = node({"messages": [HumanMessage("задача")], "artifacts": {}}, {})

    assert len(model.calls) == 2
    assert update["artifacts"]["api"] == "# Контракт API\n\nСделать серверную часть."


def test_a_quotation_keeps_its_language():
    assert tidy.foreign_lines("Пакет пишет: «服务端 отвечает 200».") == []


@pytest.mark.parametrize(
    "text",
    [
        "[ФАЙЛ протокол (v1.2).md]",
        "[ФАЙЛ отчёт (финал.v2).md] и [WIKI 1]",
        "[ФАЙЛ a.md] (см. b.txt)",
    ],
)
def test_a_correct_file_tag_with_brackets_in_the_name_is_left_alone(text):
    assert tidy.fix_tags(text) == text


@pytest.mark.parametrize(
    "broken,fixed",
    [
        ("[ФАЙЛ запись (ч.1).txt) далее", "[ФАЙЛ запись (ч.1).txt] далее"),
        ("[ФАЙЛ speech_to_text (9).doc.txt) и", "[ФАЙЛ speech_to_text (9).doc.txt] и"),
        ("[ФАЙЛ a.md) текст (см. b.txt)", "[ФАЙЛ a.md] текст (см. b.txt)"),
        ("[ФАЙЛ a.md) и [ФАЙЛ b.txt)", "[ФАЙЛ a.md] и [ФАЙЛ b.txt]"),
    ],
)
def test_a_file_tag_closed_with_a_paren_is_closed_where_the_name_ends(broken, fixed):
    assert tidy.fix_tags(broken) == fixed


# --------------------------------------------------------------------------
# Разделитель в тексте оператора
# --------------------------------------------------------------------------
def test_a_markdown_rule_in_the_request_does_not_cut_the_task():
    request = "Спроектировать выгрузку.\n\n---\nОграничения: только рублёвые заказы."
    model = Recording()

    state = build_graph(llm=model).compile().invoke({"messages": [HumanMessage(request)]})

    assert state["task"] == request
    assert "только рублёвые заказы" in model.brief_of(0)


def test_only_the_blocks_written_by_code_are_cut_from_the_question():
    question = "Часть 1\n\n---\nЧасть 2"
    added = f"{question}\n\n---\n{inputs.BLOCK_TITLE} «задача» (подставлен автоматически):\n\n- a.md"

    assert documents.operator_question(added) == question
    assert documents.has_context(added)
    assert not documents.has_context(f"{inputs.BLOCK_TITLE} лежат в чате")


def test_the_context_titles_match_the_blocks_they_cut():
    assert knowledge.BLOCK_TITLE in documents.CONTEXT_TITLES
    assert f"### {memory.BLOCK_TITLE}" in documents.CONTEXT_TITLES
    assert inputs.BLOCK_TITLE in documents.CONTEXT_TITLES


def test_a_request_naming_the_files_block_still_gets_its_context(folder):
    """Слова «Файлы задачи» в запросе — вопрос оператора, а не подставленный список."""
    request = f"{inputs.BLOCK_TITLE} лежат в чате, разбери их."

    result = nodes.context_node({"messages": [HumanMessage(request, id="m")]}, config())

    assert result["task"] == request
    assert "встреча.md" in result["messages"][0].content


# --------------------------------------------------------------------------
# Производные конвейеры
# --------------------------------------------------------------------------
def test_a_pipeline_derived_from_the_analysis_one_still_builds():
    """Так собирает граф `scripts/smoke_tool_compat.py`."""
    probe = Role("probe", "1", "Проверка", "Чтение файла", reads_files=True)
    derived = replace(roles.PIPELINE, roles=(probe,), prompt_for=lambda _: "Прочитай файл.")

    with_prelude = build_graph(pipeline=derived).compile().get_graph().nodes
    without = build_graph(pipeline=replace(derived, prelude=None)).compile().get_graph().nodes

    assert "materials" in with_prelude
    assert "materials" not in without


def test_evaluation_measures_role_documents_only(tmp_path):
    """
    Рядом с документами ролей код кладёт материалы и реестр. Оценка, которая
    мерила их как документы, находила факты в самом первоисточнике.
    """
    from evals import harness

    case = harness.load_cases(["contradiction"])[0]
    result = harness.run_case(case, tmp_path)

    assert result["stages_done"] == len(roles.ROLES)
