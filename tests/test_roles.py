"""
Описание конвейера: порядок ролей, кто чей результат видит, что уезжает в промпт.

Конвейер описан данными, а не пятью почти одинаковыми функциями, и это
переносит цену ошибки: опечатка в ключе роли теперь не «неправильный узел»,
а «узел, у которого нет префикса». Поэтому связь между `roles.ROLES`,
`prompts.ROLE_PROMPTS` и ключами состояния проверяется тестом.

Второе, что здесь проверяется, — форма сообщения роли. Всё переменное обязано
быть в его конце: задача, потом документы предыдущих этапов. Поменяй порядок —
и префикс сдвинется на каждом этапе, а вместе с ним уедет вся экономика.
"""

from __future__ import annotations

import pytest

from agent import prompts, roles


# --------------------------------------------------------------------------
# Связность описания
# --------------------------------------------------------------------------
def test_every_role_has_a_prefix_and_every_prefix_has_a_role():
    """Ключ роли — он же имя узла, он же ключ префикса. Разъехаться им нельзя."""
    assert set(roles.KEYS) == set(prompts.ROLE_PROMPTS)
    assert len(roles.ROLES) == len(roles.KEYS)  # дубликатов ключей нет


def test_numbers_are_sequential_and_unique():
    assert [role.number for role in roles.ROLES] == ["01", "02", "03", "04", "05"]


def test_each_role_sees_every_earlier_stage():
    """
    Ревьюер обязан видеть всё: половина его работы — искать противоречия между
    этапами, а по двум документам из четырёх их не найти.
    """
    for index, role in enumerate(roles.ROLES):
        assert role.needs == roles.KEYS[:index]


def test_only_the_first_role_reads_files():
    """
    Сырые материалы читает аналитик, остальные работают с его документом.
    Иначе конвейер разваливается на четыре независимых мнения о встрече —
    ровно то, ради устранения чего он и собран.
    """
    assert [role.key for role in roles.ROLES if role.reads_files] == [roles.FIRST.key]


def test_after_walks_the_pipeline_and_stops():
    assert roles.after(roles.FIRST).key == "api"
    assert roles.after(roles.LAST) is None


def test_unknown_key_is_refused_loudly():
    with pytest.raises(KeyError, match="неизвестная роль"):
        roles.by_key("аналитик")


# --------------------------------------------------------------------------
# Готовые этапы
# --------------------------------------------------------------------------
def test_done_keeps_the_pipeline_order_not_the_dictionary_order():
    """Словарь помнит порядок вставки, документ обязан идти по номерам этапов."""
    artifacts = {"review": "Р", "requirements": "Т", "data": "Д"}

    assert [role.key for role in roles.done(artifacts)] == [
        "requirements",
        "data",
        "review",
    ]


def test_blank_artifact_is_not_a_finished_stage():
    """Пустая строка — не документ: этап, который ничего не написал, не состоялся."""
    assert roles.done({"requirements": "   "}) == []
    assert roles.done(None) == []


# --------------------------------------------------------------------------
# Сообщение роли
# --------------------------------------------------------------------------
def test_task_comes_first_and_stages_follow():
    """
    Порядок здесь — это деньги. Задача и документы уезжают в КОНЕЦ запроса,
    после неподвижного префикса, и внутри сообщения сначала идёт то, что
    меняется реже.
    """
    text = roles.brief(
        roles.by_key("api"),
        "Спроектировать выгрузку",
        {"requirements": "Требования по выгрузке"},
    )

    assert text.index(roles.TASK_TITLE) < text.index(roles.STAGE_TITLE)
    assert "Спроектировать выгрузку" in text
    assert "Требования по выгрузке" in text


def test_first_role_gets_the_task_and_nothing_else():
    text = roles.brief(roles.FIRST, "Задача", {"api": "не должно попасть"})

    assert "не должно попасть" not in text
    assert roles.STAGE_TITLE not in text


def test_stages_arrive_in_pipeline_order():
    artifacts = {key: f"документ {key}" for key in roles.KEYS}
    text = roles.brief(roles.LAST, "Задача", artifacts)

    positions = [text.index(f"документ {key}") for key in roles.LAST.needs]
    assert positions == sorted(positions)


def test_missing_stage_is_named_not_silently_skipped():
    """
    Этап мог не состояться: кончился бюджет, оператор остановил конвейер.
    Молча отдать роли пустоту нельзя — она напишет документ по несуществующим
    требованиям и не скажет об этом ни слова.
    """
    text = roles.brief(roles.by_key("api"), "Задача", {})

    assert "Этап не выполнен" in text
    assert "прямо укажи в документе" in text


def test_stage_documents_are_not_shortened():
    """
    Ревьюер обязан видеть в точности то, что написал проектировщик. Пересказ
    был бы дешевле по токенам и означал бы, что ревьюят пересказ.
    """
    long_document = "строка требования\n" * 500
    text = roles.brief(roles.by_key("api"), "Задача", {"requirements": long_document})

    assert long_document.strip() in text
