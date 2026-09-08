"""
Обратное направление Confluence: поиск и чтение страниц.

До графа подготовки задачи клиент был односторонним — агент писал на wiki и
никогда её не читал. Проверяется то, что появилось вместе с чтением: что CQL
собирает клиент и ограничение по пространству из него не выкинуть; что текст
страницы вынимается из storage format вместе с содержимым макросов; и что
поиск работает через v1 даже тогда, когда страницы пишутся через v2, — у v2
полнотекстового поиска нет вообще.
"""

from __future__ import annotations

import pytest
import responses

from agent import confluence

BASE = "https://wiki.example.com"
SEARCH = "/rest/api/content/search"

V1 = confluence.Settings(
    base_url=BASE,
    token="tok",
    space_key="SUP",
    email="me@org.com",
    api_path="/rest/api/content",
    api_version="v1",
    timeout_s=5.0,
    search_path=SEARCH,
    search_limit=4,
    read_max_chars=100,
)
V2 = confluence.Settings(
    base_url=BASE,
    token="tok",
    space_key="SUP",
    space_id="777",
    email="me@org.com",
    api_path="/api/v2",
    api_version="v2",
    timeout_s=5.0,
    search_path=SEARCH,
    search_limit=4,
    read_max_chars=100,
)

PAGE = "<h1>Формат вебхука</h1><p>Провайдер шлёт <strong>POST</strong>.</p><ul><li>раз</li><li>два</li></ul>"


# --------------------------------------------------------------------------
# Поиск
# --------------------------------------------------------------------------
@responses.activate
def test_search_builds_cql_and_pins_it_to_the_space():
    """
    Ограничение по CONFLUENCE_SPACE_KEY ставит клиент. Свободный CQL от модели
    вынес бы поиск за пределы пространства, которое разрешил оператор.
    """
    responses.add(
        responses.GET,
        BASE + SEARCH,
        json={
            "results": [
                {
                    "id": "12345",
                    "title": "Вебхуки провайдера",
                    "_links": {"base": BASE, "webui": "/spaces/SUP/pages/12345"},
                }
            ]
        },
        status=200,
    )

    found = confluence.search("формат вебхука", V1)

    sent = responses.calls[0].request.params
    assert sent["cql"] == 'type = "page" AND space = "SUP" AND text ~ "формат вебхука"'
    assert sent["limit"] == "4"
    assert found == [
        {
            "id": "12345",
            "title": "Вебхуки провайдера",
            "url": f"{BASE}/spaces/SUP/pages/12345",
            "excerpt": "",
        }
    ]


@responses.activate
def test_quotes_in_the_query_cannot_break_out_of_cql():
    responses.add(responses.GET, BASE + SEARCH, json={"results": []}, status=200)

    confluence.search('вебхук" OR space = "SECRET', V1)

    assert (
        responses.calls[0].request.params["cql"]
        == 'type = "page" AND space = "SUP" AND text ~ "вебхук\\" OR space = \\"SECRET"'
    )


@responses.activate
def test_v2_still_searches_through_v1():
    """У v2 полнотекстового поиска нет: писать через v2 и искать через v1 — норма."""
    responses.add(responses.GET, BASE + SEARCH, json={"results": []}, status=200)

    confluence.search("что угодно", V2)

    assert responses.calls[0].request.url.startswith(BASE + SEARCH)


@responses.activate
def test_wrapped_results_with_excerpts_are_understood():
    """`/rest/api/search` оборачивает страницу в `content` и кладёт рядом фрагмент."""
    responses.add(
        responses.GET,
        BASE + SEARCH,
        json={
            "results": [
                {
                    "content": {
                        "id": "42",
                        "title": "Оплата",
                        "_links": {"base": BASE, "webui": "/x/42"},
                    },
                    "excerpt": "ключ <b>идемпотентности</b> хранится сутки",
                }
            ]
        },
        status=200,
    )

    found = confluence.search("идемпотентность", V1)

    assert found[0]["id"] == "42"
    assert found[0]["excerpt"] == "ключ идемпотентности хранится сутки"


def test_an_empty_query_does_not_go_to_the_network():
    assert confluence.search("  ", V1) == []


# --------------------------------------------------------------------------
# Чтение страницы
# --------------------------------------------------------------------------
@responses.activate
def test_v1_reads_the_page_body():
    responses.add(
        responses.GET,
        f"{BASE}/rest/api/content/12345",
        json={
            "id": "12345",
            "title": "Формат вебхука",
            "body": {"storage": {"value": PAGE}},
            "_links": {"base": BASE, "webui": "/spaces/SUP/pages/12345"},
        },
        status=200,
    )

    page = confluence.fetch_page("12345", V1)

    assert page["title"] == "Формат вебхука"
    assert page["url"] == f"{BASE}/spaces/SUP/pages/12345"
    assert page["text"].startswith("Формат вебхука")
    assert "- раз" in page["text"]
    assert responses.calls[0].request.params["expand"] == "body.storage,version"


@responses.activate
def test_v2_asks_for_storage_format():
    responses.add(
        responses.GET,
        f"{BASE}/api/v2/pages/12345",
        json={"id": "12345", "title": "Т", "body": {"storage": {"value": "<p>текст</p>"}}},
        status=200,
    )

    page = confluence.fetch_page("12345", V2)

    assert page["text"] == "текст"
    assert responses.calls[0].request.params["body-format"] == "storage"


@responses.activate
def test_long_page_is_capped_and_says_so():
    responses.add(
        responses.GET,
        f"{BASE}/rest/api/content/1",
        json={"id": "1", "title": "Т", "body": {"storage": {"value": "<p>" + "я" * 500 + "</p>"}}},
        status=200,
    )

    page = confluence.fetch_page("1", V1)

    assert len(page["text"]) == V1.read_max_chars
    assert page["truncated"]
    assert "обрезан" in confluence.format_page(page)


def test_a_page_id_is_validated_before_the_request_goes_out():
    """Заголовок страницы вместо идентификатора — обычная ошибка модели."""
    with pytest.raises(confluence.ConfluenceError, match="идентификатор"):
        confluence.fetch_page("Вебхуки провайдера", V1)


# --------------------------------------------------------------------------
# Storage format -> текст
# --------------------------------------------------------------------------
def test_macro_content_survives():
    """
    В `code`-макросе лежит ровно тот фрагмент конфига, ради которого страницу
    и открыли. Вырезать его вместе с обёрткой значит прочитать страницу зря.
    """
    storage = (
        '<ac:structured-macro ac:name="code"><ac:plain-text-body>'
        "<![CDATA[TIMEOUT_S=30]]></ac:plain-text-body></ac:structured-macro>"
    )

    assert confluence.storage_to_text(storage) == "TIMEOUT_S=30"


def test_entities_come_back_as_characters():
    assert confluence.storage_to_text("<p>a &amp; b &lt; c</p>") == "a & b < c"


def test_blocks_become_lines_and_blank_runs_collapse():
    text = confluence.storage_to_text("<p>раз</p><p></p><p></p><p>два</p>")

    assert text == "раз\n\nдва"


def test_round_trip_keeps_the_content():
    """Текст -> storage -> текст: разметка теряется, содержание остаётся."""
    original = "Заголовок\n\nАбзац про экспорт."
    back = confluence.storage_to_text(confluence.text_to_storage(original))

    assert "Заголовок" in back
    assert "Абзац про экспорт." in back
