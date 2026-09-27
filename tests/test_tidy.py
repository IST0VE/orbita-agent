"""
Форма документа этапа: таблицы выравнивает код, чужой алфавит — короткий запрос.

Обе поломки нашлись в черновике конвейера подготовки 27 сентября 2026: строки
таблицы пробелов вышли длиннее шапки, а в плане стояло «Сделать服务端-часть».
"""

from __future__ import annotations

from agent import tidy

TABLE = "| № | Чего не знаем |\n| --- | --- |\n| П1 | формат | блокирует |"


def test_a_row_longer_than_the_header_widens_the_header():
    assert tidy.normalize_tables(TABLE) == (
        "| № | Чего не знаем |  |\n| --- | --- | --- |\n| П1 | формат | блокирует |"
    )


def test_a_short_row_is_padded():
    text = "| A | B | C |\n| --- | --- | --- |\n| 1 | 2 |\n| 1 | 2 | 3 |"

    assert tidy.normalize_tables(text).split("\n")[2] == "| 1 | 2 |  |"


def test_an_even_table_is_left_as_it_was():
    text = "Текст\n\n|A|B|\n|:--|--:|\n|1|2|\n\nЕщё текст"

    assert tidy.normalize_tables(text) == text


def test_code_is_not_a_table():
    text = "```\n| A |\n| --- | --- |\n| 1 | 2 | 3 |\n```"

    assert tidy.normalize_tables(text) == text


def test_an_escaped_pipe_stays_inside_its_cell():
    text = "| A | B |\n| --- | --- |\n| a\\|b | c | d |"

    assert tidy.normalize_tables(text).split("\n")[2] == "| a\\|b | c | d |"


def test_foreign_lines_are_found_outside_code():
    text = "Сделать服务端-часть\nобычная строка\n```\n中文 в коде\n```\n表"

    assert tidy.foreign_lines(text) == [0, 5]


def test_russian_typography_is_not_foreign():
    assert tidy.foreign_lines("Цитата «так» — №5, 🟡 статус…") == []


def test_a_repair_is_accepted_only_whole():
    assert tidy.parse_repair('["Сделать серверную часть"]', 1) == ["Сделать серверную часть"]
    assert tidy.parse_repair('```json\n["а", "б"]\n```', 2) == ["а", "б"]
    assert tidy.parse_repair('<think>думаю</think>["а"]', 1) == ["а"]
    # Не той длины, не строки, с оставшимся иероглифом, не JSON — не принимается.
    assert tidy.parse_repair('["а"]', 2) is None
    assert tidy.parse_repair("[1]", 1) is None
    assert tidy.parse_repair('["服务"]', 1) is None
    assert tidy.parse_repair("исправил", 1) is None


def test_repaired_lines_go_back_to_their_places():
    text = "первая\nСделать服务端\nтретья"

    assert tidy.apply_repair(text, [1], ["Сделать серверную"]) == "первая\nСделать серверную\nтретья"


def test_a_latin_letter_inside_a_russian_word_is_foreign():
    """Живой прогон 27 сентября: «по aутентификации» с латинской a и «стенограммеRequirements»."""
    text = "страница по aутентификации\nв стенограммеRequirements\nnginx-пользователи, tab'а, API-шки, SHA-512"

    assert tidy.foreign_lines(text) == [0, 1]


def test_a_repair_that_keeps_a_mixed_word_is_refused():
    assert tidy.parse_repair('["по aутентификации"]', 1) is None
    assert tidy.parse_repair('["по аутентификации"]', 1) == ["по аутентификации"]


def test_source_tags_are_fixed_by_code():
    text = (
        "как сказано [ФАЙл встреча.txt], "
        "см. [ФАЙЛ speech_to_text (9).doc.txt) против [Wiki 123] и [jira ORB-1]; "
        "верный [ФАЙЛ speech_to_text (9).doc.txt] не трогается"
    )

    assert tidy.fix_tags(text) == (
        "как сказано [ФАЙЛ встреча.txt], "
        "см. [ФАЙЛ speech_to_text (9).doc.txt] против [WIKI 123] и [JIRA ORB-1]; "
        "верный [ФАЙЛ speech_to_text (9).doc.txt] не трогается"
    )
