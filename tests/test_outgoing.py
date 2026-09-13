"""
R2: одна проверка исходящего текста на все пути наружу.

Маскирование в проекте было, но жило внутри сборки документов конвейера.
Всё, что писало наружу мимо этой сборки, писало и мимо маски: новая версия
документа в графе обновления и описание задачи в Jira. Синтетический ключ
доезжал и до файла, и до payload трекера.

Ключи здесь синтетические и неработающие, но узнаваемые для детектора —
иначе тест проверял бы, что маска не трогает обычный текст, а не то, что
она трогает секрет.
"""

from __future__ import annotations

import json

import pytest
import responses
from langchain_core.messages import AIMessage, HumanMessage

from agent import drafts, jira, jira_plan, jira_writer, outgoing, publishers, update_graph

# Синтетический ключ: правильной формы, ничего не открывает.
FAKE_KEY = "sk-test1234567890abcdefghijklmnop"
FAKE_AWS = "AKIAIOSFODNN7EXAMPLE"
CREDENTIALS_URL = "https://deploy:hunter2@wiki.example.com/rest"


# --------------------------------------------------------------------------
# Сама проверка
# --------------------------------------------------------------------------
def test_a_known_secret_is_masked_in_a_plain_string():
    out = outgoing.sanitize(f"ключ {FAKE_KEY} в тексте")

    assert FAKE_KEY not in out
    assert "***" in out
    assert outgoing.verify(out) == []


def test_nested_structures_are_walked_to_the_bottom():
    """Описание Cloud уезжает документом ADF: текст лежит третьим уровнем."""
    payload = {
        "fields": {
            "description": {
                "type": "doc",
                "content": [{"type": "paragraph", "content": [{"text": f"ключ {FAKE_KEY}"}]}],
            }
        }
    }

    out = outgoing.sanitize(payload)

    assert FAKE_KEY not in json.dumps(out, ensure_ascii=False)
    assert outgoing.verify(out) == []


def test_a_finding_names_the_field_and_the_kind_without_the_value():
    findings = outgoing.verify({"fields": {"summary": f"ключ {FAKE_AWS}"}})

    assert [(f.field, f.kind) for f in findings] == [("fields.summary", "aws-access-key")]
    assert FAKE_AWS not in findings[0].describe()


def test_an_address_with_a_password_is_blocked_not_rewritten():
    """Подменить адрес нельзя: подменённый ведёт в другое место."""
    with pytest.raises(outgoing.OutgoingBlocked) as blocked:
        outgoing.guard({"url": CREDENTIALS_URL}, "plan")

    assert "url-credentials" in str(blocked.value)
    assert "hunter2" not in str(blocked.value)


def test_operator_patterns_still_work(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_MASK_PATTERNS", r"acc-\d+")

    assert "acc-1024" not in outgoing.sanitize("клиент acc-1024")


def test_ordinary_text_and_markup_survive():
    text = "# Заголовок\n\n- пункт с `кодом` и **жирным**\n\nhttps://wiki.example.com/x"

    assert outgoing.sanitize(text) == text


def test_checking_twice_changes_nothing():
    """Проверка идемпотентна: она стоит и в плане, и на границе перед сетью."""
    once = outgoing.sanitize(f"ключ {FAKE_KEY}")

    assert outgoing.sanitize(once) == once


# --------------------------------------------------------------------------
# Пути наружу
# --------------------------------------------------------------------------
def test_a_file_never_receives_a_secret():
    result = publishers.current().publish("Orbita: тема [t-1]", f"ключ {FAKE_KEY} в документе")

    text = publishers.resolve_document(
        [item["name"] for item in publishers.documents()][0]
    )
    assert FAKE_KEY not in text.read_text(encoding="utf-8")
    assert result["status"] == "created"


def test_the_update_graph_masks_the_new_version(monkeypatch: pytest.MonkeyPatch):
    state = {
        "revision": {"changes": [{"anchor": "a"}], "questions": []},
        "document": f"Новая версия. Ключ {FAKE_KEY}.",
        "source": {"title": "договор — обновление abc123"},
    }

    plan = update_graph.prepare_node(state)["publication_plan"]

    assert FAKE_KEY not in plan["document"]
    # Предпросмотр собран из того же проверенного тела, а не из исходного.
    assert plan["draft"]["document"] == plan["document"]


def test_the_update_graph_refuses_an_address_with_a_password():
    state = {
        "revision": {"changes": [{"anchor": "a"}], "questions": []},
        "document": f"Выгрузка настроена на {CREDENTIALS_URL}",
        "source": {"title": "инструкция — обновление abc123"},
    }

    result = update_graph.prepare_node(state)

    assert result["publication"]["status"] == "failed"
    assert "url-credentials" in result["publication"]["reason"]
    assert "hunter2" not in result["publication"]["reason"]


BASE = "https://jira.example.com"
API = "/rest/api/3"
CLOUD = jira.Settings(base_url=BASE, token="tok", email="me@org.com", api_path=API, timeout_s=5.0)


def _plan(**extra) -> jira_plan.Plan:
    issue = {"local": "T-1", "type": "Task", "summary": "Задача", **extra}
    return jira_plan.parse(json.dumps({"issues": [issue]}, ensure_ascii=False))


@responses.activate
def test_a_jira_payload_never_receives_a_secret():
    responses.add(
        responses.GET,
        f"{BASE}{API}/issue/createmeta/ORB/issuetypes",
        json={"values": [{"name": "Task"}]},
        status=200,
    )
    responses.add(responses.POST, f"{BASE}{API}/issue", json={"key": "ORB-1"}, status=201)

    plan = _plan(summary=f"Ротация {FAKE_AWS}", description=f"Ключ {FAKE_KEY} лежит в конфиге")
    result = jira_writer.create_issues(plan, "ORB", settings=CLOUD)

    raw = next(
        call.request.body for call in responses.calls if call.request.url.endswith("/issue")
    )
    body = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    assert FAKE_KEY not in body
    assert FAKE_AWS not in body
    assert result["status"] == "created"


def test_a_jira_draft_shows_what_will_be_sent():
    plan = _plan(description=f"Ключ {FAKE_KEY} лежит в конфиге")

    cards, _ = drafts.issues(plan, "")

    assert FAKE_KEY not in cards[0]["document"]
    assert cards[0]["document"] == outgoing.sanitize(plan.items[0].body(source=""))


def test_a_page_draft_shows_what_will_be_sent():
    card = drafts.page(
        role="api",
        title=f"Orbita: {FAKE_AWS} [t-1]",
        document=f"тело с ключом {FAKE_KEY}",
        fmt="markdown",
        where="file",
        preview={"action": "create"},
    )

    assert FAKE_KEY not in card["document"]
    assert FAKE_AWS not in card["title"]
    assert card["chars"] == len(card["document"])


def test_the_pipeline_page_is_still_masked_end_to_end():
    from agent.graph import publish_node

    publish_node(
        {
            "messages": [HumanMessage(f"Проверь ключ {FAKE_KEY}"), AIMessage("Готово")],
            "usage": {"calls": 1},
        },
        {"configurable": {"thread_id": "t-1"}},
    )

    for item in publishers.documents():
        assert FAKE_KEY not in publishers.read_document(item["name"])
