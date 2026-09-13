"""
R8: где потолок чтения применяется и что означает ноль.

`AGENT_INPUT_MAX_CHARS=0` документирован как «читать целиком», а в коде
оставался обычным числом: проверка «место кончилось» при нуле была истинной
с самого начала, и непустая папка задачи превращалась в отказ «ни один файл
папки задачи не прочитан». То же было у источников по ссылкам.

Второе, что здесь закрепляется, — сам смысл числа. На один документ потолок
действует при чтении одного документа; на весь набор — при сборке комплекта.
"""

from __future__ import annotations

import pytest

from agent import confluence, inputs, sources
from agent.sources import Budget


@pytest.fixture
def task(tmp_path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("AGENT_INPUT_DIR", str(tmp_path))
    folder = tmp_path / "задача"
    folder.mkdir()
    (folder / "требования.md").write_text("Т" * 100, encoding="utf-8")
    (folder / "контракт.md").write_text("К" * 100, encoding="utf-8")
    return "задача"


# --------------------------------------------------------------------------
# Остаток потолка
# --------------------------------------------------------------------------
def test_a_zero_budget_is_no_budget_at_all():
    budget = Budget(0)

    assert budget.unlimited
    assert not budget.exhausted
    assert budget.head("любой текст") == "любой текст"
    budget.spend("любой текст")
    assert not budget.exhausted
    assert not budget.overflowed


def test_a_positive_budget_runs_out_exactly_at_the_edge():
    budget = Budget(10)

    assert budget.left == 10
    budget.spend("1234567890")
    assert budget.exhausted
    assert budget.overflowed
    assert budget.left == 0


def test_a_budget_short_of_the_edge_still_has_room():
    budget = Budget(10)
    budget.spend("123456789")

    assert not budget.exhausted
    assert budget.left == 1
    assert budget.head("абвгд") == "а"


# --------------------------------------------------------------------------
# Файлы папки задачи
# --------------------------------------------------------------------------
def test_zero_limit_reads_every_file_whole(task, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_INPUT_MAX_CHARS", "0")

    found = sources.from_files("разложи задачу", task)

    assert "error" not in found
    assert sorted(found["names"]) == ["контракт.md", "требования.md"]
    assert found["skipped"] == []
    assert not found["truncated"]
    assert all(len(text) == 100 for text in found["each"].values())


def test_one_file_and_a_zero_limit_is_not_an_empty_input(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_INPUT_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_INPUT_MAX_CHARS", "0")
    folder = tmp_path / "одна"
    folder.mkdir()
    (folder / "документ.md").write_text("текст документа", encoding="utf-8")

    found = sources.from_files("разложи", "одна")

    assert found["names"] == ["документ.md"]
    assert found["text"] == "текст документа"


def test_a_positive_limit_applies_to_the_whole_set(task, monkeypatch):
    """Потолок на набор: второй файл дочитывается остатком, а не своими 150."""
    monkeypatch.setenv("AGENT_INPUT_MAX_CHARS", "150")

    found = sources.from_files("разложи задачу", task)

    # Порядок — как их отдаёт папка; важно, что второму досталось ровно то,
    # что осталось от набора, а не собственные 150.
    first, second = found["names"]
    assert found["each"][first] == found["each"][first][0] * 100
    partial = found["each"][second]
    letter = partial[0]
    assert partial.startswith(letter * 50)
    assert letter * 51 not in partial
    assert "обрезан" in partial
    assert found["truncated"]


def test_the_exact_edge_is_not_a_truncation(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_INPUT_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_INPUT_MAX_CHARS", "20")
    folder = tmp_path / "ровно"
    folder.mkdir()
    (folder / "документ.md").write_text("A" * 20, encoding="utf-8")

    found = sources.from_files("разложи", "ровно")

    assert found["each"]["документ.md"] == "A" * 20
    assert found["skipped"] == []


def test_a_file_that_does_not_fit_is_named_as_skipped(task, monkeypatch):
    monkeypatch.setenv("AGENT_INPUT_MAX_CHARS", "100")

    found = sources.from_files("разложи задачу", task)

    assert len(found["names"]) == 1
    assert len(found["skipped"]) == 1
    assert found["truncated"]


def test_a_single_file_read_still_uses_the_limit_per_file(task, monkeypatch):
    """Инструмент читает один документ — потолок применяется к нему одному."""
    monkeypatch.setenv("AGENT_INPUT_MAX_CHARS", "10")

    text = inputs.read(task, "требования.md")

    assert text.startswith("Т" * 10)
    assert "обрезан" in text


def test_a_single_file_read_with_a_zero_limit_is_whole(task, monkeypatch):
    monkeypatch.setenv("AGENT_INPUT_MAX_CHARS", "0")

    assert inputs.read(task, "требования.md") == "Т" * 100


# --------------------------------------------------------------------------
# Источники по ссылкам
# --------------------------------------------------------------------------
def test_zero_limit_does_not_refuse_linked_sources(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_INPUT_MAX_CHARS", "0")
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://wiki.example.com")
    monkeypatch.setattr(confluence, "missing_vars", lambda: [])
    monkeypatch.setattr(
        confluence,
        "fetch_page",
        lambda key: {"id": key, "title": "Контракт", "text": "Текст страницы",
                     "url": "https://wiki.example.com/pages/12345", "truncated": False},
    )

    block = sources.linked_context("смотри https://wiki.example.com/pages/12345")

    assert "Текст страницы" in block
    assert "достигнут лимит контекста" not in block
    assert "обрезан по лимиту контекста" not in block
