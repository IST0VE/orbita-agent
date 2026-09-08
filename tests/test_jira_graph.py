"""
Контракт и полный автономный прогон графа Jira-декомпозиции.

Проверяется четыре вещи. Первая — связность описания: ключ роли это же имя узла,
ключ артефакта и ключ префикса, и разъехаться им нельзя. Вторая — что до
следующей роли доезжает в точности то, что написала предыдущая: ревьюер,
читающий пересказ черновика, не ревью делает, а сочиняет второй черновик.
Третья — то, ради чего этот граф отдельный: вход ему пишет другой человек,
поэтому пустой вход обязан стоить ноль, а не четыре вызова с backlog из `TBD`.

Четвёртая появилась вместе с обратным направлением: конвейер не заканчивается
документом, он заводит задачи в чужой системе. Всё, что стоит между текстом
модели и этим действием — чтение источника кодом, разбор карточек, вопрос про
проект, — проверяется отдельно и без сети.
"""

from __future__ import annotations

import json

import pytest
import responses
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent import confluence, inputs, jira_graph, jira_prompts, jira_roles, jira_writer

# Аналитика приезжает документом, а не просьбой: короткое сообщение этот граф
# не пускает дальше проверки входа, и тесту прогона нужен настоящий вход.
QUESTION = """\
Разложи готовую аналитику платёжного сценария на Jira-задачи.

Платёж инициируется из checkout-формы web-app и уходит в payments-api. Сервис
создаёт запись в таблице payments, отправляет запрос провайдеру и возвращает
идентификатор попытки. Ответ провайдера асинхронный: он приходит вебхуком, по
которому статус попытки переводится в захвачен либо отклонён. Повторная отправка
того же запроса не должна создавать вторую попытку — ключ идемпотентности
приходит от клиента и хранится сутки. Ошибки провайдера делятся на возвратные
и невозвратные: первые повторяются с выдержкой, вторые сразу отдаются клиенту.
Отчётность по попыткам собирает export worker раз в сутки.
"""

SHORT = "Разложи аналитику на задачи."

LINK = "Разложи https://wiki.example.com/wiki/spaces/DOCS/pages/123456/Платежи на задачи"

TASK_DIR = "аналитика"

CARDS = {
    "issues": [
        {"id": "EPIC-1", "type": "Epic", "summary": "Платежи"},
        {"id": "TASK-1", "type": "Task", "summary": "Идемпотентность", "parent": "EPIC-1"},
    ]
}


def usage_meta() -> dict:
    return {
        "token_usage": {
            "prompt_cache_hit_tokens": 100,
            "prompt_cache_miss_tokens": 50,
            "completion_tokens": 20,
        }
    }


def answer(role) -> AIMessage:
    """Ответ роли. У последней он машинный: по нему заводятся задачи."""
    if role.key == jira_roles.LAST.key:
        body = "# Карточки\n\n```json\n" + json.dumps(CARDS, ensure_ascii=False) + "\n```"
    else:
        body = f"# {role.title}\n\nРезультат {role.number}: payments-api / backend."
    return AIMessage(content=body, response_metadata=usage_meta())


ANSWERS = [answer(role) for role in jira_roles.ROLES]


@pytest.fixture(autouse=True)
def no_publishing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")


@pytest.fixture
def folder():
    """Папка задачи с аналитикой файлом. Корень уведён в tmp_path фикстурой conftest."""
    path = inputs.ensure_root() / TASK_DIR
    path.mkdir(parents=True)
    (path / "аналитика.md").write_text(QUESTION, encoding="utf-8")
    return path


@pytest.fixture
def wiki(monkeypatch: pytest.MonkeyPatch):
    """Настроенный Confluence с одной страницей. В сеть тест не ходит."""
    # Адрес нужен по-настоящему: по нему считается, ведёт ли ссылка оператора
    # в чужую вики, — а это и есть половина смысла этого чтения.
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://wiki.example.com")
    monkeypatch.setattr(confluence, "missing_vars", lambda: [])
    monkeypatch.setattr(
        confluence,
        "fetch_page",
        lambda page_id, settings=None: {
            "id": page_id,
            "title": "Платежи",
            "url": f"https://wiki.example.com/pages/{page_id}",
            "text": QUESTION,
            "truncated": False,
        },
    )


def run(*answers, question: str = QUESTION, task: str = "") -> dict:
    model = GenericFakeChatModel(messages=iter(answers))
    app = jira_graph.build_graph(llm=model).compile()
    return app.invoke(
        {"messages": [HumanMessage(question)]},
        config={"configurable": {"thread_id": "jira-1", "input_dir": task}},
    )


# --------------------------------------------------------------------------
# Связность описания
# --------------------------------------------------------------------------
def test_every_role_has_a_prefix_and_every_prefix_has_a_role():
    """Ключ роли — он же имя узла, он же ключ префикса. Разъехаться им нельзя."""
    assert set(jira_roles.KEYS) == set(jira_prompts.ROLE_PROMPTS)
    assert len(jira_roles.ROLES) == len(jira_roles.KEYS)  # дубликатов ключей нет


def test_numbers_are_sequential_and_unique():
    assert [role.number for role in jira_roles.ROLES] == ["01", "02", "03", "04"]


def test_writing_roles_see_the_source_and_every_earlier_stage():
    """Сверять покрытие можно только против источника: он нужен всем троим."""
    for index, role in enumerate(jira_roles.ROLES[:-1]):
        assert role.needs == (jira_roles.SOURCE,) + jira_roles.KEYS[:index]


def test_the_carding_role_sees_only_the_final_backlog():
    """
    Карточки собираются по финальной версии и только по ней.

    Черновик рядом с финалом даёт ровно одну ошибку — карточку по задаче,
    которую ревью удалило, — и заводится она уже в чужом проекте.
    """
    assert jira_roles.LAST.needs == ("review",)


def test_no_role_reads_files_by_itself():
    """
    Источник читает код, а не модель.

    Адрес страницы или имя файла уже написаны в запросе, и оплаченный вызов
    ради известного аргумента ничего не выбирает. Но важнее другое: все этапы
    обязаны видеть один и тот же документ, а не каждый свою выборку из папки.
    """
    assert not any(role.reads_files for role in jira_roles.ROLES)


def test_all_roles_share_one_prefix():
    """
    Общий блок обязан стоять в начале каждого префикса и быть побайтово одним
    и тем же. На этом держится кеш: вторая и следующие роли попадают в него уже
    на первом своём вызове, и разное начало обнулило бы всю экономику.
    """
    for key in jira_roles.KEYS:
        assert jira_prompts.for_role(key).startswith(jira_prompts.COMMON)


def test_final_review_requires_coverage_and_implementation_order():
    """Финальный документ — не список замечаний, а готовый к refinement backlog."""
    prompt = jira_prompts.for_role("review")

    assert "Порядок реализации" in prompt
    assert "Матрица сервисов и компонентов" in prompt
    assert "Матрица покрытия" in prompt


def test_the_carding_prompt_declares_the_machine_format():
    """Блок JSON — контракт с `jira_plan.py`, и объявлен он должен быть явно."""
    prompt = jira_prompts.for_role("issues")

    assert "```json" in prompt
    assert '"issues"' in prompt


# --------------------------------------------------------------------------
# Полный конвейер
# --------------------------------------------------------------------------
def test_every_stage_produces_an_artifact():
    state = run(*ANSWERS)

    assert list(state["artifacts"]) == list(jira_roles.KEYS)
    assert state["stage"] == "create"  # последний узел конвейера — заведение задач
    assert state["usage"]["calls"] == len(jira_roles.ROLES)


def test_later_stages_receive_the_original_outputs():
    artifacts = {"source": "ТОЧНЫЙ ИСТОЧНИК", "scope": "ТОЧНАЯ КАРТА", "backlog": "ТОЧНЫЙ ЧЕРНОВИК"}
    text = jira_roles.brief(jira_roles.BY_KEY["review"], "ЗАПРОС", artifacts)

    assert text.index("ЗАПРОС") < text.index("ТОЧНЫЙ ИСТОЧНИК")
    assert text.index("ТОЧНЫЙ ИСТОЧНИК") < text.index("ТОЧНАЯ КАРТА")
    assert text.index("ТОЧНАЯ КАРТА") < text.index("ТОЧНЫЙ ЧЕРНОВИК")


def test_a_missing_source_is_named_instead_of_masked():
    """Роль обязана знать, что документа нет, а не выдавать за него запрос."""
    text = jira_roles.brief(jira_roles.FIRST, "ЗАПРОС", {})

    assert "Отдельного документа нет" in text


# --------------------------------------------------------------------------
# Чтение источника
# --------------------------------------------------------------------------
def test_a_confluence_link_is_read_by_code(wiki):
    update = jira_graph.source_node({"messages": [HumanMessage(LINK)]}, {})

    assert update["source"]["kind"] == "confluence"
    assert update["source"]["url"].endswith("/123456")
    assert "checkout-формы" in update["artifacts"][jira_roles.SOURCE]


def test_a_link_to_another_wiki_is_reported_to_the_roles(wiki):
    """Страница 123456 есть в любой вики, и прочитана будет наша."""
    update = jira_graph.source_node(
        {"messages": [HumanMessage("разложи https://other.example.com/pages/123456")]}, {}
    )

    assert "ВНИМАНИЕ" in update["artifacts"][jira_roles.SOURCE]
    assert "other.example.com" in update["artifacts"][jira_roles.SOURCE]


def test_an_unreadable_page_does_not_stop_the_run(monkeypatch: pytest.MonkeyPatch):
    def boom(page_id, settings=None):
        raise confluence.ConfluenceError("нет прав на пространство")

    monkeypatch.setattr(confluence, "missing_vars", lambda: [])
    monkeypatch.setattr(confluence, "fetch_page", boom)

    update = jira_graph.source_node({"messages": [HumanMessage(LINK)]}, {})

    assert update["source"]["read"] is False
    assert "нет прав" in update["artifacts"][jira_roles.SOURCE]


def test_the_named_file_is_the_only_one_read(folder):
    """Оператор сказал, какой документ раскладывать, — соседние тут лишние."""
    (folder / "лишнее.md").write_text("посторонний документ", encoding="utf-8")

    update = jira_graph.source_node(
        {"messages": [HumanMessage("разложи аналитика.md на задачи")]},
        {"configurable": {"input_dir": TASK_DIR}},
    )

    assert update["source"]["names"] == ["аналитика.md"]
    assert "посторонний" not in update["artifacts"][jira_roles.SOURCE]


def test_without_a_named_file_every_text_file_is_read(folder):
    (folder / "второй.md").write_text("второй документ", encoding="utf-8")

    update = jira_graph.source_node(
        {"messages": [HumanMessage(SHORT)]}, {"configurable": {"input_dir": TASK_DIR}}
    )

    assert len(update["source"]["names"]) == 2
    assert "второй документ" in update["artifacts"][jira_roles.SOURCE]


# --------------------------------------------------------------------------
# Файл, выбранный в интерфейсе
#
# Третий способ назвать источник, и самый определённый из трёх. Раньше их было
# два: ссылка на страницу и имя файла, найденное подстрокой в тексте запроса.
# Второй работал только если оператор догадался написать имя файла словами, а
# первый молча выигрывал у папки — и прогон с готовым документом на диске
# заворачивался сообщением про ненастроенный Confluence. Не всё живёт в wiki.
# --------------------------------------------------------------------------
def test_the_chosen_file_is_the_only_one_read(folder):
    (folder / "лишнее.md").write_text("посторонний документ", encoding="utf-8")

    update = jira_graph.source_node(
        {"messages": [HumanMessage(SHORT)]},
        {"configurable": {"input_dir": TASK_DIR, "input_file": "аналитика.md"}},
    )

    assert update["source"]["names"] == ["аналитика.md"]
    assert "посторонний" not in update["artifacts"][jira_roles.SOURCE]
    assert "выбран оператором" in update["artifacts"][jira_roles.SOURCE]


def test_the_chosen_file_beats_a_link_in_the_text(folder, wiki):
    """
    Ссылка в запросе бывает попутной — «см. также», — а файл оператор выбирает
    руками и под этот прогон. Confluence настроен, и всё равно читается файл.
    """
    update = jira_graph.source_node(
        {"messages": [HumanMessage(LINK)]},
        {"configurable": {"input_dir": TASK_DIR, "input_file": "аналитика.md"}},
    )

    assert update["source"]["kind"] == "files"
    assert update["source"]["names"] == ["аналитика.md"]


def test_a_chosen_set_of_files_is_read_whole_and_nothing_else(folder):
    """
    Аналитика редко живёт одним файлом: требования без контракта API
    раскладываются в задачи, которых нет. Выбирается комплект, читается
    комплект — и ровно он, без соседей по папке.
    """
    (folder / "контракт.md").write_text("## Контракт API\n\nPOST /export", encoding="utf-8")
    (folder / "лишнее.md").write_text("посторонний документ", encoding="utf-8")

    update = jira_graph.source_node(
        {"messages": [HumanMessage(SHORT)]},
        {
            "configurable": {
                "input_dir": TASK_DIR,
                "input_file": ["аналитика.md", "контракт.md"],
            }
        },
    )

    assert update["source"]["names"] == ["аналитика.md", "контракт.md"]
    assert "Контракт API" in update["artifacts"][jira_roles.SOURCE]
    assert "посторонний" not in update["artifacts"][jira_roles.SOURCE]
    assert "выбраны оператором" in update["artifacts"][jira_roles.SOURCE]


def test_one_lost_file_of_a_set_stops_the_run_like_all_of_them(folder):
    """
    Прочитать три документа из четырёх и разложить их как весь вход значит
    выдать неполный backlog за полный. Отказ называет пропавший файл, а не
    весь выбор: остальные на месте.
    """
    reason = jira_graph.missing_analysis(
        {"messages": [HumanMessage(SHORT)]},
        {
            "configurable": {
                "input_dir": TASK_DIR,
                "input_file": ["аналитика.md", "удалённый.md"],
            }
        },
    )

    assert "удалённый.md" in reason and "аналитика.md" not in reason
    assert "деньги не потрачены" in reason


def test_a_chosen_file_that_is_gone_is_refused_before_the_model(folder):
    """
    Тихо прочитать вместо выбранного документа всю папку хуже, чем не прочитать
    ничего: оператор увидит backlog и будет уверен, что он по его файлу.
    """
    reason = jira_graph.missing_analysis(
        {"messages": [HumanMessage(SHORT)]},
        {"configurable": {"input_dir": TASK_DIR, "input_file": "удалённый.md"}},
    )

    assert "удалённый.md" in reason
    assert "деньги не потрачены" in reason


def test_a_chosen_file_passes_admission_even_without_confluence(folder):
    """Ссылка в тексте больше не перебивает выбранный файл на проверке входа."""
    assert (
        jira_graph.missing_analysis(
            {"messages": [HumanMessage(LINK)]},
            {"configurable": {"input_dir": TASK_DIR, "input_file": "аналитика.md"}},
        )
        == ""
    )


def test_analysis_pasted_into_the_message_needs_no_document():
    update = jira_graph.source_node({"messages": [HumanMessage(QUESTION)]}, {})

    assert update["source"] == {"kind": "message", "read": True}
    assert "artifacts" not in update


def test_a_follow_up_turn_does_not_read_the_source_again(wiki):
    """Документ у треда один; ходить за ним на каждую правку — платить задержкой."""
    state = {"source": {"kind": "confluence", "read": True}, "messages": [HumanMessage(LINK)]}

    assert jira_graph.source_node(state, {}) == {}


# --------------------------------------------------------------------------
# Проверка входа
#
# Ради неё граф и живёт отдельно: аналитику пишет другой человек, и её может
# просто не приехать. Прогон по пустому входу — это четыре оплаченных вызова
# и документ из `TBD`, то есть худший из ответов: он выглядит работой.
# --------------------------------------------------------------------------
def test_empty_input_costs_nothing():
    """Модель подделке не передана вовсе: любой её вызов уронил бы тест."""
    state = run(question=SHORT)

    assert not state.get("artifacts")
    assert not state["usage"]  # счётчиков нет вовсе: считать было нечего
    assert "Раскладывать нечего" in state["messages"][-1].content


def test_analysis_in_a_file_is_enough(folder):
    """В сообщении одна строка, но аналитика лежит файлом — это годный вход."""
    state = run(*ANSWERS, question=SHORT, task=TASK_DIR)

    assert list(state["artifacts"]) == [jira_roles.SOURCE, *jira_roles.KEYS]


def test_a_link_is_enough_when_confluence_is_configured(wiki):
    assert jira_graph.missing_analysis({"messages": [HumanMessage(LINK)]}, {}) == ""


def test_a_link_without_confluence_is_refused_before_the_model():
    """Ссылка есть, читать нечем — это отказ, а не четыре вызова по одной строке."""
    reason = jira_graph.missing_analysis({"messages": [HumanMessage(LINK)]}, {})

    assert "CONFLUENCE_BASE_URL" in reason


def test_a_picture_in_the_folder_is_not_analysis():
    """Нечитаемый файл материалом не считается: роль всё равно его не прочтёт."""
    path = inputs.ensure_root() / TASK_DIR
    path.mkdir(parents=True)
    (path / "схема.png").write_bytes(b"PNG")

    state = run(question=SHORT, task=TASK_DIR)

    assert not state["usage"]  # счётчиков нет вовсе: считать было нечего


def test_a_follow_up_turn_is_not_an_empty_input():
    """
    На втором ходе треда аналитика уже разобрана, и короткая правка — обычное
    сообщение. Проверка входа, срабатывающая здесь, ломала бы диалог.
    """
    state = {"artifacts": {"scope": "карта"}, "messages": [HumanMessage(SHORT)]}

    assert jira_graph.missing_analysis(state, {}) == ""


# --------------------------------------------------------------------------
# Заведение задач
#
# Единственное место проекта с внешним побочным эффектом сильнее публикации
# страницы. Проверяется прежде всего то, чего оно НЕ делает без спроса.
# --------------------------------------------------------------------------
@pytest.fixture
def tracker(monkeypatch: pytest.MonkeyPatch):
    """Настроенная Jira без сети: заведение подменено, вызов записывается."""
    monkeypatch.setenv("JIRA_BASE_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_TOKEN", "tok")
    calls: list[dict] = []

    def create_issues(plan, project, *, settings=None, source=""):
        calls.append({"project": project, "items": [item.local for item in plan.items]})
        return {
            "status": "created",
            "project": project,
            "created": [
                {
                    "local": item.local,
                    "key": f"{project}-{index + 1}",
                    "url": f"https://jira.example.com/browse/{project}-{index + 1}",
                    "type": item.type,
                    "summary": item.summary,
                }
                for index, item in enumerate(plan.items)
            ],
            "failed": [],
            "warnings": [],
        }

    monkeypatch.setattr(jira_writer, "create_issues", create_issues)
    monkeypatch.setattr(jira_writer, "projects", lambda: [{"key": "ORB", "name": "Orbita"}])
    # Схему проекта спрашивает показ черновиков: тип карточки в окне должен
    # быть тем, под которым она заведётся. Сети здесь нет, отдаёт заглушка.
    monkeypatch.setattr(jira_writer, "issue_types", lambda project, settings=None: ["Epic", "Task"])
    return calls


def fenced(cards: dict) -> str:
    """Документ последнего этапа: карточки машинным блоком, как их пишет роль."""
    return "```json\n" + json.dumps(cards, ensure_ascii=False) + "\n```"


def state_with_cards(document: str | None = None) -> dict:
    body = document or fenced(CARDS)
    return {"artifacts": {jira_roles.LAST.key: body}, "messages": []}


def test_issues_are_created_in_the_configured_project(
    tracker, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("JIRA_PROJECT_KEY", "ORB")
    monkeypatch.setenv("JIRA_CREATE_REQUIRE_APPROVAL", "0")

    update = jira_graph.create_node(state_with_cards(), {})

    assert tracker == [{"project": "ORB", "items": ["EPIC-1", "TASK-1"]}]
    assert update["issues"]["status"] == "created"
    assert "ORB-1" in update["messages"][0].content
    assert "https://jira.example.com/browse/ORB-2" in update["messages"][0].content


def test_the_project_chosen_for_the_thread_wins_over_the_setting(
    tracker, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("JIRA_PROJECT_KEY", "ORB")
    monkeypatch.setenv("JIRA_CREATE_REQUIRE_APPROVAL", "0")

    jira_graph.create_node(state_with_cards(), {"configurable": {"jira_project": "pay"}})

    assert tracker[0]["project"] == "PAY"


def test_nothing_is_created_without_an_answer_from_the_operator(
    tracker, monkeypatch: pytest.MonkeyPatch
):
    """По умолчанию спрашивают всегда: отменить заведённые задачи нельзя."""
    monkeypatch.setenv("JIRA_PROJECT_KEY", "ORB")
    monkeypatch.setattr(jira_graph, "interrupt", lambda payload: {"decision": "rejected"})

    update = jira_graph.create_node(state_with_cards(), {})

    assert tracker == []
    assert update["issues"]["status"] == "rejected"


def test_native_review_handoff_does_not_create_issues(tracker, monkeypatch):
    from agent import jira_forms

    monkeypatch.setenv("JIRA_PROJECT_KEY", "ORB")
    monkeypatch.setattr(jira_graph, "interrupt", lambda payload: {"decision": "drafts", "project": "pay"})
    monkeypatch.setattr(jira_forms, "prepare", lambda plan, project, source="": {
        "status": "forms", "project": project, "drafts": [{"id": item.local} for item in plan.items]
    })
    result = jira_graph.create_node(state_with_cards(), {})
    assert tracker == []
    assert result["issues"]["status"] == "forms"
    assert result["issues"]["project"] == "PAY"
    assert len(result["issues"]["drafts"]) == 2


def test_the_operator_names_the_project_in_the_same_answer(
    tracker, monkeypatch: pytest.MonkeyPatch
):
    """Проект — единственный вопрос конвейера, и задаётся он один раз."""
    asked: list[dict] = []

    def ask(payload):
        asked.append(payload)
        return {"decision": "approved", "project": "pay"}

    monkeypatch.setattr(jira_graph, "interrupt", ask)

    update = jira_graph.create_node(state_with_cards(), {})

    assert asked[0]["action"] == "jira"
    assert "EPIC-1" in asked[0]["document"]  # оператор видит план, а не число
    assert tracker[0]["project"] == "PAY"
    assert update["issues"]["status"] == "created"


def test_the_operator_sees_a_draft_of_every_card(tracker, monkeypatch: pytest.MonkeyPatch):
    """
    Список строк говорит, сколько карточек. Решение принимается по тому, что
    уедет в запросе: заголовок, тип из схемы проекта и описание целиком.
    """
    monkeypatch.setenv("JIRA_PROJECT_KEY", "ORB")
    asked: list[dict] = []

    def ask(payload):
        asked.append(payload)
        return {"decision": "approved"}

    monkeypatch.setattr(jira_graph, "interrupt", ask)

    jira_graph.create_node(state_with_cards(), {})

    cards = asked[0]["drafts"]
    assert [card["id"] for card in cards] == ["EPIC-1", "TASK-1"]
    assert [card["title"] for card in cards] == ["Платежи", "Идемпотентность"]
    assert all(card["kind"] == "issue" and card["where"] == "ORB" for card in cards)
    assert {"label": "Тип", "value": "Epic"} in cards[0]["fields"]
    assert {"label": "Родитель", "value": "EPIC-1"} in cards[1]["fields"]


def test_the_draft_shows_the_description_that_will_be_sent(
    tracker, monkeypatch: pytest.MonkeyPatch
):
    """
    Описание собирает не модель, а `Item.body`: критерии, область, ссылка на
    источник. Показывать вместо него текст документа значит показывать не то.
    """
    monkeypatch.setenv("JIRA_PROJECT_KEY", "ORB")
    asked: list[dict] = []
    monkeypatch.setattr(
        jira_graph,
        "interrupt",
        lambda payload: asked.append(payload) or {"decision": "approved"},
    )
    cards = {
        "issues": [
            {
                "id": "TASK-1",
                "type": "Task",
                "summary": "Идемпотентность",
                "description": "Ключ живёт сутки.",
                "acceptance": ["повтор не создаёт вторую попытку"],
                "service": "payments-api",
            }
        ]
    }
    state = state_with_cards(fenced(cards))
    state["source"] = {"url": "https://wiki.example.com/pages/1"}

    jira_graph.create_node(state, {})

    body = asked[0]["drafts"][0]["document"]
    assert "Ключ живёт сутки." in body
    assert "повтор не создаёт вторую попытку" in body
    assert "payments-api" in body
    assert "https://wiki.example.com/pages/1" in body


def test_what_the_parser_dropped_is_shown_next_to_the_drafts(
    tracker, monkeypatch: pytest.MonkeyPatch
):
    """Отброшенной карточки в списке черновиков нет — значит, о ней говорят словами."""
    monkeypatch.setenv("JIRA_PROJECT_KEY", "ORB")
    asked: list[dict] = []
    monkeypatch.setattr(
        jira_graph,
        "interrupt",
        lambda payload: asked.append(payload) or {"decision": "rejected"},
    )
    cards = {
        "issues": [
            {"id": "TASK-1", "type": "Task", "summary": "Идемпотентность"},
            {"id": "TASK-2", "type": "Task", "summary": ""},
        ]
    }
    state = state_with_cards(fenced(cards))

    jira_graph.create_node(state, {})

    assert [card["id"] for card in asked[0]["drafts"]] == ["TASK-1"]
    assert any("TASK-2" in warning for warning in asked[0]["warnings"])


def test_the_operator_is_asked_even_with_approval_switched_off(
    tracker, monkeypatch: pytest.MonkeyPatch
):
    """Без проекта заводить некуда — этот вопрос отключить нельзя."""
    monkeypatch.setenv("JIRA_CREATE_REQUIRE_APPROVAL", "0")
    monkeypatch.setattr(
        jira_graph, "interrupt", lambda payload: {"decision": "approved", "project": "ORB"}
    )

    jira_graph.create_node(state_with_cards(), {})

    assert tracker[0]["project"] == "ORB"


def test_the_switch_stops_the_run_before_the_tracker(tracker, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JIRA_CREATE_ISSUES", "0")

    update = jira_graph.create_node(state_with_cards(), {})

    assert tracker == []
    assert update["issues"]["status"] == "disabled"


def test_an_unconfigured_tracker_is_a_skip_not_a_crash(monkeypatch: pytest.MonkeyPatch):
    """Документы к этому моменту написаны и оплачены — ронять тред нельзя."""
    monkeypatch.setenv("JIRA_CREATE_REQUIRE_APPROVAL", "0")

    update = jira_graph.create_node(state_with_cards(), {})

    assert update["issues"]["status"] == "skipped"
    assert "JIRA_BASE_URL" in update["issues"]["reason"]


def test_an_unparsable_document_is_reported_and_costs_nothing(
    tracker, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("JIRA_PROJECT_KEY", "ORB")
    monkeypatch.setenv("JIRA_CREATE_REQUIRE_APPROVAL", "0")

    update = jira_graph.create_node(state_with_cards("карточки словами, без JSON"), {})

    assert tracker == []
    assert update["issues"]["status"] == "failed"


def test_a_halted_pipeline_creates_nothing(tracker, monkeypatch: pytest.MonkeyPatch):
    """Заводить задачи по недописанному backlog'у нельзя."""
    monkeypatch.setenv("JIRA_PROJECT_KEY", "ORB")
    monkeypatch.setenv("JIRA_CREATE_REQUIRE_APPROVAL", "0")

    update = jira_graph.create_node({"artifacts": {"scope": "карта"}, "messages": []}, {})

    assert tracker == []
    assert update["issues"]["status"] == "skipped"


# --------------------------------------------------------------------------
# Весь путь целиком
#
# Остальные тесты проверяют узлы по одному; этот — что они соединены. Прогон
# идёт от документа до ключей в трекере через настоящую остановку LangGraph:
# `interrupt()` замораживает тред, `Command(resume=...)` его продолжает.
# `responses` роняет любой незарегистрированный запрос, поэтому уйти в сеть
# тест не может.
# --------------------------------------------------------------------------
@responses.activate
def test_a_document_becomes_issues_after_the_operator_names_the_project(
    monkeypatch: pytest.MonkeyPatch, folder
):
    base = "https://jira.example.com"
    monkeypatch.setenv("JIRA_BASE_URL", base)
    monkeypatch.setenv("JIRA_TOKEN", "tok")
    responses.add(
        responses.GET,
        f"{base}/rest/api/3/issue/createmeta/ORB/issuetypes",
        json={"values": [{"name": "Epic"}, {"name": "Task"}]},
        status=200,
    )
    responses.add(responses.POST, f"{base}/rest/api/3/issue", json={"key": "ORB-1"}, status=201)
    responses.add(responses.POST, f"{base}/rest/api/3/issue", json={"key": "ORB-2"}, status=201)

    model = GenericFakeChatModel(messages=iter(ANSWERS))
    app = jira_graph.build_graph(llm=model).compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "jira-e2e", "input_dir": TASK_DIR}}

    stopped = app.invoke({"messages": [HumanMessage(SHORT)]}, config=config)

    # Конвейер дошёл до заведения и встал: проект не назван нигде.
    assert stopped["__interrupt__"][0].value["action"] == "jira"
    assert "EPIC-1" in stopped["__interrupt__"][0].value["document"]

    state = app.invoke(Command(resume={"decision": "approved", "project": "ORB"}), config=config)

    assert [issue["key"] for issue in state["issues"]["created"]] == ["ORB-1", "ORB-2"]
    assert state["issues"]["project"] == "ORB"
    # Ребёнок уехал с настоящим ключом эпика, а не с локальным.
    child = json.loads(responses.calls[-1].request.body)["fields"]
    assert child["parent"] == {"key": "ORB-1"}
    # Ключи видно и в треде: за ними приходят сразу, а не в документ.
    assert "ORB-2" in state["messages"][-1].content
