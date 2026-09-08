"""
Полный прогон конвейера на подделке вместо модели.

`GenericFakeChatModel` отдаёт заранее заданную последовательность сообщений,
поэтому все пять ролей проходят без ключа, без сети и без единого секрета —
ровно то, что должно происходить в CI.

Модель передаётся в `build_graph(llm=...)` и достаётся всем ролям сразу: узлы
порождаются из `roles.ROLES` одной фабрикой, и подделка подставляется туда же,
куда в жизни подставляется настоящий клиент.
"""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent import roles
from agent.graph import (
    build_graph,
    estimate_cost,
    make_role_node,
    make_role_router,
    without_tool_calls,
)

CONFIG = {"configurable": {"thread_id": "t-1"}}

TASK = "Спроектировать асинхронную выгрузку данных по материалам встречи."


def usage_meta(hit: int, miss: int, output: int) -> dict:
    return {
        "token_usage": {
            "prompt_cache_hit_tokens": hit,
            "prompt_cache_miss_tokens": miss,
            "completion_tokens": output,
        }
    }


def fake_model(*messages):
    return GenericFakeChatModel(messages=iter(messages))


def stage_answer(role: roles.Role, hit: int = 1600, miss: int = 64) -> AIMessage:
    """Ответ роли: текст, который станет её документом."""
    return AIMessage(
        content=f"# {role.title}\n\nСодержание этапа {role.number}.",
        response_metadata=usage_meta(hit=hit, miss=miss, output=200),
    )


PIPELINE = [stage_answer(role) for role in roles.ROLES]

ASKS_FOR_FILE = AIMessage(
    content="",
    tool_calls=[
        {
            "name": "read_task_file",
            "args": {"name": "встреча.md"},
            "id": "call-1",
        }
    ],
    response_metadata=usage_meta(hit=1472, miss=128, output=30),
)


def run(*messages, task: str = TASK, config: dict | None = None):
    app = build_graph(llm=fake_model(*messages)).compile()
    return app.invoke({"messages": [HumanMessage(task)]}, config=config or CONFIG)


# --------------------------------------------------------------------------
# Маршрутизация
# --------------------------------------------------------------------------
def test_analyst_goes_to_tools_when_the_model_asked_for_a_file():
    router = make_role_router(roles.FIRST)

    assert router({"messages": [ASKS_FOR_FILE]}) == "tools"


def test_analyst_goes_to_the_next_gate_on_a_plain_answer():
    router = make_role_router(roles.FIRST)

    assert router({"messages": [PIPELINE[0]]}) == "gate_api"


def test_last_role_goes_to_remember_not_to_a_gate():
    router = make_role_router(roles.LAST)

    assert router({"messages": [PIPELINE[-1]]}) == "remember"


# --------------------------------------------------------------------------
# Полный конвейер
# --------------------------------------------------------------------------
def test_every_role_leaves_its_document(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")

    result = run(*PIPELINE)

    assert list(result["artifacts"]) == list(roles.KEYS)
    assert all(text.strip() for text in result["artifacts"].values())
    assert result["stage"] == roles.LAST.key


def test_pipeline_calls_the_model_once_per_role(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")

    assert run(*PIPELINE)["usage"]["calls"] == len(roles.ROLES)


def test_task_is_remembered_apart_from_the_message(monkeypatch: pytest.MonkeyPatch):
    """
    Задачу читают все пять ролей, поэтому нода контекста кладёт её отдельным
    полем, а не заставляет каждую вычленять её из истории по-своему.
    """
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")

    assert run(*PIPELINE)["task"] == TASK


def test_analyst_reads_a_file_and_the_pipeline_continues(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")

    from agent import inputs

    folder = inputs.ensure_root() / "задача"
    folder.mkdir(parents=True)
    (folder / "встреча.md").write_text("выгрузка асинхронная", encoding="utf-8")

    result = run(
        ASKS_FOR_FILE,
        *PIPELINE,
        config={"configurable": {"thread_id": "t-1", "input_dir": "задача"}},
    )

    answer = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert "выгрузка асинхронная" in answer.content
    # Обращение к инструменту документом не становится: роль не ответила,
    # а спросила.
    assert list(result["artifacts"]) == list(roles.KEYS)
    assert result["usage"]["calls"] == len(roles.ROLES) + 1


def test_usage_is_summed_over_every_role(monkeypatch: pytest.MonkeyPatch):
    """Редьюсер `_merge_usage` складывает счётчики по всему треду, а не по ноде."""
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")

    usage = run(*PIPELINE)["usage"]

    assert usage == {
        "calls": 5,
        "cache_hit": 1600 * 5,
        "cache_miss": 64 * 5,
        "cache_write": 0,
        "output": 200 * 5,
    }


def test_cost_lands_in_state_next_to_the_counters(monkeypatch: pytest.MonkeyPatch):
    """
    Тариф лежит в `.env` и остаётся на сервере, поэтому деньги в состояние
    кладёт граф. Считаться они обязаны по накопленным за тред счётчикам,
    а не по последнему вызову модели.
    """
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")

    result = run(*PIPELINE)

    assert result["cost"]["usd"] == pytest.approx(estimate_cost(result["usage"]))
    assert result["cost"]["calls"] == 5
    assert result["cost"]["input"] == (1600 + 64) * 5


# --------------------------------------------------------------------------
# Прерванный конвейер
# --------------------------------------------------------------------------
def test_budget_stops_the_pipeline_and_keeps_what_is_ready(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    Ворота бюджета стоят перед каждой ролью. Кончились деньги после первой —
    конвейер не падает и не публикует пустоту: уезжает то, что уже написано.
    """
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")
    monkeypatch.setenv("BUDGET_USD_PER_THREAD", "0.0000001")

    result = run(*PIPELINE)

    assert list(result["artifacts"]) == [roles.FIRST.key]
    assert result["usage"]["calls"] == 1
    assert "Бюджет треда исчерпан" in result["messages"][-1].content
    assert "Не выполнены этапы" in result["messages"][-1].content


def test_empty_answer_does_not_become_a_document(monkeypatch: pytest.MonkeyPatch):
    """Пустой ответ модели — не документ этапа, а повод не делать вид, что он есть."""
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")

    silent = AIMessage(content="", response_metadata=usage_meta(10, 10, 0))
    result = run(silent, *PIPELINE[1:])

    assert roles.FIRST.key not in result["artifacts"]


CHATTY = AIMessage(
    content="# Контракт API\n\nEndpoints, коды ошибок, идемпотентность.",
    tool_calls=[{"name": "list_task_files", "args": {}, "id": "call-2"}],
    additional_kwargs={
        "tool_calls": [
            {
                "id": "call-2",
                "type": "function",
                "function": {"name": "list_task_files", "arguments": "{}"},
            }
        ]
    },
    response_metadata=usage_meta(hit=1600, miss=64, output=200),
)


def test_stray_tool_call_does_not_cost_a_role_its_document(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    Роль без файлов зовёт инструмент вместе с текстом — живая модель так и
    делает, потому что схемы привязаны ко всем пятерым ради общего префикса.

    Вести её в `tools` нельзя: вход ей собирается заново, и ответа инструмента
    она уже не увидит. Значит вызов снимается, а написанное остаётся документом
    этапа — иначе этап, за который заплачено, пропадал бы молча, а следующая
    роль получала бы «этап не выполнен».
    """
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")

    result = run(PIPELINE[0], CHATTY, *PIPELINE[2:])

    assert result["artifacts"]["api"] == CHATTY.content
    assert list(result["artifacts"]) == list(roles.KEYS)


def test_stray_tool_call_does_not_stay_in_the_history(monkeypatch: pytest.MonkeyPatch):
    """
    Висячий вызов нельзя оставлять и в истории: провайдер требует за каждым
    вызовом ответ инструмента и отобьёт ошибкой запрос того, кто эту историю
    потом прочитает. Снимается обе формы — разобранная и сырая.
    """
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")

    answers = [m for m in run(PIPELINE[0], CHATTY, *PIPELINE[2:])["messages"] if m.type == "ai"]

    assert not any(m.tool_calls for m in answers)
    assert not any("tool_calls" in m.additional_kwargs for m in answers)


def test_analyst_keeps_its_tool_call(monkeypatch: pytest.MonkeyPatch):
    """У роли с файлами вызов остаётся: ей есть куда пойти за ответом."""
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")

    node = make_role_node(roles.FIRST, llm=fake_model(ASKS_FOR_FILE))
    update = node({"messages": [HumanMessage(TASK)], "task": TASK}, CONFIG)

    assert update["messages"][0].tool_calls
    assert "artifacts" not in update


def test_a_clean_answer_is_not_copied():
    """Ответ без вызовов возвращается как есть: копировать нечего."""
    assert without_tool_calls(PIPELINE[0]) is PIPELINE[0]


# --------------------------------------------------------------------------
# Публикация: граф доходит до конца в любом случае
# --------------------------------------------------------------------------
def test_publish_is_disabled_by_the_switch(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")

    publication = run(*PIPELINE)["publication"]

    assert publication["status"] == "disabled"
    assert "CONFLUENCE_PUBLISH" in publication["reason"]


def test_publish_is_skipped_when_credentials_are_absent(monkeypatch: pytest.MonkeyPatch):
    """
    Рубильник включён, цель — Confluence, а реквизитов нет: этап не идёт
    в сеть и не падает.

    Цель задана явно: по умолчанию (`auto`) проект без Confluence кладёт
    документы на диск, и это проверяется отдельно в test_publishers.py.
    """
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "1")
    monkeypatch.setenv("PUBLISH_TARGET", "confluence")

    publication = run(*PIPELINE)["publication"]

    assert publication["status"] == "skipped"
    assert "CONFLUENCE_BASE_URL" in publication["reason"]


def test_document_carries_every_stage(monkeypatch: pytest.MonkeyPatch):
    """Разметка storage: цель — Confluence, хотя публиковать и запрещено."""
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")
    monkeypatch.setenv("PUBLISH_TARGET", "confluence")

    document = run(*PIPELINE)["document"]

    for role in roles.ROLES:
        assert f"<h2>{role.number}. {role.title}</h2>" in document
    assert "<h2>Задача</h2>" in document
    assert "<h2>Расход токенов по треду</h2>" in document
    assert "Cache hit rate" in document


def test_every_stage_gets_its_own_page(monkeypatch: pytest.MonkeyPatch):
    """
    Страниц столько, сколько состоялось этапов. Склеить их в одну было бы
    проще для кода: человек приходит по ссылке за контрактом API, а не за
    всей историей проекта.
    """
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "1")

    pages = run(*PIPELINE, task="Асинхронная выгрузка")["publication"]["pages"]

    assert [page["role"] for page in pages] == list(roles.KEYS)
    assert pages[0]["title"] == "Orbita: Асинхронная выгрузка [t-1] — 01 Системные требования"
    assert pages[-1]["title"].endswith("— 05 Ревью и финальная версия")


def test_the_whole_document_says_the_task_once(monkeypatch: pytest.MonkeyPatch):
    """
    Документ треда собирается заново, а не склейкой пяти страниц. На страницах
    задача и расход повторяются намеренно — их открывают по отдельной ссылке, —
    но человек, которому документ показывают перед публикацией, читает его
    подряд, и пять одинаковых шапок там только мешают.
    """
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")
    monkeypatch.setenv("PUBLISH_TARGET", "confluence")

    document = run(*PIPELINE)["document"]

    assert document.count("<h2>Задача</h2>") == 1
    assert document.count("<h2>Расход токенов по треду</h2>") == 1
    assert document.count("Страница собрана автоматически") == 1
    # А сами этапы — все пять.
    assert sum(f"<h2>{r.number}. {r.title}</h2>" in document for r in roles.ROLES) == 5
