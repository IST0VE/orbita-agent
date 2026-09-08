"""
Черновики: что именно показывают оператору до записи в чужую систему.

Проверяется одно свойство и его граница. Свойство: черновик показывает то,
что уедет, — тело после рендерера цели и тип из схемы проекта, а не текст
документа и не тип, написанный моделью. Граница: сеть здесь только для
справки, и её отказ обязан превращаться в пометку, а не в исключение —
остановка открывается и тогда, когда wiki и трекер лежат.
"""

from __future__ import annotations

import pytest

from agent import drafts, jira_plan, jira_writer, publishers


def plan_of(*rows: dict) -> jira_plan.Plan:
    plan = jira_plan.Plan()
    plan.items = [jira_plan.Item(**row) for row in rows]
    return plan


# --------------------------------------------------------------------------
# Задачи
# --------------------------------------------------------------------------
def test_the_type_of_the_project_wins_over_the_type_of_the_plan(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    В русифицированном инстансе `Story` называется «Задача», и заведётся
    карточка ею. Узнать об этом после заведения — поздно.
    """
    monkeypatch.setattr(jira_writer, "issue_types", lambda project, settings=None: ["Задача"])

    cards, _ = drafts.issues(plan_of({"local": "S-1", "type": "Story", "summary": "Экспорт"}), "ORB")

    assert {"label": "Тип", "value": "Задача"} in cards[0]["fields"]
    assert "Задача" in cards[0]["note"]


def test_an_unreachable_tracker_does_not_close_the_window(monkeypatch: pytest.MonkeyPatch):
    def refuse(project, settings=None):
        raise jira_writer.JiraError("нет сети")

    monkeypatch.setattr(jira_writer, "issue_types", refuse)

    cards, warnings = drafts.issues(
        plan_of({"local": "T-1", "type": "Task", "summary": "Экспорт"}), "ORB"
    )

    assert cards[0]["title"] == "Экспорт"
    assert any("нет сети" in warning for warning in warnings)


def test_without_a_project_the_schema_is_not_asked(monkeypatch: pytest.MonkeyPatch):
    """Проекта нет — спрашивать не у кого, и ходить в сеть незачем."""
    monkeypatch.setattr(
        jira_writer,
        "issue_types",
        lambda project, settings=None: pytest.fail("схема спрошена без проекта"),
    )

    cards, _ = drafts.issues(plan_of({"local": "T-1", "type": "Task", "summary": "Экспорт"}), "")

    assert cards[0]["where"] == "проект не выбран"


# --------------------------------------------------------------------------
# Страницы
# --------------------------------------------------------------------------
def test_a_page_that_exists_is_marked_as_an_overwrite(tmp_path):
    publisher = publishers.FilePublisher()
    publisher.publish("Отчёт", "было")
    plan = {
        "publisher": publisher,
        "pages": [{"role": "requirements", "title": "Отчёт", "document": "стало"}],
    }

    draft = drafts.pages(plan)[0]

    assert draft["action"] == "update"
    assert draft["document"] == "стало"
    assert draft["format"] == "markdown"


def test_a_new_page_is_marked_as_a_creation():
    plan = {
        "publisher": publishers.FilePublisher(),
        "pages": [{"role": "requirements", "title": "Отчёт", "document": "стало"}],
    }

    assert drafts.pages(plan)[0]["action"] == "create"


def test_an_unconfigured_confluence_leaves_the_fate_unknown():
    """Спросить wiki не удалось — так и сказано; окно от этого не закрывается."""
    plan = {
        "publisher": publishers.ConfluencePublisher(),
        "pages": [{"role": "requirements", "title": "Отчёт", "document": "стало"}],
    }

    draft = drafts.pages(plan)[0]

    assert draft["action"] == "unknown"
    assert draft["note"]
