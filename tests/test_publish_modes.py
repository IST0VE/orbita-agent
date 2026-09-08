"""
Задача 2.1: CONFLUENCE_PUBLISH_MODE.

`route()` отправляет в `publish` после каждого ответа оператору — так и было
задумано. Дорого другое: на треде из десяти ходов это десять пар GET+PUT и
десять версий страницы в истории Confluence, из которых девять никому не нужны.

Режим решает, ходить ли в сеть; сам документ собирается в любом случае.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent import config as cfg
from agent.graph import publish_node

CONFIG = {"configurable": {"thread_id": "t-1"}}


def state(*, answer="Ответ оператору", usage=None, document_hash=None):
    value = {
        "messages": [HumanMessage("Вопрос оператора"), AIMessage(answer)],
        "usage": usage or {"calls": 1, "cache_hit": 10, "cache_miss": 5, "output": 3},
    }
    if document_hash is not None:
        value["document_hash"] = document_hash
    return value


@pytest.fixture(autouse=True)
def credentials(monkeypatch: pytest.MonkeyPatch):
    """Реквизиты на месте — иначе этап отвалится раньше, чем дойдёт до режима."""
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://wiki.example.com")
    monkeypatch.setenv("CONFLUENCE_TOKEN", "tok")
    monkeypatch.setenv("CONFLUENCE_SPACE_KEY", "SUP")


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch):
    """Подменяем сам поход в сеть: здесь проверяется решение, а не транспорт."""
    calls = []

    def fake_publish(title, document, settings=None):
        calls.append({"title": title, "document": document})
        return {"status": "created", "page_id": "1", "version": len(calls),
                "title": title, "url": "https://wiki.example.com/1"}

    monkeypatch.setattr("agent.confluence.publish_page", fake_publish)
    return calls


# --------------------------------------------------------------------------
# each — историческое поведение
# --------------------------------------------------------------------------
def test_each_publishes_on_every_turn(published, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_PUBLISH_MODE", "each")

    first = publish_node(state(), CONFIG)
    second = publish_node(state(document_hash=first["document_hash"]), CONFIG)

    assert len(published) == 2
    assert first["publication"]["status"] == "created"
    assert second["publication"]["status"] == "created"


def test_each_is_the_default(published):
    publish_node(state(), CONFIG)
    assert len(published) == 1


# --------------------------------------------------------------------------
# changed — в сеть только при изменившемся документе
# --------------------------------------------------------------------------
def test_changed_skips_when_document_is_the_same(
    published, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("CONFLUENCE_PUBLISH_MODE", "changed")

    first = publish_node(state(), CONFIG)
    same = publish_node(state(document_hash=first["document_hash"]), CONFIG)

    assert len(published) == 1
    assert same["publication"]["status"] == "unchanged"
    # Документ всё равно собран и лежит в состоянии — просто не уехал в сеть.
    assert same["document"]


def test_changed_publishes_when_thread_grows(
    published, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("CONFLUENCE_PUBLISH_MODE", "changed")

    first = publish_node(state(answer="Первый ответ"), CONFIG)
    grown = publish_node(
        state(answer="Другой ответ", document_hash=first["document_hash"]), CONFIG
    )

    assert len(published) == 2
    assert grown["publication"]["status"] == "created"


def test_changed_ignores_the_header(published, monkeypatch: pytest.MonkeyPatch):
    """
    В шапке стоит время сборки. Если бы хеш считался по всему документу,
    режим changed видел бы изменение на каждом ходе и не экономил бы ничего.
    Шапка здесь подменена явно: два прогона подряд укладываются в одну секунду,
    и на настоящем времени тест был бы плавающим.
    """
    monkeypatch.setenv("CONFLUENCE_PUBLISH_MODE", "changed")
    headers = iter(["<p>шапка 10:00:00</p>", "<p>шапка 10:00:07</p>"])
    monkeypatch.setattr(
        "agent.nodes.document_header",
        lambda config=None, renderer=None, pipeline=None: next(headers),
    )

    first = publish_node(state(), CONFIG)
    second = publish_node(state(document_hash=first["document_hash"]), CONFIG)

    assert first["document"] != second["document"]
    assert second["publication"]["status"] == "unchanged"


def test_hash_is_not_updated_when_publication_did_not_happen(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    Хеш описывает то, что лежит на странице. Публикация не состоялась —
    значит, и обновлять его нечем, иначе следующий ход решит, что всё уже там.
    """
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")
    assert "document_hash" not in publish_node(state(), CONFIG)


# --------------------------------------------------------------------------
# manual — решает интерфейс
# --------------------------------------------------------------------------
def test_manual_does_nothing_without_the_flag(
    published, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("CONFLUENCE_PUBLISH_MODE", "manual")

    result = publish_node(state(), CONFIG)

    assert published == []
    assert result["publication"]["status"] == "postponed"


def test_manual_publishes_on_explicit_request(
    published, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("CONFLUENCE_PUBLISH_MODE", "manual")

    result = publish_node(
        state(), {"configurable": {"thread_id": "t-1", "publish": True}}
    )

    assert len(published) == 1
    assert result["publication"]["status"] == "created"


# --------------------------------------------------------------------------
# Валидация
# --------------------------------------------------------------------------
def test_unknown_mode_is_a_configuration_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_PUBLISH_MODE", "иногда")
    with pytest.raises(cfg.ConfigError, match="CONFLUENCE_PUBLISH_MODE"):
        cfg.confluence_publish_mode()


def test_switch_wins_over_mode(published, monkeypatch: pytest.MonkeyPatch):
    """CONFLUENCE_PUBLISH=0 — рубильник: он старше любого режима."""
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")
    monkeypatch.setenv("CONFLUENCE_PUBLISH_MODE", "each")

    assert publish_node(state(), CONFIG)["publication"]["status"] == "disabled"
    assert published == []
