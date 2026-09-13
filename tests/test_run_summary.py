"""
U1: итог, проблемы и следующее действие — до подробностей.

Раньше эти три вопроса приходилось собирать глазами по четырём блокам JSON:
публикация отдельно, остановка конвейера отдельно, заведённые задачи отдельно,
стоимость отдельно. Каждый блок честно отвечал на свой вопрос и ни один —
на главный: что делать дальше.

Здесь же закрепляется второе требование U1: подтверждение публикации
показывает назначение, судьбу объекта и различия, и берёт их из того же
плана, который будет исполнен.
"""

from __future__ import annotations

import pytest

from agent import drafts, publishers
from agent.summary import summary_of


# --------------------------------------------------------------------------
# Итог хода
# --------------------------------------------------------------------------
def test_a_clean_run_says_what_to_check():
    summary = summary_of({
        "artifacts": {"requirements": "…", "api": "…"},
        "publication": {"status": "created", "pages": [{}, {}]},
    })

    assert "Документов этапов: 2" in summary["outcome"]
    assert summary["problems"] == []
    assert "Проверьте документы" in summary["next"][0]


def test_a_stale_plan_is_a_problem_with_a_next_step():
    summary = summary_of({
        "artifacts": {"requirements": "…"},
        "publication": {"status": "stale", "reason": "план изменился после подтверждения"},
    })

    assert any("план изменился" in item for item in summary["problems"])
    assert any("подтвердите публикацию" in item for item in summary["next"])


def test_a_halted_pipeline_names_the_stage():
    summary = summary_of({"halt": {"stage": "api", "reason": "оператор остановил"}})

    assert any("на этапе api" in item for item in summary["problems"])
    assert any("подтвердите этап заново" in item for item in summary["next"])


def test_unresolved_jira_operations_are_surfaced():
    summary = summary_of({
        "issues": {"status": "unknown", "unresolved": [{"local": "T-1"}, {"local": "T-2"}]},
    })

    assert any("неизвестным результатом: 2" in item for item in summary["problems"])
    assert any("метке операции" in item for item in summary["next"])


def test_unpriced_calls_are_a_problem_not_a_zero():
    summary = summary_of({"cost": {"usd": 0.0, "unpriced_calls": 3}})

    assert any("неизвестному тарифу: 3" in item for item in summary["problems"])
    assert any("тариф модели" in item for item in summary["next"])


def test_the_nt_verdict_and_execution_are_shown_apart():
    summary = summary_of({"execution_status": "STOPPED", "analysis_result": "INCONCLUSIVE"})

    assert "Тест: STOPPED" in summary["outcome"]
    assert "SLA: INCONCLUSIVE" in summary["outcome"]


def test_the_same_next_step_is_not_repeated():
    summary = summary_of({
        "publication": {"status": "stale", "reason": "план изменился"},
        "halt": {},
        "cost": {},
    })

    assert len(summary["next"]) == len(set(summary["next"]))


def test_an_empty_state_says_there_is_no_result_yet():
    summary = summary_of({})

    assert summary["outcome"] == []
    assert summary["next"] == ["Результата ещё нет."]


def test_the_publish_node_puts_the_summary_into_the_state(monkeypatch: pytest.MonkeyPatch):
    from langchain_core.messages import AIMessage, HumanMessage

    from agent.graph import publish_node

    monkeypatch.setenv("PUBLISH_TARGET", "none")
    update = publish_node(
        {"messages": [HumanMessage("вопрос"), AIMessage("ответ")], "usage": {"calls": 1}},
        {"configurable": {"thread_id": "t-1"}},
    )

    assert update["summary"]["outcome"]
    assert update["summary"]["next"]


# --------------------------------------------------------------------------
# Что именно подтверждает оператор
# --------------------------------------------------------------------------
def test_an_update_shows_what_will_change():
    target = publishers.current()
    title = "Orbita: тема [t-1] — 01 Требования"
    target.publish(title, "первая строка\nвторая строка\n")

    preview = target.preview(title)
    before = publishers.current_text(target, title, preview)
    diff = publishers.diff_of(before, "первая строка\nтретья строка\nчетвёртая\n")

    assert diff["available"]
    assert diff["added"] == 2
    assert diff["removed"] == 1
    assert "третья строка" in diff["text"]


def test_a_new_document_says_it_is_created_not_changed():
    card = drafts.page(
        role="api", title="Orbita: новая [t-1]", document="тело",
        fmt="markdown", where="file", preview={"action": "create"},
    )

    assert card["diff"]["available"] is False
    assert "создаётся заново" in card["diff"]["reason"]


def test_the_draft_of_an_update_carries_the_diff_and_the_destination():
    target = publishers.current()
    title = "Orbita: тема [t-1] — 02 API"
    target.publish(title, "старое тело\n")

    plan = {
        "publisher": target,
        "pages": [{"role": "api", "title": title, "document": "новое тело\n"}],
    }
    card = drafts.pages(plan)[0]

    assert card["action"] == "update"
    assert card["diff"]["available"] and card["diff"]["added"] == 1
    # Назначение видно поимённо: не «в файл», а в какой именно файл.
    assert any("Файл" == field["label"] for field in card["fields"])


def test_an_unreadable_previous_version_is_said_out_loud(monkeypatch: pytest.MonkeyPatch):
    """Различий может не быть — но тогда это сказано, а не подразумевается."""
    diff = publishers.diff_of(None, "новое тело")

    assert diff["available"] is False
    assert "не прочитана" in diff["reason"]


def test_an_unchanged_document_is_not_an_empty_diff():
    diff = publishers.diff_of("одно и то же\n", "одно и то же\n")

    assert diff["available"] and diff["unchanged"]
    assert diff["added"] == diff["removed"] == 0


def test_a_long_diff_is_cut_and_says_so():
    before = "\n".join(f"строка {number}" for number in range(500))
    after = "\n".join(f"другая {number}" for number in range(500))

    diff = publishers.diff_of(before, after)

    assert diff["text"].count("\n") <= publishers.DIFF_LINES
    assert "показаны первые" in diff["text"]
