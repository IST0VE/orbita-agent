"""
Контракт и полный автономный прогон конвейера подготовки задачи.

Проверяется четыре вещи. Первая — связность описания: ключ роли это же имя узла,
ключ артефакта и ключ префикса, и разъехаться им нельзя. Вторая — то, ради чего
менялся `graph.py`: в инструменты ходят ДВЕ роли, и после ответа инструмента
управление обязано вернуться к той, которая спрашивала. Раньше узел инструментов
имел ровно одно исходящее ребро, и второй роли-читателю в графе места не было.
Третья — проверка входа: Jira тут обязательна, и прогон без неё это пять
оплаченных вызовов и пять документов из `TBD`. Четвёртая — что инструменты
конвейера привязаны ко всем ролям сразу: набор уезжает в кешируемый префикс.

Сеть не участвует нигде: походы в Jira и Confluence подменяются, а модель —
подделка из langchain. Тест, который случайно уйдёт в сеть, упадёт.
"""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage

from agent import confluence, prep_graph, prep_prompts, prep_roles, tools
from agent import graph as common_graph
from agent import jira as jira_api

QUESTION = "Надо сделать ORB-123: экспорт отчётов в CSV из личного кабинета."
# Так пишет оператор на самом деле: ссылка из адресной строки, а не ключ.
LINK = "напиши документацию по задаче https://jira.example.com/browse/ORB-123"
ALIEN = "напиши документацию по задаче https://other.atlassian.net/browse/ORB-123"
SHORT = "надо сделать"

ISSUE = {
    "key": "ORB-123",
    "url": "https://jira.example.com/browse/ORB-123",
    "summary": "Экспорт отчётов в CSV",
    "description": "Нужен экспорт в личном кабинете.",
    "status": "In Progress",
    "type": "Story",
    "priority": "High",
    "resolution": "",
    "labels": ["export"],
    "components": ["web-app"],
    "versions": [],
    "assignee": "Мария И.",
    "reporter": "Пётр",
    "parent": "",
    "subtasks": [],
    "links": [],
    "due": "",
    "updated": "2026-08-25T10:00:00.000+0300",
    "comments": [
        {"author": "Пётр", "created": "2026-08-20", "text": "Разделитель — точка с запятой."}
    ],
}

PAGES = [
    {
        "id": "12345",
        "title": "Отчёты личного кабинета",
        "url": "https://wiki.example.com/x/12345",
        "excerpt": "выгрузка сейчас только в XLSX",
    }
]


def usage_meta() -> dict:
    return {
        "token_usage": {
            "prompt_cache_hit_tokens": 100,
            "prompt_cache_miss_tokens": 50,
            "completion_tokens": 20,
        }
    }


def answer(role) -> AIMessage:
    return AIMessage(
        content=f"# {role.title}\n\nРезультат {role.number} по ORB-123.",
        response_metadata=usage_meta(),
    )


def asks(name: str, call_id: str, **args) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id}],
        response_metadata=usage_meta(),
    )


# Ход конвейера. Задачу читает нода, а не модель, поэтому вызова на «позови
# jira_issue с уже известным ключом» здесь нет: разбор отвечает документом
# сразу. В инструменты ходит один поиск — и по делу: сколько запросов уйдёт
# на пробелы, заранее не знает никто.
ANSWERS = [
    answer(prep_roles.BY_KEY["intake"]),
    answer(prep_roles.BY_KEY["gaps"]),
    asks("confluence_search", "call-1", query="экспорт отчётов"),
    answer(prep_roles.BY_KEY["research"]),
    answer(prep_roles.BY_KEY["plan"]),
    answer(prep_roles.BY_KEY["draft"]),
]


@pytest.fixture(autouse=True)
def no_publishing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch):
    """Jira настроена, но в сеть никто не ходит: обе операции подменены."""
    monkeypatch.setenv("JIRA_BASE_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_TOKEN", "tok")
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://wiki.example.com")
    monkeypatch.setenv("CONFLUENCE_TOKEN", "tok")
    monkeypatch.setenv("CONFLUENCE_SPACE_KEY", "SUP")
    monkeypatch.setattr(jira_api, "fetch_issue", lambda key, *a, **k: dict(ISSUE, key=key))
    monkeypatch.setattr(confluence, "search", lambda query, *a, **k: PAGES)


def run(*answers, question: str = QUESTION, task: str = "") -> dict:
    model = GenericFakeChatModel(messages=iter(answers))
    app = prep_graph.build_graph(llm=model).compile()
    return app.invoke(
        {"messages": [HumanMessage(question)]},
        config={"configurable": {"thread_id": "prep-1", "input_dir": task}},
    )


# --------------------------------------------------------------------------
# Связность описания
# --------------------------------------------------------------------------
def test_every_role_has_a_prefix_and_every_prefix_has_a_role():
    assert set(prep_roles.KEYS) == set(prep_prompts.ROLE_PROMPTS)
    assert len(prep_roles.ROLES) == len(set(prep_roles.KEYS))


def test_numbers_are_sequential_and_unique():
    assert [role.number for role in prep_roles.ROLES] == ["01", "02", "03", "04", "05"]


def test_all_roles_share_one_prefix():
    """
    Общий блок обязан стоять в начале каждого префикса и быть побайтово одним
    и тем же: на нём держится кеш, и разное начало обнулило бы всю экономику.
    """
    for key in prep_roles.KEYS:
        assert prep_prompts.for_role(key).startswith(prep_prompts.COMMON)


def test_reading_happens_before_planning():
    """
    Порядок этапов — суть конвейера: сначала прочитать задачу, потом решить,
    чего не знаем, потом искать, и только потом планировать. Роль, которая
    планирует раньше поиска, планирует по догадкам.
    """
    order = list(prep_roles.KEYS)

    assert order.index("intake") < order.index("gaps") < order.index("research")
    assert order.index("research") < order.index("plan") < order.index("draft")


def test_the_plan_sees_everything_that_was_read():
    plan = prep_roles.BY_KEY["plan"]

    assert plan.needs == ("intake", "gaps", "research")


def test_later_stages_receive_the_original_outputs():
    """
    Пересказ ломает связь с источником: ссылку на страницу wiki нельзя
    «примерно передать», а без неё найденное становится утверждением агента.
    """
    artifacts = {"intake": "ТОЧНЫЙ РАЗБОР", "research": "ТОЧНЫЕ ВЫЖИМКИ", "plan": "ТОЧНЫЙ ПЛАН"}
    text = prep_roles.brief(prep_roles.LAST, "ЗАПРОС", artifacts)

    assert text.index("ЗАПРОС") < text.index("ТОЧНЫЙ РАЗБОР")
    assert text.index("ТОЧНЫЙ РАЗБОР") < text.index("ТОЧНЫЕ ВЫЖИМКИ")
    assert text.index("ТОЧНЫЕ ВЫЖИМКИ") < text.index("ТОЧНЫЙ ПЛАН")


def test_a_missing_stage_is_not_disguised():
    text = prep_roles.brief(prep_roles.BY_KEY["plan"], "ЗАПРОС", {"intake": "разбор"})

    assert "Этап не выполнен" in text


# --------------------------------------------------------------------------
# Инструменты конвейера
# --------------------------------------------------------------------------
def test_the_pipeline_carries_its_own_toolset():
    """Единственный конвейер, читающий чужие системы, а не только папку задачи."""
    names = {item.name for item in prep_roles.PIPELINE.tools}

    assert {"jira_issue", "jira_search", "confluence_search", "confluence_page"} <= names


def test_file_tools_stay_in_the_set():
    """
    `context_node` дописывает список файлов задачи в сообщение любого конвейера
    и обещает модели `read_task_file`. Набор без файловых сделал бы это
    обещание ложным.
    """
    names = {item.name for item in prep_roles.PIPELINE.tools}

    assert {"list_task_files", "read_task_file"} <= names


def test_only_the_search_role_may_ask():
    """
    Инструменты остались там, где модель действительно выбирает. Задача читается
    кодом по ссылке из запроса; что искать в wiki и сколько на это уйдёт
    запросов — не знает заранее никто, и вот это решение модели и оставлено.
    """
    readers = [role.key for role in prep_roles.ROLES if role.reads_files]

    assert readers == ["research"]


def test_the_intake_role_reads_the_prefetched_ticket():
    """`needs` разбора — не документ этапа, а задача, которую положила нода."""
    assert prep_roles.FIRST.needs == (prep_roles.TICKET,)
    assert not prep_roles.FIRST.reads_files


def test_no_tool_can_write_anything():
    """
    Между черновиком и заведёнными задачами обязан стоять человек. Side effect
    в этом проекте проходит через ноду публикации с подтверждением оператора,
    а не через решение модели посреди этапа.
    """
    for item in tools.RESEARCH_TOOLS:
        assert not any(
            word in item.description.lower()
            for word in ("создать", "завести", "изменить", "обновить", "опубликовать")
        )


# --------------------------------------------------------------------------
# Полный конвейер
# --------------------------------------------------------------------------
def test_every_stage_produces_an_artifact(configured):
    state = run(*ANSWERS)

    assert [key for key in state["artifacts"] if key in prep_roles.KEYS] == list(prep_roles.KEYS)
    assert state["stage"] == prep_roles.LAST.key


def test_the_search_role_gets_control_back_after_its_tool(configured):
    state = run(*ANSWERS)
    kinds = [(m.type, getattr(m, "name", "")) for m in state["messages"]]

    assert ("tool", "confluence_search") in kinds
    # Роль договорила после ответа инструмента, а не потеряла ход.
    assert state["artifacts"]["research"]


def test_tool_answers_reach_the_thread(configured):
    state = run(*ANSWERS)
    answers = [m.content for m in state["messages"] if m.type == "tool"]

    assert any("12345" in text for text in answers)


def test_tool_calls_are_paid_for(configured):
    """Переписка с инструментами — это вызовы модели, и они обязаны считаться."""
    state = run(*ANSWERS)

    assert state["usage"]["calls"] == len(ANSWERS)


def test_reading_the_ticket_costs_no_model_call(configured):
    """
    Ради этого нода и появилась. Пять ролей плюс один поход в инструменты —
    шесть вызовов. Седьмым был вызов, в котором модель просили назвать ключ,
    уже найденный регуляркой в ссылке.
    """
    state = run(*ANSWERS)

    assert state["usage"]["calls"] == len(prep_roles.ROLES) + 1
    assert state["artifacts"][prep_roles.TICKET]


# --------------------------------------------------------------------------
# Проверка входа
# --------------------------------------------------------------------------
def test_without_jira_the_run_costs_nothing():
    """
    Модель подделке не передана вовсе: любой её вызов уронил бы тест. Без Jira
    пять ролей выпустили бы пять документов из `TBD` — оплаченный отказ,
    который выглядит как результат.
    """
    state = run()

    assert not state.get("artifacts")
    assert not state["usage"]
    assert "JIRA_BASE_URL" in state["messages"][-1].content


def test_an_unsearchable_message_costs_nothing(configured):
    state = run(question=SHORT)

    assert not state["usage"]
    assert "Искать нечего" in state["messages"][-1].content


def test_an_issue_key_alone_is_enough(configured):
    """Ключ задачи — исчерпывающий вход: всё остальное конвейер прочитает сам."""
    assert prep_graph.missing_source({"messages": [HumanMessage("ORB-123")]}, {}) == ""


def test_a_description_without_a_key_is_enough(configured):
    """Ключа нет, но есть чем искать — и в Jira, и в Confluence."""
    assert prep_graph.missing_source({"messages": [HumanMessage(QUESTION)]}, {}) == ""


def test_a_follow_up_turn_is_not_an_empty_input(configured):
    """
    На втором ходе треда задача уже разобрана, и короткая правка — обычное
    сообщение. Проверка входа, срабатывающая здесь, ломала бы диалог.
    """
    state = {"artifacts": {"intake": "разбор"}, "messages": [HumanMessage(SHORT)]}

    assert prep_graph.missing_source(state, {}) == ""


def test_confluence_is_not_required(configured, monkeypatch: pytest.MonkeyPatch):
    """
    Без wiki конвейер работает: инструмент скажет, что источник недоступен,
    роль поиска это запишет, а документы соберутся из того, что есть в задаче.
    Ненастроенная Jira обесценивает весь прогон, ненастроенный Confluence — один
    его этап, и обходиться с ними одинаково было бы неверно.
    """
    for name in ("CONFLUENCE_BASE_URL", "CONFLUENCE_TOKEN", "CONFLUENCE_SPACE_KEY"):
        monkeypatch.delenv(name, raising=False)

    state = run(*ANSWERS)

    assert [key for key in state["artifacts"] if key in prep_roles.KEYS] == list(prep_roles.KEYS)
    unavailable = [m.content for m in state["messages"] if m.type == "tool"]
    assert any("Confluence не настроена" in text for text in unavailable)


# --------------------------------------------------------------------------
# Ссылка на задачу вместо ключа
#
# Обычный вход конвейера — не `ORB-123`, а адрес из адресной строки браузера.
# Разбирает его регулярка до первого вызова модели: просить об этом модель
# значит платить токенами за работу одной регулярки из jira.py и иногда
# получать в ответ BROWSE-1.
# --------------------------------------------------------------------------
def test_a_link_is_a_valid_input(configured):
    assert prep_graph.missing_source({"messages": [HumanMessage(LINK)]}, {}) == ""


def test_the_key_from_a_link_reaches_the_role(configured):
    text = prep_roles.brief(prep_roles.FIRST, LINK, {})

    assert "ключи задач Jira: ORB-123" in text


def test_a_link_to_another_tracker_is_flagged(configured):
    """
    Ключ задачи ничего не говорит о том, из какого он трекера. Прочитав
    `ORB-123` из настроенного инстанса по ссылке на чужой, конвейер молча
    уехал бы не туда — в лучшем случае это 404, в худшем существующая, но
    совсем другая задача.
    """
    text = prep_roles.brief(prep_roles.FIRST, ALIEN, {})

    assert "other.atlassian.net" in text
    assert "настроен другой" in text


def test_an_ordinary_request_carries_no_warning(configured):
    text = prep_roles.brief(prep_roles.FIRST, LINK, {})

    assert "внимание" not in text


def test_nothing_is_added_when_there_is_nothing_to_recognise(configured):
    assert prep_roles.recognised("опиши экспорт отчётов") == ""


def test_the_page_title_keeps_the_key_and_drops_the_link(configured):
    """
    В восьмидесяти символах заголовка ссылка занимает шестьдесят и не сообщает
    ничего: адрес трекера у всех страниц пространства один и тот же. Ключ из
    неё, наоборот, — самое полезное, что в заголовке может быть.
    """
    title = common_graph.page_title({"messages": [HumanMessage(LINK)]}, {})

    assert "ORB-123" in title
    assert "https://" not in title


# --------------------------------------------------------------------------
# Одна страница на прогон
# --------------------------------------------------------------------------
def test_the_whole_run_is_one_page(configured):
    """
    Пять документов этого конвейера — один разговор от тикета до черновика
    документации. Постранично их получал человек, попросивший «страницу по
    задаче», и закрывал четыре из пяти не читая.
    """
    state = run(*ANSWERS)
    pages = common_graph.stage_pages(state, {}, pipeline=prep_roles.PIPELINE)

    assert len(pages) == 1
    for role in prep_roles.ROLES:
        assert role.title in pages[0]["document"]


def test_the_only_page_has_no_stage_in_its_title(configured):
    """Различать одной странице нечего: суффикс с ролью был бы враньём."""
    state = run(*ANSWERS)
    pages = common_graph.stage_pages(state, {}, pipeline=prep_roles.PIPELINE)

    assert pages[0]["role"] == ""
    assert pages[0]["title"] == common_graph.page_title(state, {})


def test_a_run_without_stages_publishes_nothing_extra(configured):
    """Пустой прогон — ноль страниц; заглушку добавит уже `publish_plan`."""
    assert common_graph.stage_pages({"artifacts": {}}, {}, pipeline=prep_roles.PIPELINE) == []


def test_other_pipelines_still_publish_per_stage():
    """
    Одна страница — свойство этого конвейера, а не новое поведение публикации.
    У остальных этапы читают разные люди и в разное время.
    """
    from agent import roles as analytics_roles

    assert prep_roles.PIPELINE.one_page
    assert not analytics_roles.PIPELINE.one_page


# --------------------------------------------------------------------------
# Нода чтения задачи
#
# Задачу читает код. Раньше это делала первая роль инструментом, и стоило это
# лишнего вызова на каждый прогон: адрес известен из запроса, аргумент у вызова
# ровно один, и модель в этом решении ничего не выбирала — она озвучивала
# найденное регуляркой.
# --------------------------------------------------------------------------
def ticket_state(question: str = LINK, ticket: dict | None = None) -> dict:
    state = {"messages": [HumanMessage(question)]}
    if ticket is not None:
        state["ticket"] = ticket
    return state


def test_the_ticket_is_read_without_a_model(configured):
    """Модели тут нет вовсе: нода вызывается напрямую и не может её позвать."""
    update = prep_graph.ticket_node(ticket_state(), {})

    assert update["ticket"]["key"] == "ORB-123"
    assert "Экспорт отчётов в CSV" in update["artifacts"][prep_roles.TICKET]
    assert "Разделитель — точка с запятой." in update["artifacts"][prep_roles.TICKET]


def test_what_was_read_is_visible_in_the_thread(configured):
    """Ход по графу должен быть виден оператору, а не только в артефактах."""
    update = prep_graph.ticket_node(ticket_state(), {})

    assert "ORB-123" in update["messages"][0].content
    assert "ключ назван в запросе" in update["messages"][0].content


def test_the_ticket_is_not_fetched_twice_in_a_thread(configured, monkeypatch):
    """
    Повторный ход треда в трекер не ходит: задача одна и уже прочитана.
    Подделка чтения здесь падает — вызвать её значит провалить тест.
    """
    monkeypatch.setattr(
        jira_api, "fetch_issue", lambda *a, **k: pytest.fail("повторное чтение задачи")
    )

    assert prep_graph.ticket_node(ticket_state(ticket={"key": "ORB-123"}), {}) == {}


def test_a_new_key_in_the_thread_is_read(configured):
    """А вот другая задача в том же треде — это уже другой материал."""
    update = prep_graph.ticket_node(
        ticket_state("теперь посмотри PAY-7", ticket={"key": "ORB-123"}), {}
    )

    assert update["ticket"]["key"] == "PAY-7"


def test_without_a_key_the_ticket_is_chosen_by_search(configured, monkeypatch):
    monkeypatch.setattr(
        jira_api, "search", lambda query, *a, **k: [{"key": "ORB-9", "summary": "Экспорт"}]
    )

    update = prep_graph.ticket_node(ticket_state("нужен экспорт отчётов в CSV"), {})

    assert update["ticket"]["key"] == "ORB-9"
    assert update["ticket"]["chosen"] == "search"


def test_a_ticket_chosen_by_search_says_so_to_the_role(configured, monkeypatch):
    """
    Документ, в котором не видно, о какой задаче речь, хуже отсутствия
    документа: человек прочитает план по чужой задаче и не заметит.
    """
    monkeypatch.setattr(
        jira_api, "search", lambda query, *a, **k: [{"key": "ORB-9", "summary": "Экспорт"}]
    )

    update = prep_graph.ticket_node(ticket_state("нужен экспорт отчётов в CSV"), {})

    assert "ключ задачи в запросе не назван" in update["artifacts"][prep_roles.TICKET]


def test_an_unreadable_ticket_does_not_stop_the_run(configured, monkeypatch):
    """
    Остаётся запрос оператора, и разбор обязан начать с того, что задача
    не прочитана. Отказ был бы хуже: причина видна только из документа.
    """

    def boom(*args, **kwargs):
        raise jira_api.JiraError("объект не найден (HTTP 404)")

    monkeypatch.setattr(jira_api, "fetch_issue", boom)

    update = prep_graph.ticket_node(ticket_state(), {})

    assert "не прочитана" in update["artifacts"][prep_roles.TICKET]
    assert update["ticket"]["error"]


def test_a_missing_ticket_is_named_as_missing_to_the_role():
    """Пустой артефакт — не молчание: роль обязана узнать, что читать нечего."""
    text = prep_roles.brief(prep_roles.FIRST, "запрос", {})

    assert "Задача не прочитана" in text
    assert "Не выдавай запрос оператора за содержание тикета" in text


def test_a_link_to_another_tracker_reaches_the_role(configured):
    """
    Ключ ничего не говорит о том, из какого он трекера: по ссылке на чужой
    инстанс прочитана будет задача с тем же ключом из настроенного.
    """
    update = prep_graph.ticket_node(ticket_state(ALIEN), {})

    assert "other.atlassian.net" in update["artifacts"][prep_roles.TICKET]


def test_the_ticket_is_not_published_as_a_stage(configured):
    """
    Задача лежит в `artifacts` рядом с документами этапов, но страницей не
    становится: `Pipeline.done()` перебирает роли, а не ключи.
    """
    state = run(*ANSWERS)

    assert state["artifacts"][prep_roles.TICKET]
    assert [role.key for role in prep_roles.PIPELINE.done(state["artifacts"])] == list(
        prep_roles.KEYS
    )


def test_the_search_queries_reach_the_research_role(configured):
    """
    Роль с инструментами получает не `brief()`, а переписку хода целиком, и
    документы предыдущих этапов приезжают ей оттуда — слово в слово. На это
    ссылается комментарий к `needs` в prep_roles.
    """
    state = run(*ANSWERS)
    turn = [m.content for m in state["messages"]]

    assert any(prep_roles.BY_KEY["gaps"].title in str(text) for text in turn)
    assert any(prep_roles.BY_KEY["intake"].title in str(text) for text in turn)
