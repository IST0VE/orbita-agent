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
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

from agent import confluence, evidence, inputs, nodes, prep_graph, prep_prompts, prep_roles, tools
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


def test_a_new_ticket_cannot_cite_evidence_from_the_previous_ticket(monkeypatch):
    old = evidence.for_file("old.txt", "Старое утверждение")
    new = evidence.for_file("new.txt", "Новый материал")
    content, meta = evidence.attach(old)
    history = [ToolMessage(content=content, artifact={"evidence": meta}, tool_call_id="old")]
    state = {"ticket": {"key": "ORB-1"}, "messages": history,
             "evidence": {old.id: old.to_dict()}, "citations": {"intake": {"findings": []}}}
    monkeypatch.setattr(prep_graph, "_read_ticket", lambda *_: {
        "ticket": {"key": "ORB-2", "evidence": new.id},
        "evidence": {new.id: new.to_dict()},
    })

    update = prep_graph.ticket_node(state, {})
    current = {**state, **update, "messages": [*history, AIMessage(content="Новая задача")]}
    _, reviewed = prep_roles.review(current, prep_roles.ROLES[0], f"Факт [{old.id}].")

    assert update["evidence_start"] == len(history)
    assert list(update["evidence"]) == [new.id]
    assert update["citations"] == {}
    assert any(item["kind"] == "unknown_ref" for item in reviewed["citations"]["intake"]["findings"])


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

    assert plan.needs == (
        *prep_roles.READ_BY_CODE, prep_roles.CHANGE, "intake", "gaps", "research",
        prep_roles.SOURCES, prep_roles.CHECK,
    )


def test_every_role_sees_what_the_code_read():
    """
    27 сентября 2026 стенограмму, выбранную оператором, прочитала одна роль
    поиска, а тикет, кроме разбора, не видел никто. Разбор объявил главной
    находкой расхождение, которое стенограмма объясняла, а следующие роли
    повторили её, не имея чем проверить.
    """
    for role in prep_roles.ROLES:
        assert set(prep_roles.READ_BY_CODE) <= set(role.needs), role.key


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
    """`needs` разбора — не документы этапов, а то, что положила нода чтения."""
    assert prep_roles.FIRST.needs == prep_roles.READ_BY_CODE
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


class Recording(GenericFakeChatModel):
    """Подделка, которая помнит, с чем её звали: что видела роль, проверяется по ней."""

    calls: list = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def test_the_run_of_27_september_would_not_go_blind(configured, monkeypatch: pytest.MonkeyPatch):
    """
    Прогон 27 сентября 2026 целиком. Разбор ответил анонсом «I'll start by
    checking…» с вызовом инструмента — и эта строка стала документом этапа.
    Поиск читал страницы Confluence по полторы тысячи токенов при
    LLM_MAX_HISTORY_TOKENS=3000: обрезка выбрасывала сначала список запросов,
    потом прочитанное, и на последнем ходе роль видела один запрос оператора.
    Итог — пять документов о том, что задача не прочитана, при прочитанной
    задаче и восьми успешных обращениях к инструментам.
    """
    monkeypatch.setenv("LLM_MAX_HISTORY_TOKENS", "3000")
    # Вторая страница больше всего лимита: её ответ не влез бы ни в какое окно.
    monkeypatch.setattr(
        confluence,
        "fetch_page",
        lambda page_id, *a, **k: {
            "title": f"Страница {page_id}",
            "url": f"https://wiki.example.com/x/{page_id}",
            "text": "абзац " * (1200 if page_id == "1" else 2600),
            "truncated": False,
        },
    )
    announcement = AIMessage(
        content=(
            "I'll start by checking the attached files and looking for existing "
            "documentation and the linked task."
        ),
        tool_calls=[{"name": "jira_issue", "args": {"key": "ORB-123"}, "id": "call-0"}],
        response_metadata=usage_meta(),
    )
    gaps = AIMessage(
        content="# Что нужно выяснить\n\nЗапрос: выгрузка отчётов в CSV. " + "пробел " * 800,
        response_metadata=usage_meta(),
    )
    model = Recording(
        messages=iter(
            [
                announcement,
                answer(prep_roles.BY_KEY["intake"]),
                gaps,
                asks("confluence_page", "call-1", page_id="1"),
                asks("confluence_page", "call-2", page_id="2"),
                answer(prep_roles.BY_KEY["research"]),
                answer(prep_roles.BY_KEY["plan"]),
                answer(prep_roles.BY_KEY["draft"]),
            ]
        )
    )
    app = prep_graph.build_graph(llm=model).compile()
    state = app.invoke(
        {"messages": [HumanMessage(LINK)]},
        config={"configurable": {"thread_id": "prep-1", "input_dir": ""}},
    )

    assert state["artifacts"]["intake"] == answer(prep_roles.BY_KEY["intake"]).content
    research = [
        seen
        for seen in model.calls
        if prep_prompts.ROLE_PROMPTS["research"] in str(seen[0].content)
    ]
    assert len(research) == 3
    for seen in research:
        assert any("выгрузка отчётов в CSV" in str(m.content) for m in seen), (
            "роль поиска потеряла список запросов"
        )
    answers = [m for m in research[-1] if m.type == "tool"]
    assert "«Страница 2»" in str(answers[-1].content).splitlines()[0], (
        "роль поиска не увидела страницу, которую только что попросила"
    )
    assert list(state["artifacts"]) == [
        prep_roles.TICKET, prep_roles.LINKED, prep_roles.PAGES, prep_roles.CHANGE,
        prep_roles.FILES, "intake", prep_roles.CHECK, "gaps", "research",
        prep_roles.SOURCES, "plan", "draft",
    ]


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


# --------------------------------------------------------------------------
# Что код читает до ролей: связанные задачи и материалы оператора
#
# 27 сентября 2026 стенограмму встречи, выбранную оператором, прочитала только
# роль поиска, и только своим ходом из восьми. Разбор её не видел и объявил
# главной находкой расхождение заголовка с описанием, которое стенограмма
# объясняла. Связанную задачу, где было сказано, где живёт аутентификация,
# тоже читала одна роль поиска.
# --------------------------------------------------------------------------
TRANSCRIPT = (
    "10:02 Анна: сейчас есть действие смена пароля, пароль генерируется сам.\n"
    "10:03 Борис: генерация сломана, длина ноль."
)


@pytest.fixture
def transcript():
    """Папка задачи со стенограммой и соседним файлом, которого оператор не выбирал."""
    folder = inputs.ensure_root() / "prep-task"
    folder.mkdir(parents=True)
    (folder / "стенограмма.txt").write_text(TRANSCRIPT, encoding="utf-8")
    (folder / "старое.md").write_text("содержимое невыбранного файла", encoding="utf-8")
    return "prep-task"


def picked(task: str, *names: str) -> dict:
    return {"configurable": {"thread_id": "prep-1", "input_dir": task, "input_file": list(names)}}


def test_the_operator_files_are_read_by_code(configured, transcript):
    update = prep_graph.ticket_node(ticket_state(), picked(transcript, "стенограмма.txt"))

    block = update["artifacts"][prep_roles.FILES]
    assert "длина ноль" in block
    assert update["ticket"]["files"] == ["стенограмма.txt"]
    # Невыбранный файл назван, но не прочитан: выбирать за оператора нечем.
    assert "старое.md" in block
    assert "содержимое невыбранного файла" not in block


def test_nothing_is_read_for_an_operator_who_picked_nothing(configured, transcript):
    update = prep_graph.ticket_node(ticket_state(), picked(transcript))

    block = update["artifacts"][prep_roles.FILES]
    assert "длина ноль" not in block
    assert "стенограмма.txt" in block
    assert update["ticket"]["files"] == []


def test_a_file_named_in_the_request_counts_as_picked(configured, transcript):
    update = prep_graph.ticket_node(
        ticket_state(LINK + ", стенограмма в файле стенограмма.txt"), picked(transcript)
    )

    assert "длина ноль" in update["artifacts"][prep_roles.FILES]


def test_every_role_gets_the_operator_files(configured, transcript):
    """Стенограмма доходит до каждой роли, включая роль поиска с её инструментами."""
    model = Recording(messages=iter(ANSWERS))
    app = prep_graph.build_graph(llm=model).compile()
    app.invoke({"messages": [HumanMessage(LINK)]}, config=picked(transcript, "стенограмма.txt"))

    assert len(model.calls) == len(ANSWERS)
    for seen in model.calls:
        assert "длина ноль" in str(seen[1].content)


def test_files_picked_anew_in_the_thread_are_read_again(configured, transcript):
    known = {"key": "ORB-123", "files": []}
    update = prep_graph.ticket_node(
        ticket_state(ticket=known), picked(transcript, "стенограмма.txt")
    )

    assert "длина ноль" in update["artifacts"][prep_roles.FILES]
    assert update["ticket"] == {**known, "files": ["стенограмма.txt"]}


@pytest.mark.parametrize("move_to_another_folder", [False, True])
def test_new_contents_of_a_same_named_file_reach_every_role(
    configured, transcript, move_to_another_folder,
):
    model = Recording(messages=iter([answer(role) for role in prep_roles.ROLES] * 2))
    app = prep_graph.build_graph(llm=model).compile(checkpointer=InMemorySaver())
    app.invoke({"messages": [HumanMessage(LINK)]}, config=picked(transcript, "стенограмма.txt"))

    folder = inputs.ensure_root() / ("new-folder" if move_to_another_folder else transcript)
    folder.mkdir(exist_ok=True)
    (folder / "стенограмма.txt").write_text("Новые требования: длина пароля 24.", encoding="utf-8")
    state = app.invoke(
        {"messages": [HumanMessage("Обнови документы по новым материалам.")]},
        config=picked(folder.name, "стенограмма.txt"),
    )

    assert "длина пароля 24" in state["artifacts"][prep_roles.FILES]
    for seen in model.calls[-len(prep_roles.ROLES):]:
        assert "длина пароля 24" in str(seen[1].content)
        assert "длина ноль" not in str(seen[1].content)


def fetch_from(*issues: dict):
    by_key = {item["key"]: item for item in issues}

    def fetch(key, *args, **kwargs):
        if key not in by_key:
            raise jira_api.JiraError("объект не найден (HTTP 404)")
        return by_key[key]

    return fetch


LINKED_ISSUE = dict(
    ISSUE,
    key="ORB-7",
    url="https://jira.example.com/browse/ORB-7",
    summary="Модуль входа",
    description="Аутентификация сейчас внутри модуля healthcheck.",
    comments=[],
)


def test_linked_issues_are_read_by_code(configured, monkeypatch):
    main = dict(
        ISSUE,
        parent="ORB-1",
        links=[{"relation": "blocks", "key": "ORB-7", "summary": "Модуль входа"}],
    )
    parent = dict(ISSUE, key="ORB-1", url="https://jira.example.com/browse/ORB-1", summary="Эпик")
    monkeypatch.setattr(jira_api, "fetch_issue", fetch_from(main, parent, LINKED_ISSUE))

    update = prep_graph.ticket_node(ticket_state(), {})

    block = update["artifacts"][prep_roles.LINKED]
    assert "внутри модуля healthcheck" in block
    assert "Связь: ORB-123 blocks ORB-7." in block
    assert [item["key"] for item in update["ticket"]["linked"]] == ["ORB-1", "ORB-7"]
    # Текст связанных задач — в артефакте для ролей, а не в сводке состояния.
    assert all("text" not in item for item in update["ticket"]["linked"])
    assert "Связанные задачи прочитаны: ORB-1, ORB-7" in update["messages"][0].content


def test_linked_issues_stop_at_the_ceiling(configured, monkeypatch):
    monkeypatch.setenv("PREP_LINKED_ISSUES", "1")
    main = dict(
        ISSUE,
        links=[
            {"relation": "blocks", "key": "ORB-7", "summary": "Модуль входа"},
            {"relation": "relates to", "key": "ORB-8", "summary": "Соседняя"},
        ],
    )
    monkeypatch.setattr(jira_api, "fetch_issue", fetch_from(main, LINKED_ISSUE))

    update = prep_graph.ticket_node(ticket_state(), {})

    assert [bool(item.get("skipped")) for item in update["ticket"]["linked"]] == [False, True]
    assert "ORB-8 (relates to)" in update["artifacts"][prep_roles.LINKED]
    assert "PREP_LINKED_ISSUES" in update["artifacts"][prep_roles.LINKED]


def test_an_unreadable_linked_issue_is_named_not_hidden(configured, monkeypatch):
    main = dict(ISSUE, links=[{"relation": "blocks", "key": "ORB-9", "summary": "Нет доступа"}])
    monkeypatch.setattr(jira_api, "fetch_issue", fetch_from(main))

    update = prep_graph.ticket_node(ticket_state(), {})

    assert "Не прочитана: объект не найден" in update["artifacts"][prep_roles.LINKED]
    assert "Не открылись: ORB-9" in update["messages"][0].content


# --------------------------------------------------------------------------
# Роль поиска: вход через бриф, счёт ходов, реестр источников
# --------------------------------------------------------------------------
def research_calls(model: Recording) -> list:
    return [
        seen for seen in model.calls
        if prep_prompts.ROLE_PROMPTS["research"] in str(seen[0].content)
    ]


def test_the_search_role_is_briefed_and_told_its_turns(configured):
    model = Recording(messages=iter(ANSWERS))
    prep_graph.build_graph(llm=model).compile().invoke(
        {"messages": [HumanMessage(LINK)]},
        config={"configurable": {"thread_id": "prep-1", "input_dir": ""}},
    )

    first, second = research_calls(model)
    assert "# Задача из Jira" in str(first[1].content)
    assert "Разделитель — точка с запятой." in str(first[1].content)
    assert prep_roles.BY_KEY["gaps"].title in str(first[1].content)
    assert "Ходов с инструментами осталось: 12 из 12" in str(first[-1].content)
    # Второй вызов — тот же бриф и собственная переписка роли, без документов этапов.
    assert [m.type for m in second[1:4]] == ["human", "ai", "tool"]
    assert second[1].content == first[1].content
    assert "осталось: 11 из 12" in str(second[-1].content)


def test_the_last_turn_is_named_as_last():
    assert "последний ход" in nodes.turns_left(1, 12)


def test_the_ledger_counts_what_was_read(configured, monkeypatch):
    monkeypatch.setattr(
        confluence,
        "fetch_page",
        lambda page_id, *a, **k: {
            "id": page_id,
            "title": "Отчёты личного кабинета",
            "url": "https://wiki.example.com/x/12345",
            "text": "выгрузка сейчас только в XLSX",
            "truncated": False,
        },
    )
    state = run(
        ANSWERS[0],
        ANSWERS[1],
        asks("confluence_search", "call-1", query="экспорт отчётов"),
        asks("confluence_page", "call-2", page_id="12345"),
        *ANSWERS[3:],
    )

    ledger = state["artifacts"][prep_roles.SOURCES]
    assert "### Прочитано: 2" in ledger
    assert "[JIRA ORB-123]" in ledger
    assert "[WIKI 12345]" in ledger
    assert "### Найдено, но не открыто: 0" in ledger
    assert "| Confluence | экспорт отчётов | пространство SUP | 1 |" in ledger


def test_the_plan_and_the_draft_get_the_ledger():
    for key in ("plan", "draft"):
        text = prep_roles.brief(prep_roles.BY_KEY[key], "ЗАПРОС", {prep_roles.SOURCES: "РЕЕСТР"})
        assert "# Реестр источников прогона\n\nРЕЕСТР" in text


def test_a_missing_ledger_is_said_aloud():
    text = prep_roles.brief(prep_roles.BY_KEY["plan"], "ЗАПРОС", {})
    assert "Реестр не собран" in text


# --------------------------------------------------------------------------
# Страница: итог развёрнут, рабочие этапы свёрнуты
# --------------------------------------------------------------------------
def test_the_page_folds_the_working_stages(configured):
    state = run(*ANSWERS)
    document = common_graph.stage_pages(state, {}, pipeline=prep_roles.PIPELINE)[0]["document"]

    for key in ("intake", "gaps", "research"):
        role = prep_roles.BY_KEY[key]
        assert f'<ac:parameter ac:name="title">{role.number}. {role.title}</ac:parameter>' in document
    for key in ("plan", "draft"):
        role = prep_roles.BY_KEY[key]
        assert f"<h2>{role.number}. {role.title}</h2>" in document
    assert '<ac:parameter ac:name="title">Источники прогона</ac:parameter>' in document
    assert "Задача Jira: ORB-123 «Экспорт отчётов в CSV»" in document
    # Порядок чтения: итог, план, потом рабочие этапы и реестр.
    order = [
        document.index("05. Первичная документация"),
        document.index("04. План работ"),
        document.index("01. Разбор задачи"),
        document.index("Источники прогона"),
    ]
    assert order == sorted(order)
    # И там, где документ собирает сам граф: узлы публикации аннотированы общим
    # `State`, и без расширенной входной схемы сводки задачи они не видели.
    assert "Задача Jira: ORB-123 «Экспорт отчётов в CSV»" in state["document"]
    assert "[JIRA ORB-123]" in state["artifacts"][prep_roles.SOURCES]


# --------------------------------------------------------------------------
# Форма документа
# --------------------------------------------------------------------------
def test_foreign_script_is_rewritten_in_place(configured):
    draft = AIMessage(
        content="# Первичная документация\n\nСделать服务端-часть.\n\nОстальное без изменений.",
        response_metadata=usage_meta(),
    )
    repair = AIMessage(content='["Сделать серверную часть."]', response_metadata=usage_meta())

    state = run(*ANSWERS[:-1], draft, repair)

    text = state["artifacts"]["draft"]
    assert "Сделать серверную часть." in text
    assert "服务端" not in text
    assert "Остальное без изменений." in text
    # Починка — отдельный платный вызов, и он учтён.
    assert state["usage"]["calls"] == len(ANSWERS) + 1


def test_a_row_longer_than_its_header_widens_the_table(configured):
    gaps = AIMessage(
        content="# Что нужно выяснить\n\n| № | Чего не знаем |\n| --- | --- |\n| П1 | формат | [TBD] |",
        response_metadata=usage_meta(),
    )

    state = run(ANSWERS[0], gaps, *ANSWERS[2:])

    assert "| № | Чего не знаем |  |" in state["artifacts"]["gaps"]
    assert "| П1 | формат | [TBD] |" in state["artifacts"]["gaps"]


def test_the_prompts_carry_the_rules_the_27_september_run_broke():
    common = prep_prompts.COMMON
    assert "Прежде чем назвать расхождение" in common
    assert "Цитата о системе X — факт только о X" in common
    assert "ошибки распознавания" in common
    assert "Искать в wiki и читать найденное — работа этапа 03" in common
    assert "Ссылку ставь только к тому, что в источнике написано" in common
    assert "собранная самой Orbita" in common
    assert "«комментариев нет» — это ответ, а не пробел" in common
    assert "одно–три слова" in prep_prompts.ROLE_PROMPTS["gaps"]
    assert "Ходы — потолок, а не норма" in prep_prompts.ROLE_PROMPTS["research"]
    assert "поэтому придумали" in prep_prompts.ROLE_PROMPTS["intake"]
    assert "дочитать" in prep_prompts.ROLE_PROMPTS["plan"]
    assert "Перед выдачей сверь документ сам с собой" in prep_prompts.ROLE_PROMPTS["draft"]


def test_the_tools_leave_a_trace_for_the_ledger(configured):
    """След — для реестра источников: модель его не видит, код по нему считает."""
    message = tools.confluence_search.invoke(
        {"type": "tool_call", "name": "confluence_search", "args": {"query": "экспорт"}, "id": "t1"}
    )

    assert message.artifact["found"][0]["id"] == "12345"
    assert message.content.startswith("Область поиска: пространство SUP.")


def test_a_wrong_project_is_a_readable_answer(configured):
    message = tools.jira_search.invoke(
        {"type": "tool_call", "name": "jira_search",
         "args": {"query": "пароль", "project": "не проект"}, "id": "t2"}
    )

    assert "поиск в Jira не выполнен" in message.content
    assert message.artifact["error"]


# --------------------------------------------------------------------------
# Страницы Confluence по ссылкам из запроса и задачи
# --------------------------------------------------------------------------
WIKI = "https://wiki.example.com/pages/viewpage.action?pageId="


def wiki_pages(read: list):
    def fetch(page_id, *args, **kwargs):
        read.append(page_id)
        return {
            "id": page_id,
            "title": f"Страница {page_id}",
            "url": f"https://wiki.example.com/x/{page_id}",
            "text": f"Текст {page_id}",
            "truncated": False,
        }

    return fetch


def test_pages_linked_from_the_request_and_the_ticket_are_read_by_code(configured, monkeypatch):
    main = dict(ISSUE, description=f"Спецификация: {WIKI}55555")
    monkeypatch.setattr(jira_api, "fetch_issue", fetch_from(main))
    read: list = []
    monkeypatch.setattr(confluence, "fetch_page", wiki_pages(read))

    update = prep_graph.ticket_node(ticket_state(f"{LINK} и {WIKI}44444"), {})

    assert read == ["44444", "55555"]
    block = update["artifacts"][prep_roles.PAGES]
    assert "## Страница 44444 «Страница 44444»" in block
    assert "Источник [EV-" in block
    assert "Текст 55555" in block
    assert [item["id"] for item in update["ticket"]["pages"]] == ["44444", "55555"]
    assert all("text" not in item for item in update["ticket"]["pages"])
    assert "Страницы Confluence по ссылкам прочитаны: 44444, 55555" in update["messages"][0].content


def test_a_page_on_another_wiki_is_not_read(configured, monkeypatch):
    """Страница 44444 есть в любой вики: по чужой ссылке прочиталась бы своя, другая."""
    monkeypatch.setattr(confluence, "fetch_page", lambda *a, **k: pytest.fail("чужая вики"))

    update = prep_graph.ticket_node(
        ticket_state(f"{LINK} https://other.example.org/pages/viewpage.action?pageId=44444"), {}
    )

    assert update["ticket"]["pages"] == []


def test_linked_pages_stop_at_the_ceiling(configured, monkeypatch):
    monkeypatch.setenv("PREP_LINKED_PAGES", "1")
    read: list = []
    monkeypatch.setattr(confluence, "fetch_page", wiki_pages(read))

    update = prep_graph.ticket_node(ticket_state(f"{LINK} {WIKI}44444 {WIKI}55555"), {})

    assert read == ["44444"]
    assert "PREP_LINKED_PAGES: 55555" in update["artifacts"][prep_roles.PAGES]


def test_pages_read_by_code_are_in_the_ledger(configured, monkeypatch):
    monkeypatch.setattr(confluence, "fetch_page", wiki_pages([]))

    state = run(*ANSWERS, question=f"{QUESTION} См. {WIKI}44444")

    ledger = state["artifacts"][prep_roles.SOURCES]
    assert "[WIKI 44444]" in ledger
    assert "страница по ссылке из запроса или задачи, прочитана кодом" in ledger


def test_sources_survive_followups_without_recounting_searches(configured, monkeypatch):
    monkeypatch.setattr(confluence, "fetch_page", wiki_pages([]))
    first = [
        ANSWERS[0], ANSWERS[1],
        asks("confluence_search", "search-1", query="экспорт"),
        asks("confluence_page", "page-1", page_id="12345"),
        *ANSWERS[3:],
    ]
    second = [answer(role) for role in prep_roles.ROLES]
    third = [
        ANSWERS[0], ANSWERS[1], asks("confluence_page", "page-2", page_id="44444"),
        *ANSWERS[3:],
    ]
    model = Recording(messages=iter([*first, *second, *third]))
    app = prep_graph.build_graph(llm=model).compile(checkpointer=InMemorySaver())
    config = picked("")
    initial = app.invoke({"messages": [HumanMessage(LINK)]}, config=config)
    revised = app.invoke({"messages": [HumanMessage("Сократи формулировки.")]}, config=config)

    assert revised["artifacts"][prep_roles.SOURCES] == initial["artifacts"][prep_roles.SOURCES]
    assert "[WIKI 12345]" in revised["artifacts"][prep_roles.SOURCES]
    assert "### Запросы: 1" in revised["artifacts"][prep_roles.SOURCES]
    for seen in model.calls[-2:]:  # план и документация получают прежние источники
        assert "[WIKI 12345]" in str(seen[1].content)

    expanded = app.invoke({"messages": [HumanMessage("Дочитай ещё страницу.")]}, config=config)
    sources = expanded["artifacts"][prep_roles.SOURCES]
    assert "[WIKI 12345]" in sources and "[WIKI 44444]" in sources
    assert "### Прочитано: 3" in sources
    assert "### Запросы: 1" in sources


def test_pages_linked_by_adf_labels_in_description_and_comments_are_read(configured, monkeypatch):
    def linked_text(page_id):
        return jira_api.field_text({
            "type": "doc", "content": [{"type": "paragraph", "content": [{
                "type": "text", "text": "Спецификация",
                "marks": [{"type": "link", "attrs": {"href": WIKI + page_id}}],
            }]}],
        })

    main = dict(
        ISSUE, description=linked_text("44444"),
        comments=[{"author": "Автор", "created": "2026-09-27", "text": linked_text("55555")}],
    )
    monkeypatch.setattr(jira_api, "fetch_issue", fetch_from(main))
    read = []
    monkeypatch.setattr(confluence, "fetch_page", wiki_pages(read))

    update = prep_graph.ticket_node(ticket_state(), {})

    assert read == ["44444", "55555"]
    assert "Текст 44444" in update["artifacts"][prep_roles.PAGES]
    assert "Текст 55555" in update["artifacts"][prep_roles.PAGES]
