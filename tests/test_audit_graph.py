"""
Полный прогон конвейера сверки на подделке вместо модели.

Проверяется то, ради чего собран пятый граф.

Первое: пакет читает и проверяет код. Значит, до первого обращения к модели уже
известно, есть ли что сверять и что в пакете формально сломано, — и прогон по
пустой папке обязан стоить ноль, а не три вызова с ответом «документов нет».

Второе: роли видят документы по именам. Находка «в требованиях сказано одно,
а в контракте другое» проверяется по имени документа; роль, которой пакет
приехал одним полотном, назвала бы вместо имени пересказ раздела.

Третье: заключению пакет не показывают. Это не экономия на мелочи — сообщение
роли собирается заново на каждом этапе, и пакет в нём самая большая часть.
"""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent import audit_graph, audit_roles, inputs

QUESTION = "Сверь пакет документации по выгрузке заказов."

TASK_DIR = "пакет"

REQUIREMENTS = """\
# Требования

## Функциональные

- ФТ-1. Заказ создаётся один раз по ключу идемпотентности.
- ФТ-2. Отчёт выгружается за период. Срок хранения TBD.
"""

API = """\
# Контракт API

POST /orders — создание заказа, закрывает ФТ-1.

```json
{"id": "o-1", "status": "created"}
```
"""

ARCHITECTURE = """\
# Архитектура

Сервис orders-api принимает POST /orders и закрывает NFR-9.
"""

PACKAGE = {
    "требования.md": REQUIREMENTS,
    "контракт.md": API,
    "архитектура.md": ARCHITECTURE,
}


def usage_meta(hit: int = 1600, miss: int = 64, output: int = 200) -> dict:
    return {
        "token_usage": {
            "prompt_cache_hit_tokens": hit,
            "prompt_cache_miss_tokens": miss,
            "completion_tokens": output,
        }
    }


def answer(role) -> AIMessage:
    return AIMessage(
        content=f"# {role.title}\n\nСодержание этапа {role.number}.",
        response_metadata=usage_meta(),
    )


ANSWERS = [answer(role) for role in audit_roles.ROLES]


@pytest.fixture(autouse=True)
def no_publishing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")


@pytest.fixture
def folder():
    """Папка задачи с пакетом. Корень уведён в tmp_path фикстурой conftest."""
    path = inputs.ensure_root() / TASK_DIR
    path.mkdir(parents=True)
    for name, text in PACKAGE.items():
        (path / name).write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def empty_folder():
    path = inputs.ensure_root() / "пусто"
    path.mkdir(parents=True)
    return path


def config(task: str = TASK_DIR, **extra) -> dict:
    return {"configurable": {"thread_id": "a-1", "input_dir": task, **extra}}


def run(*answers, conf: dict | None = None, question: str = QUESTION) -> dict:
    app = audit_graph.build_graph(llm=GenericFakeChatModel(messages=iter(answers))).compile()
    return app.invoke({"messages": [HumanMessage(question)]}, config=conf or config())


# --------------------------------------------------------------------------
# Полный конвейер
# --------------------------------------------------------------------------
def test_three_documents_come_out_and_the_package_is_read_once(folder):
    state = run(*ANSWERS)
    artifacts = state["artifacts"]

    for role in audit_roles.ROLES:
        assert role.title in artifacts[role.key]
    assert state["package"]["read"] is True
    assert state["package"]["names"] == sorted(PACKAGE)


def test_the_check_report_reaches_the_artifacts_before_the_model_speaks(folder):
    state = run(*ANSWERS)
    report = state["artifacts"][audit_roles.CHECKS]

    # Требование, объявленное и никем не использованное.
    assert "ФТ-2" in report
    # Ссылка на требование, которого нет ни в одном документе.
    assert "NFR-9" in report
    # Маршрут, известный двум документам, — трассировка, а не находка.
    assert "`POST /orders`" in report


def test_the_package_reaches_the_roles_with_document_names(folder):
    state = run(*ANSWERS)
    text = audit_roles.brief(audit_roles.FIRST, QUESTION, state["artifacts"])

    for name in PACKAGE:
        assert f"## Документ {name}" in text
    assert audit_roles.CHECKS_TITLE in text


def test_the_verdict_gets_the_findings_and_not_the_package(folder):
    """
    Пакет — самая большая часть сообщения, и заключение пишется по находкам,
    а не по источнику. Заплатить за него третий раз значит заплатить за уже
    разобранное.
    """
    state = run(*ANSWERS)
    text = audit_roles.brief(audit_roles.LAST, QUESTION, state["artifacts"])

    assert audit_roles.PACKAGE_TITLE not in text
    assert audit_roles.CHECKS_TITLE in text
    assert "Расхождения" in text


def test_a_second_turn_does_not_read_the_package_again(folder):
    """
    Документы у треда одни, находки по ним детерминированы, и второй прогон
    сверки дал бы ровно то же самое ценой похода на диск.
    """
    app = audit_graph.build_graph(
        llm=GenericFakeChatModel(messages=iter(ANSWERS + ANSWERS))
    ).compile(checkpointer=InMemorySaver())
    conf = config()
    first = app.invoke({"messages": [HumanMessage(QUESTION)]}, config=conf)
    for name in PACKAGE:
        (folder / name).write_text("удалено", encoding="utf-8")
    second = app.invoke({"messages": [HumanMessage("перепиши вердикт короче")]}, config=conf)

    assert second["artifacts"][audit_roles.PACKAGE] == first["artifacts"][audit_roles.PACKAGE]


# --------------------------------------------------------------------------
# Проверка входа
# --------------------------------------------------------------------------
def test_an_empty_folder_costs_nothing(empty_folder):
    """
    Модели не передано ни одного ответа: подделка бросит StopIteration на первом
    же обращении. Тест проходит ровно тогда, когда обращения не было.
    """
    state = run(conf=config(task="пусто"))

    assert state["artifacts"] == {}
    assert not state.get("usage")
    assert "Сверять нечего" in state["messages"][-1].content


def test_a_missing_picked_file_is_refused_by_name(folder):
    state = run(conf=config(input_file=["требования.md", "потерянный.md"]))

    message = state["messages"][-1].content
    assert "потерянный.md" in message
    assert "требования.md" not in message
    assert not state.get("usage")


def test_a_pasted_retelling_is_not_a_package(empty_folder):
    """
    Конвейер декомпозиции принимает аналитику, вставленную в сообщение: он её
    раскладывает. Этот проверяет документы друг против друга, и пересказ ему
    сверять не с чем.
    """
    state = run(
        conf=config(task="пусто"),
        question="Требования: заказ создаётся один раз. " * 40,
    )

    assert not state.get("usage")
    assert "не с чем" in state["messages"][-1].content
