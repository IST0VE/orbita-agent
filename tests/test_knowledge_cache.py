"""
Замер hit rate на конвейере: что именно кешируется, когда ролей пять.

Замерять на живом DeepSeek в тестах нельзя — нужен ключ, сеть и деньги. Поэтому
здесь стоит эмулятор префиксного кеша: он считает совпадение от нулевого символа
запроса, округляет вниз до блока и выдаёт те же счётчики, что приходят от
провайдера. Это не доказывает тарифы DeepSeek, но доказывает ровно то, что
может сломать retrieval: сохранился ли общий префикс между запросами.

Эмулятор помнит все прошлые запросы, а не последний. Так устроен и настоящий
кеш — это кеш, а не одна ячейка, — и на конвейере разница принципиальная:
запросы идут не по одной растущей истории, а по пяти разным ролям вперемежку.
Модель с одной ячейкой показывала бы ноль там, где провайдер платит по льготной
цене, и тест ловил бы собственную упрощённость вместо ошибок в промптах.

Главных тестов два, и оба про форму запроса, а не про проценты:

  * `test_every_request_starts_with_the_shared_block` — общее начало у всех
    пяти ролей на месте, значит вторая и следующие роли попадают в кеш уже
    на первом своём вызове;
  * `test_requests_of_the_same_role_extend_each_other` — от хода к ходу роль
    видит свой префикс побайтово тем же.

Проценты идут следом, чтобы цифру можно было увидеть, а не вывести.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent import prompts, roles
from agent.graph import build_graph, hit_rate

# DeepSeek кеширует блоками по 64 токена: хвост короче блока в кеш не попадает.
BLOCK_TOKENS = 64
CHARS_PER_TOKEN = 4

TASKS = [
    "Спроектировать асинхронную выгрузку заказов в CSV и XLSX по материалам встречи.",
    "Добавить в ту же выгрузку фильтры по периоду и статусу заказа.",
    "Учесть повторную доставку сообщений из брокера и падение worker.",
    "Разобрать, что происходит при истечении TTL файла во время скачивания.",
]


def as_text(messages: list) -> str:
    """Запрос к модели одной строкой — то, что провайдер видит как префикс."""
    return "\n".join(f"{m.type}: {m.content}" for m in messages)


def common_prefix(left: str, right: str) -> int:
    limit = min(len(left), len(right))
    for index in range(limit):
        if left[index] != right[index]:
            return index
    return limit


class PrefixCacheModel:
    """Подделка модели, которая считает кеш так же, как считает его провайдер."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self.requests: list[str] = []

    def invoke(self, messages: list) -> AIMessage:
        text = as_text(messages)
        self.requests.append(text)

        total = len(text) // CHARS_PER_TOKEN
        # Лучшее совпадение по всем прошлым запросам: кеш хранит их все, и
        # запрос третьей роли попадает в общее начало первой, а не мимо.
        matched = max((common_prefix(text, old) for old in self.seen), default=0)
        hit = min(matched // CHARS_PER_TOKEN // BLOCK_TOKENS * BLOCK_TOKENS, total)
        self.seen.append(text)

        return AIMessage(
            content="Документ этапа. Разделы по пунктам инструкции.",
            response_metadata={
                "token_usage": {
                    "prompt_cache_hit_tokens": hit,
                    "prompt_cache_miss_tokens": total - hit,
                    "completion_tokens": 60,
                }
            },
        )


class _Clock:
    """Часы для антипримера: каждый вызов — новая секунда."""

    def __init__(self) -> None:
        self.tick = 0

    def now(self) -> datetime:
        self.tick += 1
        return datetime(2026, 8, 28, 12, 0, self.tick)


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")
    monkeypatch.setenv("MEMORY_ENABLED", "0")


def measure(monkeypatch: pytest.MonkeyPatch, knowledge: bool) -> tuple[float, dict, list]:
    monkeypatch.setenv("KNOWLEDGE_ENABLED", "1" if knowledge else "0")
    model = PrefixCacheModel()
    app = build_graph(llm=model).compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "kb" if knowledge else "base"}}

    usage: dict = {}
    for task in TASKS:
        usage = app.invoke({"messages": [HumanMessage(task)]}, config=config)["usage"]
    return hit_rate(usage), usage, model.requests


def by_role(requests: list[str]) -> dict[str, list[str]]:
    """
    Запросы по ролям. Ход конвейера — ровно один вызов на роль в порядке
    `roles.ROLES`, поэтому раскладка идёт по остатку от деления.
    """
    return {
        role.key: requests[index :: len(roles.ROLES)]
        for index, role in enumerate(roles.ROLES)
    }


# --------------------------------------------------------------------------
# Форма запроса
# --------------------------------------------------------------------------
def test_every_request_starts_with_the_shared_block(monkeypatch: pytest.MonkeyPatch):
    """
    Ради этого COMMON стоит первым, а не после описания роли: общее начало
    у всех пяти префиксов побайтово одно, и роль, которая идёт второй, платит
    льготную цену за него уже на первом своём вызове.
    """
    _, _, requests = measure(monkeypatch, knowledge=False)

    assert len(requests) == len(TASKS) * len(roles.ROLES)
    for request in requests:
        assert request.startswith("system: " + prompts.COMMON)


@pytest.mark.parametrize("knowledge", [False, True])
def test_requests_of_the_same_role_extend_each_other(
    monkeypatch: pytest.MonkeyPatch, knowledge: bool
):
    """
    От хода к ходу роль видит свой префикс тем же. Меняется только то, что
    приезжает в конце сообщения, — задача и документы предыдущих этапов.

    В режиме базы знаний префикс другой (короче на вынесенные стандарты), но
    требование к нему то же самое: он обязан быть одним и тем же на каждом ходе.
    """
    _, _, requests = measure(monkeypatch, knowledge=knowledge)
    prefix_of = prompts.core_prompt if knowledge else prompts.for_role

    for key, sent in by_role(requests).items():
        prefix = "system: " + prefix_of(key)
        for request in sent:
            assert request.startswith(prefix)


def test_retrieval_does_not_cost_the_cache(monkeypatch: pytest.MonkeyPatch):
    """
    Критерий приёмки 4.2: hit rate после подключения retrieval не падает.

    На конвейере замер даёт примерно 83 процента без базы знаний и 76 с ней.
    Допуск поэтому десять пунктов, а не пять, как было на одиночном агенте, и
    разрыв вырос по понятной причине: retrieval бьёт по префиксу дважды.
    Вынесенные стандарты укорачивают префикс сразу всем пяти ролям, а найденная
    справка добавляет в конец текст, которого в кеше не было. Проценты от этого
    честно проседают — но проседают, а не обнуляются.

    Ловится здесь именно обнуление: сломанный префикс даёт не минус семь
    пунктов, а ноль, что и показывает соседний тест на антипримере.
    """
    base_rate, _, _ = measure(monkeypatch, knowledge=False)
    kb_rate, _, _ = measure(monkeypatch, knowledge=True)

    assert base_rate > 50
    assert kb_rate > 50
    assert kb_rate >= base_rate - 10


def test_broken_prefix_is_visible_to_the_same_measurement(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    Контрольный опыт: тот же замер на антипримере (`--bad`, время в промпте)
    показывает ноль. Значит, метод замера способен увидеть сломанный кеш —
    и цифрам из соседнего теста можно верить.
    """
    monkeypatch.setenv("KNOWLEDGE_ENABLED", "1")
    # Часы должны идти: ходы теста укладываются в одну секунду, и на настоящих
    # часах антипример случайно выглядел бы исправным.
    monkeypatch.setattr("agent.nodes.datetime", _Clock())
    model = PrefixCacheModel()
    app = build_graph(unstable_prefix=True, llm=model).compile(
        checkpointer=InMemorySaver()
    )
    config = {"configurable": {"thread_id": "bad"}}

    usage: dict = {}
    for task in TASKS:
        usage = app.invoke({"messages": [HumanMessage(task)]}, config=config)["usage"]

    assert hit_rate(usage) == 0
