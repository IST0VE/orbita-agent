"""
Разбор карточек: что доедет до трекера, а что будет отброшено.

Этот модуль — граница между текстом модели и запросом в чужую систему, и
проверяется он соответственно: не «разбирается ли правильный JSON» (это
скучная половина), а что происходит с неправильным. Карточка без заголовка,
подзадача под эпиком, родитель, которого нет, план на двести задач — всё это
модель пишет регулярно, и каждый случай стоит либо отказа Jira на всей пачке,
либо заведённой не туда задачи.
"""

from __future__ import annotations

import json

import pytest

from agent import jira_plan

DOCUMENT = """\
# Карточки

| Local ID | Тип | Заголовок |
|---|---|---|
| EPIC-1 | Epic | Платежи |

```json
{
  "issues": [
    {"id": "TASK-1", "type": "Task", "summary": "Идемпотентность", "parent": "EPIC-1",
     "description": "Хранить ключ сутки", "acceptance": ["повтор не создаёт вторую попытку"],
     "depends_on": ["TASK-2"], "labels": ["payments"], "estimate": "3-5 SP",
     "service": "payments-api", "component": "checkout", "layer": "backend"},
    {"id": "TASK-2", "type": "Story", "summary": "Вебхук провайдера", "parent": "EPIC-1"},
    {"id": "EPIC-1", "type": "Epic", "summary": "Платежи"}
  ]
}
```
"""


def plan_of(issues: list[dict], **extra) -> jira_plan.Plan:
    """План из карточек без обёртки документа: короче и читается так же."""
    return jira_plan.parse(json.dumps({"issues": issues, **extra}, ensure_ascii=False))


# --------------------------------------------------------------------------
# Достать машинную часть документа
# --------------------------------------------------------------------------
def test_json_is_taken_from_the_fenced_block_not_from_the_table():
    plan = jira_plan.parse(DOCUMENT)

    assert [item.local for item in plan.items] == ["EPIC-1", "TASK-1", "TASK-2"]


def test_json_without_a_fence_is_still_parsed():
    """Потерянные кавычки — ошибка модели, а не повод потерять оплаченный прогон."""
    plan = jira_plan.parse('Вот карточки: {"issues": [{"id": "T-1", "summary": "Раз"}]}')

    assert [item.summary for item in plan.items] == ["Раз"]


def test_a_document_without_json_is_an_error():
    with pytest.raises(jira_plan.PlanError):
        jira_plan.parse("# Карточки\n\nвсё написано текстом")


def test_broken_json_is_an_error_with_a_reason():
    with pytest.raises(jira_plan.PlanError):
        jira_plan.parse('```json\n{"issues": [{"id": "T-1",}]}\n```')


# --------------------------------------------------------------------------
# Порядок заведения
#
# Эпики первыми — не косметика: у ребёнка в запросе стоит настоящий ключ
# родителя, а он появляется только после ответа трекера.
# --------------------------------------------------------------------------
def test_epics_go_first_and_subtasks_last():
    plan = plan_of(
        [
            {"id": "SUB-1", "type": "Sub-task", "summary": "Шаг", "parent": "TASK-1"},
            {"id": "TASK-1", "type": "Task", "summary": "Задача", "parent": "EPIC-1"},
            {"id": "EPIC-1", "type": "Epic", "summary": "Эпик"},
        ]
    )

    assert [item.local for item in plan.items] == ["EPIC-1", "TASK-1", "SUB-1"]


def test_order_inside_a_level_is_the_order_of_the_plan():
    """Порядок задач в массиве — порядок реализации, и терять его нельзя."""
    plan = plan_of(
        [
            {"id": "T-1", "type": "Task", "summary": "Первая"},
            {"id": "T-2", "type": "Task", "summary": "Вторая"},
            {"id": "T-3", "type": "Task", "summary": "Третья"},
        ]
    )

    assert [item.local for item in plan.items] == ["T-1", "T-2", "T-3"]


# --------------------------------------------------------------------------
# Что отбрасывается и почему
# --------------------------------------------------------------------------
def test_a_card_without_a_summary_is_dropped_with_a_reason():
    """Пустой заголовок — это 400 от Jira, и узнать о нём надо до сети."""
    plan = plan_of([{"id": "T-1", "type": "Task"}, {"id": "T-2", "summary": "Есть"}])

    assert [item.local for item in plan.items] == ["T-2"]
    assert any("T-1" in warning for warning in plan.warnings)


def test_an_unknown_type_becomes_a_task_instead_of_failing_the_batch():
    plan = plan_of([{"id": "T-1", "type": "Инициатива", "summary": "Раз"}])

    assert plan.items[0].type == "Task"
    assert plan.warnings


def test_a_missing_parent_is_dropped_not_sent_to_jira():
    plan = plan_of([{"id": "T-1", "type": "Task", "summary": "Раз", "parent": "EPIC-9"}])

    assert plan.items[0].parent == ""
    assert any("EPIC-9" in warning for warning in plan.warnings)


def test_a_card_cannot_be_its_own_parent():
    plan = plan_of([{"id": "T-1", "type": "Task", "summary": "Раз", "parent": "T-1"}])

    assert plan.items[0].parent == ""


def test_a_subtask_under_an_epic_loses_the_link():
    """Jira такого не допускает, а модель пишет так регулярно: в тексте логично."""
    plan = plan_of(
        [
            {"id": "EPIC-1", "type": "Epic", "summary": "Эпик"},
            {"id": "SUB-1", "type": "Sub-task", "summary": "Шаг", "parent": "EPIC-1"},
        ]
    )

    assert {item.local: item.parent for item in plan.items} == {"EPIC-1": "", "SUB-1": ""}


def test_a_task_under_a_task_loses_the_link():
    plan = plan_of(
        [
            {"id": "T-1", "type": "Task", "summary": "Раз"},
            {"id": "T-2", "type": "Task", "summary": "Два", "parent": "T-1"},
        ]
    )

    assert [item.parent for item in plan.items] == ["", ""]


def test_duplicate_local_keys_do_not_create_two_cards():
    plan = plan_of(
        [
            {"id": "T-1", "type": "Task", "summary": "Раз"},
            {"id": "T-1", "type": "Task", "summary": "Тоже раз"},
        ]
    )

    assert [item.summary for item in plan.items] == ["Раз"]


def test_the_batch_is_capped_and_the_tail_is_named():
    """Декомпозиция на сотню задач — ошибка модели, и платить за неё нельзя."""
    plan = jira_plan.parse(
        json.dumps(
            {"issues": [{"id": f"T-{n}", "type": "Task", "summary": f"Раз {n}"} for n in range(10)]}
        ),
        limit=3,
    )

    assert len(plan.items) == 3
    assert "T-9" in plan.warnings[-1]


def test_a_long_summary_is_trimmed_to_the_jira_limit():
    plan = plan_of([{"id": "T-1", "type": "Task", "summary": "я" * 400}])

    assert len(plan.items[0].summary) == jira_plan.SUMMARY_LIMIT


# --------------------------------------------------------------------------
# Описание задачи
# --------------------------------------------------------------------------
def test_the_description_carries_every_field_of_the_card():
    plan = jira_plan.parse(DOCUMENT)
    item = next(item for item in plan.items if item.local == "TASK-1")

    body = item.body({"TASK-2": "ORB-8"}, source="https://wiki/pages/1")

    assert "Хранить ключ сутки" in body
    assert "повтор не создаёт вторую попытку" in body
    assert "payments-api / checkout / backend" in body
    assert "ORB-8" in body  # зависимость на уже заведённую задачу — настоящим ключом
    assert "3-5 SP" in body
    assert "https://wiki/pages/1" in body


def test_a_dependency_on_a_pending_card_stays_local():
    """Врать про ключ, которого ещё нет, хуже, чем показать локальный."""
    plan = jira_plan.parse(DOCUMENT)
    item = next(item for item in plan.items if item.local == "TASK-1")

    assert "TASK-2" in item.body({})


def test_the_summary_line_counts_epics_and_tasks_separately():
    plan = jira_plan.parse(DOCUMENT)

    assert plan.summary() == "эпиков 1, задач 2"
