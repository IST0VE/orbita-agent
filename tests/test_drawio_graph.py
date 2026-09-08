"""
Полный прогон конвейера схем на подделке вместо модели.

Проверяется то, чего нет у первого графа и ради чего собран второй.

Первое: схему читает код, а не роль. Значит, до первого обращения к модели уже
известно, есть ли о чём говорить, — и прогон по пустой папке обязан стоить ноль,
а не четыре вызова с ответом «данных нет».

Второе: данные схемы доезжают до каждой роли одинаковыми. Четыре специалиста,
насчитавшие на одной схеме четыре разных набора стрелок, — это ровно тот исход,
ради устранения которого разбор вынесен в отдельную ноду.

Третье: ревьюеру приезжает готовая сверка ссылок. Она считается арифметикой,
поэтому проверять её надо на выдуманном id, а не на правдоподобном тексте.
"""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage

from agent import diagram_roles, drawio_graph, inputs

QUESTION = "Опиши, что нарисовано на схеме, и собери страницу документации."

MODEL = """\
<mxGraphModel dx="800" dy="600">
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />
    <mxCell id="worker" value="Export Worker" style="rounded=1;" vertex="1" parent="1" />
    <mxCell id="db" value="PostgreSQL" style="shape=cylinder;" vertex="1" parent="1" />
    <mxCell id="e-write" value="INSERT export_jobs" style="" edge="1" parent="1" source="worker" target="db" />
    <mxCell id="e-lost" value="метрики?" style="" edge="1" parent="1" source="worker" />
  </root>
</mxGraphModel>"""

DIAGRAM = f'<mxfile host="test"><diagram id="d1" name="Выгрузка">{MODEL}</diagram></mxfile>'

TASK_DIR = "схема"


def usage_meta(hit: int = 1600, miss: int = 64, output: int = 200) -> dict:
    return {
        "token_usage": {
            "prompt_cache_hit_tokens": hit,
            "prompt_cache_miss_tokens": miss,
            "completion_tokens": output,
        }
    }


def answer(role, miss: int = 64) -> AIMessage:
    return AIMessage(
        content=f"# {role.title}\n\nСодержание этапа {role.number} [id: e-write].",
        response_metadata=usage_meta(miss=miss),
    )


ANSWERS = [answer(role) for role in diagram_roles.ROLES]


@pytest.fixture(autouse=True)
def no_publishing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")


@pytest.fixture
def folder():
    """Папка задачи со схемой. Корень уведён в tmp_path фикстурой conftest."""
    path = inputs.ensure_root() / TASK_DIR
    path.mkdir(parents=True)
    (path / "поток.drawio").write_text(DIAGRAM, encoding="utf-8")
    return path


def config(task: str = TASK_DIR, **extra) -> dict:
    return {"configurable": {"thread_id": "d-1", "input_dir": task, **extra}}


def run(*answers, conf: dict | None = None, question: str = QUESTION) -> dict:
    app = drawio_graph.build_graph(llm=GenericFakeChatModel(messages=iter(answers))).compile()
    return app.invoke({"messages": [HumanMessage(question)]}, config=conf or config())


# --------------------------------------------------------------------------
# Полный конвейер
# --------------------------------------------------------------------------
def test_every_role_leaves_its_document(folder):
    state = run(*ANSWERS)

    assert set(diagram_roles.KEYS) <= set(state["artifacts"])
    assert state["stage"] == diagram_roles.LAST.key
    assert state["usage"]["calls"] == len(diagram_roles.ROLES)


def test_the_thread_says_which_diagram_was_read(folder):
    """
    Разбор — это ход графа, и он обязан быть виден. Оператор, не понимающий,
    какой файл прочитан, не может доверять ни одному из четырёх документов.
    """
    state = run(*ANSWERS)

    note = state["messages"][1].content
    assert "поток.drawio" in note
    assert "полных 1" in note


def test_the_parsed_diagram_is_in_the_state(folder):
    state = run(*ANSWERS)

    assert state["diagram"]["nodes"] == 2
    assert state["diagram"]["complete_edges"] == 1
    assert state["diagram"]["incomplete_edges"] == 1


def test_operator_can_name_the_file(folder):
    """Схем в папке две — берётся названная, а не первая по алфавиту."""
    (folder / "вторая.drawio").write_text(
        DIAGRAM.replace("PostgreSQL", "ClickHouse"), encoding="utf-8"
    )

    state = run(*ANSWERS, conf=config(diagram="вторая.drawio"))

    assert state["diagram"]["name"] == "вторая.drawio"


# --------------------------------------------------------------------------
# Прогон без схемы
# --------------------------------------------------------------------------
def test_empty_folder_costs_nothing(folder):
    """
    Критерий приёмки: модель не вызывается ни разу. Ответ подделки остаётся
    неизрасходованным, счётчик вызовов — нулевой.
    """
    (folder / "поток.drawio").unlink()

    state = run(answer(diagram_roles.FIRST))

    assert state.get("usage", {}).get("calls", 0) == 0
    assert not set(diagram_roles.KEYS) & set(state.get("artifacts") or {})
    assert "Разбор схемы не выполнен" in state["messages"][-1].content


def test_broken_file_is_reported_to_the_operator(folder):
    (folder / "поток.drawio").write_bytes(bytes([0, 1, 2]) + b"binary junk")

    state = run(answer(diagram_roles.FIRST))

    assert state.get("usage", {}).get("calls", 0) == 0
    assert "не разбирается" in state["messages"][-1].content


def test_unknown_task_folder_does_not_crash_the_thread(folder):
    state = run(answer(diagram_roles.FIRST), conf=config(task="нет-такой-папки"))

    assert state.get("usage", {}).get("calls", 0) == 0
    assert "Разбор схемы не выполнен" in state["messages"][-1].content


# --------------------------------------------------------------------------
# Бюджет
# --------------------------------------------------------------------------
def test_budget_stops_the_pipeline_between_stages(
    folder, monkeypatch: pytest.MonkeyPatch
):
    """Копеечный лимит: разбор бесплатный, первая роль проходит, вторая — нет."""
    monkeypatch.setenv("BUDGET_USD_PER_THREAD", "0.01")
    expensive = answer(diagram_roles.FIRST, miss=1_000_000)
    unused = answer(diagram_roles.ROLES[1])

    state = run(expensive, unused)

    assert list(state["artifacts"]) == [
        diagram_roles.DIAGRAM,
        diagram_roles.IDS,
        diagram_roles.FIRST.key,
    ]
    assert "Бюджет треда исчерпан" in state["messages"][-1].content
    assert diagram_roles.ROLES[1].title in state["messages"][-1].content


# --------------------------------------------------------------------------
# Что видит роль
# --------------------------------------------------------------------------
def test_data_of_the_diagram_reaches_the_first_role(folder):
    state = run(*ANSWERS)
    brief = diagram_roles.brief(diagram_roles.FIRST, "задача", state["artifacts"])

    assert "complete_edges_are_facts" in brief
    assert "Export Worker" in brief


def test_variable_parts_stay_at_the_end_of_the_message():
    """
    Задача, схема и документы этапов — в таком порядке и только в конце.
    Порядок здесь не косметика: любой сдвиг начала сообщения оплачивается
    промахом кеша на каждом следующем этапе.
    """
    brief = diagram_roles.brief(
        diagram_roles.BY_KEY["components"],
        "задача",
        {diagram_roles.DIAGRAM: "{}", "survey": "разбор"},
    )

    assert brief.index(f"# {diagram_roles.TASK_TITLE}") == 0
    assert brief.index(f"# {diagram_roles.DIAGRAM_TITLE}") < brief.index(
        f"# {diagram_roles.STAGE_TITLE}"
    )


def test_role_is_told_that_a_stage_did_not_happen():
    brief = diagram_roles.brief(
        diagram_roles.BY_KEY["components"], "задача", {diagram_roles.DIAGRAM: "{}"}
    )

    assert "Этап не выполнен" in brief


# --------------------------------------------------------------------------
# Сверка ссылок
# --------------------------------------------------------------------------
def review_brief(page: str, ids: str = "worker db e-write e-lost") -> str:
    return diagram_roles.brief(
        diagram_roles.BY_KEY["review"],
        "задача",
        {diagram_roles.DIAGRAM: "{}", diagram_roles.IDS: ids, "page": page},
    )


def test_invented_reference_is_handed_to_the_reviewer():
    brief = review_brief("Worker шлёт события в Kafka [id: kafka-topic].")

    assert "НЕ НАЙДЕНО" in brief
    assert "kafka-topic" in brief
    assert "blocker" in brief


def test_real_references_are_not_reported_as_defects():
    brief = review_brief("Worker пишет в PostgreSQL [id: e-write].")

    assert "НЕ НАЙДЕНО" not in brief
    assert "Все указывают на существующие элементы" in brief


def test_document_without_references_cannot_be_checked_at_all():
    """
    Документ без ссылок выглядит как обычный текст и проверке не поддаётся
    в принципе. Молчать об этом нельзя: непроверяемое ревью хуже отсутствующего.
    """
    brief = review_brief("Worker пишет в базу данных.")

    assert "нет ни одной ссылки" in brief
    assert "blocker" in brief


# --------------------------------------------------------------------------
# Публикация
# --------------------------------------------------------------------------
def test_a_page_per_stage_goes_out(folder, monkeypatch: pytest.MonkeyPatch):
    """
    Страниц столько, сколько этапов состоялось. Данные схемы и список id лежат
    в тех же артефактах, но страницами не становятся: публикуются роли,
    а не всё, что нода положила в состояние.
    """
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "1")
    monkeypatch.setenv("PUBLISH_TARGET", "file")

    state = run(*ANSWERS)
    pages = state["publication"]["pages"]

    assert [page["role"] for page in pages] == list(diagram_roles.KEYS)
    assert state["publication"]["status"] == "created"
    assert all(page["status"] == "created" for page in pages)


def test_the_document_is_signed_by_the_pipeline_that_made_it(
    folder, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PUBLISH_TARGET", "none")

    state = run(*ANSWERS)

    assert diagram_roles.PIPELINE.byline in state["document"]
