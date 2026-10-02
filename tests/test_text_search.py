"""
Условие полнотекстового поиска: запрос как есть плюс основы его слов.

27 сентября 2026 роль поиска конвейера подготовки задала Confluence тринадцать
запросов. Все пять пустых были фразами в три–шесть слов, а однословный
`apiusers` нашёл ровно нужную страницу. Проверяется, что фраза теперь ищется и
по основам (`парол*` находит и «пароль», и «паролю»), а имя утилиты — как есть.
"""

from __future__ import annotations

import pytest

from agent import text_search


def quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("паролю", "парол*"),
        ("пароль", "парол*"),
        ("требования", "требован*"),
        ("пользователями", "пользовател*"),
        ("аутентификации", "аутентификац*"),
        ("интерфейс", "интерфейс*"),
        ("роли", "роли"),
        ("nginx", "nginx"),
        ("authgw-user-mgmt", "authgw-user-mgmt"),
    ],
)
def test_a_word_is_cut_to_its_stem(word: str, expected: str):
    assert text_search.stem(word) == expected


def test_service_words_are_not_searched():
    assert text_search.words("требования к паролю и доступу") == ["требования", "паролю", "доступу"]


def test_a_phrase_is_searched_as_is_or_by_its_stems():
    assert text_search.condition("требования к паролю", quote) == (
        '(text ~ "требования к паролю" OR (text ~ "требован*" AND text ~ "парол*"))'
    )


def test_a_single_name_goes_as_is():
    """У имени утилиты окончаний нет: усечение превратило бы его в чужие совпадения."""
    assert text_search.condition("apiusers", quote) == 'text ~ "apiusers"'


def test_a_quote_cannot_break_out_of_the_condition():
    condition = text_search.condition('пароль" OR space = "SECRET', quote)

    assert 'text ~ "пароль\\" OR space = \\"SECRET"' in condition
    assert 'space = "SECRET"' not in condition.replace('\\"', "")


def test_a_long_query_keeps_only_the_first_words():
    condition = text_search.condition("один два три четыре пять шесть семь", quote)

    assert condition.count(" AND ") == text_search.MAX_WORDS - 1
