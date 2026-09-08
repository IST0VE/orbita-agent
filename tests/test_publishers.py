"""
Задача 4.5: цель публикации как протокол.

Главное здесь — не абстракция, а её следствие: проект без Confluence должен
класть документ треда на диск, а не писать «этап пропущен». Человек, впервые
запустивший агента, видит готовый документ и понимает, что вообще должно было
получиться.

Формат — свойство цели: на wiki уезжает storage format, в файл — Markdown.
Поэтому проверяется и разметка, и то, что маскирование работает в обеих.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent import config as cfg
from agent import publishers, roles
from agent.graph import build_graph, publish_node

CONFIG = {"configurable": {"thread_id": "t-1"}}

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
    def invoke(self, messages: list) -> AIMessage:
        return ANSWER


@pytest.fixture(autouse=True)
def no_memory(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MEMORY_ENABLED", "0")


@pytest.fixture
def confluence_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://wiki.example.com")
    monkeypatch.setenv("CONFLUENCE_TOKEN", "tok")
    monkeypatch.setenv("CONFLUENCE_SPACE_KEY", "SUP")


def state(*, answer: str = "Ответ оператору", question: str = "Вопрос оператора") -> dict:
    return {
        "messages": [HumanMessage(question), AIMessage(answer)],
        "usage": {"calls": 1, "cache_hit": 10, "cache_miss": 5, "output": 3},
    }


def files() -> list[Path]:
    directory = Path(cfg.publish_dir())
    return sorted(directory.glob("*.md")) if directory.exists() else []


# --------------------------------------------------------------------------
# Выбор цели
# --------------------------------------------------------------------------
def test_auto_falls_back_to_a_file_without_confluence():
    assert publishers.resolve() == "file"


def test_auto_picks_confluence_when_it_is_configured(confluence_env):
    assert publishers.resolve() == "confluence"


def test_explicit_target_wins_over_auto(confluence_env, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PUBLISH_TARGET", "file")
    assert publishers.resolve() == "file"


def test_unknown_target_is_a_configuration_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PUBLISH_TARGET", "notion")

    with pytest.raises(cfg.ConfigError, match="PUBLISH_TARGET"):
        publishers.resolve()


def test_each_target_brings_its_own_format(confluence_env):
    assert publishers.current().renderer.name == "storage"
    assert publishers._PUBLISHERS["file"].renderer.name == "markdown"


# --------------------------------------------------------------------------
# Критерий приёмки: запуск без Confluence
# --------------------------------------------------------------------------
def test_thread_without_confluence_puts_the_document_on_disk():
    app = build_graph(llm=Once()).compile()

    result = app.invoke({"messages": [HumanMessage("Что с экспортом?")]}, config=CONFIG)

    assert result["publication"]["status"] == "created"

    # Страница у каждого этапа своя, и ссылка теперь у страницы, а не у
    # публикации целиком: сводить пять адресов к одному нечестно.
    pages = result["publication"]["pages"]
    assert len(pages) == len(files()) == len(roles.ROLES)
    assert all(page["url"].startswith("file:") for page in pages)
    assert "Экспорт больше 100000 строк" in files()[0].read_text(encoding="utf-8")


def test_the_file_is_markdown_not_storage_format():
    publish_node(state(), CONFIG)

    text = files()[0].read_text(encoding="utf-8")
    assert text.startswith("# Orbita: Вопрос оператора [t-1]")
    assert "## Вопрос оператора" in text
    assert "| Метрика | Значение |" in text
    assert "<h2>" not in text


def test_the_same_thread_rewrites_its_own_file():
    """Upsert по заголовку сохраняется: имя файла — тот же заголовок страницы."""
    first = publish_node(state(answer="Первый ответ"), CONFIG)
    second = publish_node(state(answer="Второй ответ"), CONFIG)

    assert first["publication"]["status"] == "created"
    assert second["publication"]["status"] == "updated"
    assert len(files()) == 1
    assert "Второй ответ" in files()[0].read_text(encoding="utf-8")


def test_masking_works_in_markdown_too(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_MASK_PATTERNS", r"acc-\d+")

    publish_node(state(question="Клиент acc-1024 жалуется"), CONFIG)

    text = files()[0].read_text(encoding="utf-8")
    assert "acc-1024" not in text
    assert "***" in text


def test_unwritable_directory_does_not_break_the_thread(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """На месте папки лежит файл: публикация падает, тред — нет."""
    blocked = tmp_path / "занято"
    blocked.write_text("не папка", encoding="utf-8")
    monkeypatch.setenv("PUBLISH_DIR", str(blocked))

    publication = publish_node(state(), CONFIG)["publication"]

    assert publication["status"] == "failed"
    assert "не записать" in publication["reason"]


# --------------------------------------------------------------------------
# Ничего никуда
# --------------------------------------------------------------------------
def test_target_none_keeps_the_document_in_the_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PUBLISH_TARGET", "none")

    result = publish_node(state(), CONFIG)

    assert result["publication"]["status"] == "disabled"
    assert "PUBLISH_TARGET=none" in result["publication"]["reason"]
    assert files() == []
    assert result["document"]


def test_the_switch_is_stronger_than_the_target(monkeypatch: pytest.MonkeyPatch):
    """CONFLUENCE_PUBLISH=0 — исторические имя, общий смысл: выключено всё."""
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")

    assert publish_node(state(), CONFIG)["publication"]["status"] == "disabled"
    assert files() == []


# --------------------------------------------------------------------------
# Имя файла
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Orbita: экспорт [t-1]", "Orbita_экспорт_t-1"),
        ("   ", "document"),
        ("a" * 200, "a" * 120),
    ],
)
def test_title_becomes_a_readable_file_name(title: str, expected: str):
    assert publishers.slug(title) == expected


# --------------------------------------------------------------------------
# Чтение опубликованного
#
# Интерфейс показывает документы отдельным блоком: страница файловой цели
# приезжает в состояние треда как `file://`, а такую ссылку вкладка браузера
# не открывает. Поэтому папку публикации читает сервер — с той же границей,
# что и папку задачи.
# --------------------------------------------------------------------------
def test_published_document_is_listed_with_its_title():
    publishers.current().publish("Orbita: тема [t-1] — 01 Системные требования", "текст")

    listed = publishers.documents()

    assert [item["title"] for item in listed] == ["Orbita: тема [t-1] — 01 Системные требования"]
    assert publishers.read_document(listed[0]["name"]).startswith("# Orbita")


def test_a_stranger_file_in_the_folder_is_not_a_document():
    """Оператор может держать в папке публикации что угодно; список — про страницы."""
    publishers.current().publish("Orbita: тема [t-1]", "текст")
    (publishers.directory() / "заметка.txt").write_text("не документ", encoding="utf-8")

    assert [item["name"] for item in publishers.documents()] == ["Orbita_тема_t-1.md"]


def test_documents_are_listed_from_the_freshest():
    for number in range(3):
        publishers.current().publish(f"Orbita: тема [t-{number}]", "текст")
    # Три файла пишутся в один тик часов файловой системы, поэтому возраст
    # проставляется явно: проверяется порядок, а не разрешение таймера.
    now = time.time()
    for number, age in ((0, 300), (1, 200), (2, 100)):
        path = publishers.directory() / f"Orbita_тема_t-{number}.md"
        os.utime(path, (now - age, now - age))

    titles = [item["title"] for item in publishers.documents()]

    assert titles == ["Orbita: тема [t-2]", "Orbita: тема [t-1]", "Orbita: тема [t-0]"]


@pytest.mark.parametrize("name", ["../.env", "нет-такого.md", "вложенная/страница.md"])
def test_document_outside_the_publish_folder_is_refused(name: str):
    publishers.current().publish("Orbita: тема [t-1]", "текст")

    with pytest.raises(publishers.DocumentError):
        publishers.read_document(name)


def test_an_empty_publish_folder_is_not_an_error():
    assert publishers.documents() == []
