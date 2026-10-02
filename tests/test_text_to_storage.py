"""
Задача 1.4: `text_to_storage` — рукописный конвертер текста модели в storage
format Confluence.

Это самое хрупкое место в проекте: ошибка здесь даёт невалидный XML, и его
не примет уже wiki, а не наш код. Поэтому у каждого случая один общий
критерий — результат обязан разбираться как XML-фрагмент.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from agent.confluence import text_to_storage

# Storage format — это XHTML с макросами Confluence в пространстве имён ac.
# Чтобы фрагмент можно было разобрать отдельно, оборачиваем его в корень
# с объявленными префиксами.
_NS = (
    'xmlns:ac="http://example.com/ac" '
    'xmlns:ri="http://example.com/ri"'
)


def parse(fragment: str) -> ET.Element:
    """Разобрать фрагмент storage format. Падает, если XML невалиден."""
    return ET.fromstring(f"<root {_NS}>{fragment}</root>")


def test_paragraphs_and_headings():
    out = text_to_storage("## Заголовок\n\nПростой абзац.")
    root = parse(out)
    assert root.find("h3").text == "Заголовок"
    assert root.find("p").text == "Простой абзац."


def test_unclosed_code_fence_is_closed_anyway():
    """
    Модель оборвала ответ на середине блока кода — закрывающих бэктиков нет.
    Конвертер обязан закрыть макрос сам, иначе фрагмент нельзя разобрать.
    """
    out = text_to_storage("Пример:\n```\ncurl https://api\n")

    assert "ac:structured-macro" in out
    assert out.count("<![CDATA[") == out.count("]]>") == 1
    parse(out)


def test_cdata_terminator_inside_code_does_not_break_the_macro():
    """
    Последовательность `]]>` внутри кода закрыла бы CDATA раньше времени.
    Приём стандартный: разорвать её на две секции.
    """
    out = text_to_storage("```\nif (a[b[i]]>0) { }\n```")

    assert "]]]]><![CDATA[>" in out
    parse(out)


def test_ordered_list_switching_to_bullet_closes_previous_list():
    """
    Между списками нет пустой строки. Если предыдущий <ol> не закрыть,
    получится <ol><li>…<ul>…</ol> — вложенность разъедется, XML не разберётся.
    """
    out = text_to_storage("1. первый\n2. второй\n- третий")
    root = parse(out)

    assert len(root.findall("ol/li")) == 2
    assert len(root.findall("ul/li")) == 1
    assert out.index("</ol>") < out.index("<ul>")


def test_list_is_closed_at_the_end_of_text():
    out = text_to_storage("- один\n- два")
    root = parse(out)
    assert [li.text for li in root.findall("ul/li")] == ["один", "два"]


def test_special_characters_escaped_but_markup_survives():
    """
    `<` и `&` экранируются, а **жирный** и `код` обязаны остаться разметкой:
    это единственные два формата, которые конвертер понимает.
    """
    out = text_to_storage("Если a < b & c — это **важно**, см. `docs`")
    root = parse(out)

    assert "&lt;" in out
    assert "&amp;" in out
    paragraph = root.find("p")
    assert paragraph.find("strong").text == "важно"
    assert paragraph.find("code").text == "docs"


def test_angle_brackets_in_code_fence_are_not_markup():
    """Внутри CDATA экранирование не нужно — текст должен доехать как есть."""
    out = text_to_storage("```\n<div class='x'> & </div>\n```")

    assert "<div class='x'> & </div>" in out
    parse(out)


@pytest.mark.parametrize("value", ["", "   ", "\n\n", None])
def test_empty_input_gives_empty_output(value):
    """Пустая строка и None не должны ни падать, ни рождать мусорную разметку."""
    out = text_to_storage(value)
    assert out.strip() == ""
    parse(out)


def test_markdown_table_becomes_native_xhtml_between_list_and_heading():
    root = parse(text_to_storage(
        "- до\n| Поле | Значение |\n| :--- | ---: |\n"
        "| **Имя** | `<tag>` & текст |\n## После"
    ))
    assert [node.tag for node in root] == ["ul", "table", "h3"]
    assert [cell.text for cell in root.findall("table/tbody/tr/th")] == ["Поле", "Значение"]
    assert root.find("table/tbody/tr/td/strong").text == "Имя"
    assert root.find("table/tbody/tr/td/code").text == "<tag>"


def test_table_without_outer_pipes_preserves_escaped_pipe_and_empty_cells():
    root = parse(text_to_storage("A | B\n--- | ---\na\\|b | c\n| d | |"))
    rows = root.findall("table/tbody/tr")
    assert [cell.text for cell in rows[1]] == ["a|b", "c"]
    assert len(rows[2]) == 2
    assert rows[2][0].text == "d"


def test_pipes_in_code_and_prose_do_not_create_tables():
    root = parse(text_to_storage("a | b\nnot a separator\n```\n| A | B |\n| --- | --- |\n```"))
    assert root.find("table") is None
    assert root.find("p").text == "a | b"


def test_a_row_longer_than_the_header_stays_in_the_table():
    """
    27 сентября 2026 таблица пробелов вышла с шапкой в пять колонок и строками
    в шесть, и все строки остались на странице текстом с «|».
    """
    root = parse(text_to_storage("| № | Чего не знаем |\n| --- | --- |\n| П1 | формат | блокирует |"))

    rows = root.findall("table/tbody/tr")
    assert len(rows) == 2
    assert len(rows[0]) == 3
    assert rows[1][2].text == "блокирует"
    assert root.find("p") is None


def test_links_become_clickable():
    root = parse(text_to_storage(
        "См. [страницу](https://wiki.example.com/x?a=1&b=2) и https://jira.example.com/browse/ORB-1."
    ))

    links = root.findall("p/a")
    assert [link.get("href") for link in links] == [
        "https://wiki.example.com/x?a=1&b=2",
        "https://jira.example.com/browse/ORB-1",
    ]
    assert links[0].text == "страницу"
    # Точка в конце предложения — не часть адреса.
    assert "".join(root.find("p").itertext()).endswith("ORB-1.")


@pytest.mark.parametrize("markup", [
    "<https://wiki.example.com/pages/12345>",
    "**https://wiki.example.com/pages/12345**",
    "**[страница](https://wiki.example.com/pages/12345)**",
])
def test_formatted_links_are_valid_storage(markup):
    root = parse(text_to_storage(markup))

    assert root.find(".//a").get("href") == "https://wiki.example.com/pages/12345"
    if markup.startswith("**"):
        assert root.find("p/strong/a") is not None


def test_a_url_in_a_link_label_does_not_create_a_nested_anchor():
    root = parse(text_to_storage(
        "[**см.** https://wiki.example.com/1](https://wiki.example.com/2)"
    ))

    assert len(root.findall(".//a")) == 1
    assert root.find("p/a").get("href") == "https://wiki.example.com/2"
    assert root.find("p/a/strong").text == "см."


def test_inline_code_preserves_literal_markdown_and_urls():
    root = parse(text_to_storage("`**https://wiki.example.com/?a=1&b=2**`"))

    assert root.find("p/code").text == "**https://wiki.example.com/?a=1&b=2**"
    assert root.find(".//a") is None
    assert root.find(".//strong") is None


@pytest.mark.parametrize("url", [
    "https://wiki.example.com/?a=1&",
    "https://wiki.example.com/path_(example)",
    "https://wiki.example.com/?a=**value**",
])
def test_url_characters_are_escaped_without_becoming_markup(url):
    root = parse(text_to_storage(url))

    assert root.find("p/a").get("href") == url
    assert root.find("p/a").text == url


def test_link_label_is_escaped_and_cannot_inject_html():
    root = parse(text_to_storage('[<img src="x"/>](https://wiki.example.com/?x=1&y=2)'))

    assert root.find("p/a").text == '<img src="x"/>'
    assert root.find(".//img") is None
