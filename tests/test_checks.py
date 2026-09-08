"""
Сверка пакета кодом: то, что обязано считаться одинаково на каждом прогоне.

Эти проверки существуют ради одного утверждения: формальный дефект дешевле
найти арифметикой, чем вызовом модели. Утверждение держится, только пока
арифметика точна, поэтому здесь проверяется не «нашлось что-то похожее»,
а конкретные ключи, конкретные степени и конкретные списки.

Отдельно проверяется отказ. Пакет без идентификаторов требований — это не
успешная сверка с пустым результатом: трассировать его нечем, и сказать об
этом обязана сверка, а не читатель отчёта.
"""

from __future__ import annotations

from agent import checks


def kinds(report: dict) -> list[str]:
    return [finding["kind"] for finding in report["findings"]]


def by_kind(report: dict, kind: str) -> dict:
    return next(finding for finding in report["findings"] if finding["kind"] == kind)


# --------------------------------------------------------------------------
# Трассировка
# --------------------------------------------------------------------------
def test_requirement_is_declared_where_it_starts_a_line_and_mentioned_elsewhere():
    """
    Объявление и упоминание — разные вещи, и различить их можно позицией.
    Требование живёт там, где оно начинает пункт списка или строку таблицы;
    ссылаются на него из середины предложения.
    """
    report = checks.run(
        {
            "требования.md": "## Функциональные\n\n- ФТ-1. Заказ создаётся один раз.",
            "api.md": "Endpoint реализует ФТ-1 и возвращает 201.",
        }
    )
    [item] = report["requirements"]
    assert item["key"] == "ФТ-1"
    assert item["declared_in"] == ["требования.md"]
    assert item["mentioned_in"] == ["api.md"]
    assert item["covered"] is True
    assert "requirement_without_design" not in kinds(report)


def test_requirement_nobody_refers_to_is_a_requirement_without_design():
    report = checks.run(
        {
            "требования.md": "- ФТ-1. Заказ.\n- ФТ-2. Возврат.",
            "api.md": "Endpoint реализует ФТ-1.",
        }
    )
    finding = by_kind(report, "requirement_without_design")
    assert finding["severity"] == checks.MAJOR
    assert "ФТ-2" in finding["text"]
    assert "ФТ-1" not in finding["text"]


def test_reference_to_a_requirement_that_nobody_declared_is_a_blocker():
    """
    Решение, обоснованное несуществующим требованием, — худший вид расхождения:
    оно выглядит обоснованным. Поэтому blocker, а не major.
    """
    report = checks.run(
        {
            "требования.md": "- ФТ-1. Заказ.",
            "архитектура.md": "Компонент закрывает ФТ-1 и NFR-9.",
        }
    )
    finding = by_kind(report, "orphan_requirement")
    assert finding["severity"] == checks.BLOCKER
    assert "NFR-9" in finding["text"]
    assert finding["where"] == ["архитектура.md"]


def test_requirement_declared_in_two_documents_is_a_minor_finding():
    report = checks.run(
        {
            "требования.md": "- ФТ-1. Заказ.",
            "копия.md": "- ФТ-1. Заказ, но иначе.",
        }
    )
    finding = by_kind(report, "requirement_declared_twice")
    assert finding["severity"] == checks.MINOR
    assert sorted(finding["where"]) == ["копия.md", "требования.md"]


def test_package_without_identifiers_says_so_instead_of_reporting_success():
    """
    Конвейер аналитики нумерует требования списком, а не ключами, и такой пакет
    сюда придёт. Молчание сверки на нём читалось бы как «всё сошлось».
    """
    report = checks.run({"требования.md": "1. Заказ создаётся один раз.\n2. Возврат."})
    assert report["requirements"] == []
    finding = by_kind(report, "no_requirement_ids")
    assert finding["severity"] == checks.MAJOR
    assert "трассировать" in finding["text"].lower()


def test_a_reference_wrapped_onto_a_new_line_is_not_a_declaration():
    """
    Markdown в проекте свёрстан по ширине, и перенос регулярно ставит
    идентификатор в начало строки. Считать это объявлением — значит превратить
    ссылку-сироту в объявленное требование и потерять находку уровня blocker
    молча: она не станет слабее, она исчезнет.
    """
    report = checks.run(
        {
            "req.md": "- ФТ-1. Заказ.",
            "arch.md": "Алерт на рост очереди — по ФТ-1 и\nNFR-9.",
        }
    )
    assert "NFR-9" in by_kind(report, "orphan_requirement")["text"]


def test_an_identifier_that_opens_a_paragraph_is_still_a_declaration():
    """Требования нумеруют не только списком: абзац с ключа — тоже объявление."""
    report = checks.run(
        {
            "req.md": "## Требования\n\nФТ-1. Заказ создаётся один раз.\n",
            "api.md": "Endpoint закрывает ФТ-1.",
        }
    )
    [item] = report["requirements"]
    assert item["declared_in"] == ["req.md"]
    assert item["mentioned_in"] == ["api.md"]


def test_requirements_are_sorted_by_number_not_by_string():
    report = checks.run(
        {"req.md": "- ФТ-2. Раз.\n- ФТ-10. Два.\n- ФТ-9. Три.", "api.md": "ФТ-2 ФТ-9 ФТ-10"}
    )
    assert [item["key"] for item in report["requirements"]] == ["ФТ-2", "ФТ-9", "ФТ-10"]


def test_section_numbers_and_bare_digits_are_not_requirements():
    """Без дефиса ключа нет: «раздел 3» и «US 2» — не требования."""
    report = checks.run({"doc.md": "См. раздел 3, пункт 2 и US 2."})
    assert report["requirements"] == []


# --------------------------------------------------------------------------
# Примеры JSON
# --------------------------------------------------------------------------
def test_broken_json_example_is_found_and_named_by_document():
    report = checks.run(
        {
            "api.md": '```json\n{"id": 1,}\n```',
            "req.md": "- ФТ-1. Заказ.",
        }
    )
    finding = by_kind(report, "broken_json")
    assert finding["severity"] == checks.MAJOR
    assert finding["where"] == ["api.md"]
    assert "блок 1" in finding["text"]


def test_valid_json_and_other_fences_produce_no_finding():
    report = checks.run(
        {
            "api.md": '```json\n{"id": 1}\n```\n\n```python\nnot json at all(\n```',
            "req.md": "- ФТ-1. Заказ.\n\nРешение в api.md для ФТ-1.",
        }
    )
    assert "broken_json" not in kinds(report)


# --------------------------------------------------------------------------
# Маршруты
# --------------------------------------------------------------------------
def test_endpoints_are_collected_across_documents_and_the_trailing_slash_is_the_same_route():
    report = checks.run(
        {
            "api.md": "POST /orders создаёт заказ.",
            "архитектура.md": "Сервис принимает POST /orders/ и пишет в базу.",
        }
    )
    assert report["endpoints"] == {"POST /orders": ["api.md", "архитектура.md"]}


def test_route_known_to_one_document_only_is_reported_when_others_are_shared():
    report = checks.run(
        {
            "api.md": "POST /orders\nGET /orders/{id}",
            "архитектура.md": "Обрабатывает POST /orders.",
        }
    )
    finding = by_kind(report, "endpoint_in_one_document")
    assert finding["severity"] == checks.MINOR
    assert "GET /orders/{id}" in finding["text"]


def test_sentence_punctuation_does_not_split_one_route_into_two():
    """
    «Принимает POST /orders.» и `POST /orders` — один маршрут. Точка в конце
    предложения делала из него второй, и пакет расходился сам с собой молча:
    сверка отчитывалась, что всё сошлось, потому что сравнивала разные строки.
    """
    report = checks.run(
        {
            "api.md": "POST /orders создаёт заказ.",
            "арх.md": "Сервис принимает POST /orders.",
            "данные.md": "Запись идёт по POST /orders,",
        }
    )
    assert report["endpoints"] == {"POST /orders": ["api.md", "арх.md", "данные.md"]}


def test_a_single_document_package_has_no_route_findings():
    """Пакет из одного контракта — не находка: связывать маршруты не с чем."""
    report = checks.run({"api.md": "POST /orders\nGET /orders"})
    assert "endpoint_in_one_document" not in kinds(report)


# --------------------------------------------------------------------------
# Пометки и пустой пакет
# --------------------------------------------------------------------------
def test_placeholders_are_counted_by_kind():
    report = checks.run({"план.md": "Срок TBD, ключ TODO, объём ??? — уточнить у заказчика."})
    finding = by_kind(report, "placeholder")
    assert finding["severity"] == checks.MINOR
    for label in ("TBD", "TODO", "???", "уточнить"):
        assert label in finding["text"]


def test_empty_documents_are_dropped_and_an_empty_package_is_a_blocker():
    report = checks.run({"пусто.md": "   ", "тоже.md": ""})
    assert report["documents"] == []
    assert by_kind(report, "empty_package")["severity"] == checks.BLOCKER


def test_findings_are_sorted_with_blockers_first():
    report = checks.run(
        {
            "req.md": "- ФТ-1. Заказ. Срок TBD.",
            "arch.md": "Решение по ФТ-1 и по NFR-9.",
        }
    )
    severities = [finding["severity"] for finding in report["findings"]]
    assert severities == sorted(severities, key=[checks.BLOCKER, checks.MAJOR, checks.MINOR].index)
    assert report["findings"][0]["kind"] == "orphan_requirement"


# --------------------------------------------------------------------------
# Текст для роли
# --------------------------------------------------------------------------
def test_block_carries_the_matrix_and_the_findings():
    report = checks.run(
        {
            "req.md": "- ФТ-1. Заказ.",
            "api.md": "POST /orders закрывает ФТ-1.",
        }
    )
    block = checks.as_block(report)
    assert "| ФТ-1 | req.md | api.md |" in block
    assert "`POST /orders`" in block
    assert "Находки сверки" in block


def test_block_says_plainly_that_nothing_formal_was_found():
    """
    «Дефектов не найдено» без границы проверки читается как «пакет верен».
    Границу обязан назвать тот, кто проверял.
    """
    block = checks.as_block(
        checks.run({"req.md": "- ФТ-1. Заказ.", "api.md": "Решение по ФТ-1 готово."})
    )
    assert "проверяется арифметикой" in block
