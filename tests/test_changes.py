"""
Изменение и устойчивые номера требований.

Номер требования даёт код: модель нумерует заново на каждом прогоне, и
трассировка «R-17 → задача → проверка» на её номерах невозможна. Проверяется
сверка (`reconcile`), переписывание документа (`rewrite`), узел `change` на
подделке хранилища и сквозной прогон двух тредов по одной задаче.

База не участвует: хранилище подменено словарём, который ведёт себя как
`PostgresChanges`, — та же сверка под «блокировкой», те же поля.
"""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from starlette.testclient import TestClient

from agent import api, changes, confluence, db, evidence, jira, prep_graph, prep_roles, security
from agent.security import Principal

ISSUE = {
    "key": "PWD-36",
    "url": "https://jira.example.com/browse/PWD-36",
    "summary": "Смена алгоритма паролей",
    "description": "Пароли API перевести на SHA-512.",
    "status": "In Progress",
    "type": "Story",
    "updated": "2026-09-20T10:00:00.000+0300",
    "comments": [],
    "comments_read": True,
}


def existing(*texts: str, dropped: tuple[int, ...] = ()) -> list[dict]:
    return [
        {"number": index, "text": text, "fingerprint": changes.fingerprint(text),
         "status": "dropped" if index in dropped else "active", "revision": 1, "evidence": []}
        for index, text in enumerate(texts, start=1)
    ]


def proposed(*pairs: tuple[str, str]) -> list[dict]:
    return [{"asked": asked, "text": text, "evidence": []} for asked, text in pairs]


# --------------------------------------------------------------------------
# Разбор
# --------------------------------------------------------------------------
def test_requirements_are_read_only_from_their_section():
    document = (
        "# План\n\n## Требования\n\n- R-1: Пароли в SHA-512 [EV-a1b2c3].\n"
        "- **R-?** — Постепенная смена пользователей\n| R-3 | Файл .htusers | [EV-000001] |\n\n"
        "## Примерные задачи\n\n- R-9: не требование, а строка задачи\n"
    )

    found = changes.parse(document)

    assert [(item["asked"], item["text"]) for item in found] == [
        ("1", "Пароли в SHA-512 [EV-a1b2c3]."),
        ("?", "Постепенная смена пользователей"),
        ("3", "Файл .htusers — [EV-000001]"),
    ]
    assert found[0]["evidence"] == ["EV-a1b2c3"]


def test_a_document_without_the_section_says_nothing_about_requirements():
    assert changes.parse("## План\n\n- R-1: что-то") is None
    assert changes.parse("## Требования\n\nПока нет.") == []


# --------------------------------------------------------------------------
# Сверка
# --------------------------------------------------------------------------
def test_new_requirements_are_numbered_after_the_highest_ever_given():
    """Номер выбывшего не занимается: R-3 выбыло, новое получает R-4."""
    settled = changes.reconcile(
        existing("Пароли в SHA-512", "Смена постепенная", "Старый формат", dropped=(3,)),
        proposed(("1", "Пароли в SHA-512"), ("?", "Новое требование о журнале")),
    )

    assert settled["assigned"] == [1, 4]
    states = {item["number"]: (item["status"], item["change"]) for item in settled["requirements"]}
    assert states == {
        1: ("active", "same"), 2: ("dropped", "dropped"), 3: ("dropped", "absent"), 4: ("active", "new"),
    }


def test_the_same_text_keeps_its_number_whatever_the_model_wrote():
    """Модель сдвинула нумерацию, убрав первое требование: код её поправляет."""
    settled = changes.reconcile(
        existing("Пароли в SHA-512", "Смена пользователей постепенная"),
        proposed(("1", "Смена пользователей постепенная")),
    )

    assert settled["assigned"] == [2]
    assert settled["remap"] == {1: 2}
    assert "R-1 → R-2" in settled["notes"][0]


def test_a_reworded_requirement_under_its_number_is_a_new_revision():
    settled = changes.reconcile(
        existing("Пароли пользователей API хранятся в SHA-512"),
        proposed(("1", "Пароли пользователей API хранить в SHA-512 вместо MD5")),
    )

    [item] = settled["requirements"]
    assert (item["number"], item["change"], item["revision"]) == (1, "changed", 2)


def test_a_number_given_to_a_different_requirement_is_not_reused():
    """R-1 под совсем другим текстом — не правка R-1, а новое требование."""
    settled = changes.reconcile(
        existing("Пароли в SHA-512"),
        proposed(("1", "Журнал входов хранится девяносто дней")),
    )

    assert settled["assigned"] == [2]
    assert "R-1 у изменения — другое требование" in settled["notes"][0]


def test_a_reworded_requirement_without_a_number_is_recognised():
    settled = changes.reconcile(
        existing("Смена пользователей постепенная, без простоя"),
        proposed(("?", "Смена пользователей постепенная без простоя сервиса")),
    )

    assert settled["assigned"] == [1]


def test_a_dropped_requirement_can_come_back():
    settled = changes.reconcile(existing("Пароли в SHA-512", dropped=(1,)), proposed(("?", "Пароли в SHA-512")))

    assert settled["requirements"][0]["change"] == "returned"


def test_the_document_gets_the_final_numbers_and_its_references_follow():
    document = (
        "## Требования\n- R-1: Смена пользователей постепенная\n- R-?: Журнал входов\n"
        "## Задачи\n- TASK-1 покрывает R-1\n"
    )
    parsed = changes.parse(document)
    settled = changes.reconcile(
        existing("Пароли в SHA-512", "Смена пользователей постепенная"), parsed
    )

    text = changes.rewrite(document, parsed, settled["assigned"], settled["remap"])

    assert "- R-2: Смена пользователей постепенная" in text
    assert "- R-3: Журнал входов" in text
    assert "TASK-1 покрывает R-2" in text


def test_an_ambiguous_number_is_not_rewritten_in_references():
    document = "## Требования\n- R-1: Первое требование\n- R-1: Второе требование\n## Задачи\n- см. R-1\n"
    parsed = changes.parse(document)
    settled = changes.reconcile([], parsed)

    text = changes.rewrite(document, parsed, settled["assigned"], settled["remap"])

    assert "- R-1: Первое требование" in text and "- R-2: Второе требование" in text
    assert "- см. R-1" in text


def test_references_to_new_requirements_follow_their_labels():
    """Задачи ссылаются на новые требования метками: код ставит номер и в ссылку."""
    document = (
        "## Требования\n- R-1: Пароли в SHA-512\n- R-?1: Журнал входов\n"
        "- **R-?2** — Смена постепенная\n"
        "## Задачи\n- TASK-1 покрывает R-?2 и R-1\n- TASK-2 покрывает R-?1\n"
    )
    parsed = changes.parse(document)
    settled = changes.reconcile(existing("Пароли в SHA-512"), parsed)

    text = changes.rewrite(document, parsed, settled["assigned"], settled["remap"])

    assert [item["label"] for item in parsed] == ["", "?1", "?2"]
    assert "- R-2: Журнал входов" in text and "- **R-3** — Смена постепенная" in text
    assert "TASK-1 покрывает R-3 и R-1" in text
    assert "TASK-2 покрывает R-2" in text


@pytest.mark.parametrize(
    ("requirements", "reference"),
    [
        ("- R-?: Журнал входов\n", "TASK-1 покрывает R-1"),
        # Два голых `R-?`: на какое из них ссылка, неизвестно — остаётся как есть.
        ("- R-?: Журнал входов\n- R-?: Смена постепенная\n", "TASK-1 покрывает R-?"),
    ],
)
def test_a_bare_new_mark_is_rewritten_only_when_it_is_the_only_one(requirements, reference):
    document = f"## Требования\n{requirements}## Задачи\n- TASK-1 покрывает R-?\n"
    parsed = changes.parse(document)
    settled = changes.reconcile([], parsed)

    assert reference in changes.rewrite(document, parsed, settled["assigned"], settled["remap"])


def test_new_sources_under_the_same_wording_are_a_change_of_their_own():
    before = [{**existing("Пароли в SHA-512")[0], "evidence": ["EV-aaaaaa"]}]
    settled = changes.reconcile(
        before, [{"asked": "1", "text": "Пароли в SHA-512 [EV-bbbbbb]", "evidence": ["EV-bbbbbb"]}]
    )

    [item] = settled["requirements"]
    assert (item["change"], item["revision"], item["evidence"]) == ("cited", 1, ["EV-bbbbbb"])


def test_a_number_given_in_the_chat_is_not_a_claim_on_the_database():
    """
    Соседний чат сохранил своё R-1, пока этот называл R-1 своё новое требование.
    Похожая формулировка — не повод отнять у соседа номер и переписать его текст.
    """
    neighbour = existing("Пароли пользователей API хранятся в SHA-512")
    ours = [{"number": 1, "text": "Пароли пользователей API хранятся в SHA-512 с солью",
             "status": "active", "evidence": []}]

    settled = changes.merge(neighbour, ours, committed=[])

    assert settled["assigned"] == [2]
    assert settled["remap"] == {1: 2}
    first, second = settled["requirements"]
    assert (first["change"], first["text"]) == ("untouched", "Пароли пользователей API хранятся в SHA-512")
    assert second["change"] == "new"


def test_only_what_the_thread_changed_goes_to_the_database():
    """Сосед переформулировал R-1; этот тред R-1 не трогал и правку не откатывает."""
    committed = existing("Пароли в SHA-512", "Смена постепенная")
    now = existing("Пароли в SHA-512 с солью", "Смена постепенная")
    ours = [committed[0], {**committed[1], "text": "Смена постепенная, без простоя сервиса",
                           "fingerprint": changes.fingerprint("Смена постепенная, без простоя сервиса")}]

    settled = changes.merge(now, ours, committed)

    first, second = settled["requirements"]
    assert (first["change"], first["text"]) == ("untouched", "Пароли в SHA-512 с солью")
    assert (second["change"], second["text"]) == ("changed", "Смена постепенная, без простоя сервиса")


def test_the_block_tells_the_role_which_numbers_to_keep():
    change = {"id": "CH-1", "key": "PWD-36",
              "requirements": existing("Пароли в SHA-512", "Старое", dropped=(2,))}

    text = changes.block(change)

    assert "- R-1: Пароли в SHA-512" in text
    assert "их номера не занимай): R-2" in text
    assert "`R-?1`" in text


# --------------------------------------------------------------------------
# Хранилище
# --------------------------------------------------------------------------
class MemoryChanges:
    """То же, что `PostgresChanges`, в словаре: сверка при записи — та же."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict] = {}
        self.saves: list[dict] = []

    def find(self, owner: str, key: str) -> dict | None:
        found = self.rows.get((owner, key))
        if found is None:
            return None
        return {"id": found["id"], "key": key, "title": found["title"],
                "requirements": [dict(item) for item in found["requirements"]]}

    def save(self, *, owner, key, title, thread_id, graph, requirements, evidence,
             committed=()) -> dict:
        self.saves.append({"owner": owner, "key": key, "thread_id": thread_id,
                           "requirements": requirements, "evidence": list(evidence)})
        row = self.rows.setdefault(
            (owner, key), {"id": f"CH-{len(self.rows) + 1:04d}", "title": title, "requirements": []}
        )
        settled = {"requirements": row["requirements"], "remap": {}, "notes": []}
        if requirements is not None:
            settled = changes.merge(row["requirements"], requirements, committed)
            row["requirements"] = [
                {key_: value for key_, value in item.items() if key_ != "change"}
                for item in settled["requirements"]
            ]
        return {"id": row["id"], **settled}

    def list(self, owner: str) -> list[dict]:
        return [
            {"id": row["id"], "key": key, "title": row["title"],
             "requirements": sum(item["status"] == "active" for item in row["requirements"])}
            for (who, key), row in self.rows.items()
            if who == owner
        ]

    def get(self, owner: str, change_id: str) -> dict | None:
        for (who, key), row in self.rows.items():
            if who == owner and row["id"] == change_id:
                return {"id": row["id"], "key": key, "requirements": row["requirements"]}
        return None


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> MemoryChanges:
    fake = MemoryChanges()
    monkeypatch.setattr(changes, "store", fake)
    monkeypatch.setenv("POSTGRES_URI", "postgresql://example.invalid/orbita")
    monkeypatch.setattr(changes, "_down", {})
    return fake


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_TOKEN", "tok")
    monkeypatch.setenv("CONFLUENCE_PUBLISH", "0")
    monkeypatch.setattr(jira, "fetch_issue", lambda key, *a, **k: dict(ISSUE, key=key))
    monkeypatch.setattr(confluence, "search", lambda *a, **k: [])


TICKET_ID = evidence.for_issue(ISSUE, jira.format_issue(ISSUE)).id


def doc(title: str, body: str = "") -> AIMessage:
    return AIMessage(content=f"# {title}\n\n{body or 'Текст этапа.'}")


def run(plan: str, thread: str = "t-1") -> dict:
    model = GenericFakeChatModel(
        messages=iter(
            [doc("Разбор"), doc("Пробелы"), doc("Что уже есть"), doc("План", plan), doc("Документация")]
        )
    )
    app = prep_graph.build_graph(llm=model).compile()
    return app.invoke(
        {"messages": [HumanMessage("напиши документацию по задаче PWD-36")]},
        config={"configurable": {"thread_id": thread, "input_dir": ""}},
    )


FIRST_PLAN = (
    f"## Требования\n- R-?1: Пароли API хранятся в «SHA-512» [{TICKET_ID}]\n"
    "- R-?2: Смена пользователей постепенная [ВЫВОД]\n## Задачи\n- TASK-1 покрывает R-?2"
)
#: Тот же план на следующем ходе: модель пишет номера, которые ей дал код.
SECOND_PLAN = (
    f"## Требования\n- R-1: Пароли API хранятся в «SHA-512» [{TICKET_ID}]\n"
    "- R-2: Смена пользователей постепенная [ВЫВОД]\n## Задачи\n- TASK-1 покрывает R-1"
)


def test_the_first_run_numbers_and_stores_the_requirements(configured, store):
    state = run(FIRST_PLAN)

    assert "- R-1: Пароли API хранятся в «SHA-512»" in state["artifacts"]["plan"]
    assert "- R-2: Смена пользователей постепенная" in state["artifacts"]["plan"]
    assert "TASK-1 покрывает R-2" in state["artifacts"]["plan"]
    change = state["change"]
    assert change["stored"] and change["id"] == "CH-0001"
    assert [item["number"] for item in change["requirements"]] == [1, 2]
    saved = store.saves[-1]
    assert saved["thread_id"] == "t-1"
    assert TICKET_ID in {item["id"] for item in saved["evidence"]}
    assert all("text" not in item for item in saved["evidence"]), "текст источника в базу не пишется"


def test_a_second_thread_keeps_the_numbers_the_model_lost(configured, store):
    """Второй прогон по той же задаче: модель сдвинула нумерацию, код вернул номера."""
    run(FIRST_PLAN, thread="t-1")

    state = run(
        "## Требования\n- R-1: Смена пользователей постепенная [ВЫВОД]\n"
        "- R-?: Журнал смены паролей хранится в Jira [ВЫВОД]\n## Задачи\n- TASK-1 покрывает R-1",
        thread="t-2",
    )

    plan = state["artifacts"]["plan"]
    assert "- R-2: Смена пользователей постепенная" in plan
    assert "- R-3: Журнал смены паролей" in plan
    assert "TASK-1 покрывает R-2" in plan
    states = {item["number"]: item["status"] for item in state["change"]["requirements"]}
    assert states == {1: "dropped", 2: "active", 3: "active"}
    assert "Изменение CH-0001" in prep_roles.subject(state)
    assert "выбыли R-1" in prep_roles.subject(state)


def test_the_plan_sees_the_requirements_of_the_change(configured, store):
    run(FIRST_PLAN, thread="t-1")
    model_seen: list = []

    class Seeing(GenericFakeChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            model_seen.append(messages)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    app = prep_graph.build_graph(
        llm=Seeing(messages=iter([doc("Р"), doc("П"), doc("Ч"), doc("План", FIRST_PLAN), doc("Д")]))
    ).compile()
    app.invoke(
        {"messages": [HumanMessage("напиши документацию по задаче PWD-36")]},
        config={"configurable": {"thread_id": "t-3", "input_dir": ""}},
    )

    plan_call = model_seen[3][-1].content
    assert "# Требования изменения" in plan_call
    assert "- R-1: Пароли API хранятся в «SHA-512»" in plan_call


def test_without_a_database_the_run_goes_on_and_says_so(configured, monkeypatch):
    """POSTGRES_URI не задан: номера живут в треде, строка под задачей говорит правду."""
    state = run(FIRST_PLAN)

    assert "- R-1:" in state["artifacts"]["plan"]
    assert state["change"]["stored"] is False
    assert "не сохранено: база не настроена" in prep_roles.subject(state)


def test_a_broken_database_does_not_stop_the_run(configured, store, monkeypatch):
    def down(**_):
        raise db.DatabaseUnavailable("база Orbita не ответила")

    monkeypatch.setattr(store, "save", down)
    state = run(FIRST_PLAN)

    assert state["artifacts"]["draft"]
    assert "база Orbita не ответила" in state["change"]["error"]
    assert state["change"]["base"], "номера должны пережить ход и без базы"


def test_a_nested_run_does_not_write_the_change(configured, store):
    model = GenericFakeChatModel(
        messages=iter([doc("Р"), doc("П"), doc("Ч"), doc("План", FIRST_PLAN), doc("Д")])
    )
    app = prep_graph.build_graph(llm=model).compile()
    state = app.invoke(
        {"messages": [HumanMessage("напиши документацию по задаче PWD-36")]},
        config={"configurable": {"thread_id": "n-1", "input_dir": "", "nested": True}},
    )

    assert store.saves == []
    assert "вложенный прогон" in state["change"]["error"]


def test_a_number_taken_by_a_neighbour_thread_is_fixed_before_publication(configured, store):
    """
    Соседний тред сохранил R-1 между чтением изменения и записью этого прогона.
    Узел `change` сверяет под блокировкой и переписывает план до публикации.
    """
    original = store.find

    def stale(owner, key):
        # Прелюдия видит изменение без требований — как до записи соседа.
        found = original(owner, key)
        return found and {**found, "requirements": []}

    store.rows[(changes.owner(), "PWD-36")] = {
        "id": "CH-0001", "title": "", "requirements": existing("Журнал входов девяносто дней")
    }
    store.find = stale
    state = run(FIRST_PLAN)

    plan = state["artifacts"]["plan"]
    assert "- R-2: Пароли API хранятся" in plan
    assert "- R-3: Смена пользователей постепенная" in plan
    # Требование соседа этот прогон не видел — и не снимает его.
    states = {item["number"]: item["status"] for item in state["change"]["requirements"]}
    assert states == {1: "active", 2: "active", 3: "active"}


class Chat:
    """Один тред с памятью между ходами: на каждый ход — пять документов, план свой."""

    def __init__(self, thread: str = "t-1") -> None:
        self.queue: list[AIMessage] = []
        model = GenericFakeChatModel(messages=self._answers())
        self.app = prep_graph.build_graph(llm=model).compile(checkpointer=InMemorySaver())
        self.config = {"configurable": {"thread_id": thread, "input_dir": ""}}

    def _answers(self):
        while True:
            yield self.queue.pop(0)

    def turn(self, plan: str, text: str = "напиши документацию по задаче PWD-36") -> dict:
        self.queue += [doc("Разбор"), doc("Пробелы"), doc("Что уже есть"), doc("План", plan),
                       doc("Документация")]
        return self.app.invoke({"messages": [HumanMessage(text)]}, config=self.config)


def neighbour_rewords_the_first(store: MemoryChanges, text: str) -> None:
    row = store.rows[(changes.owner(), "PWD-36")]
    first = row["requirements"][0]
    row["requirements"][0] = {**first, "text": text, "fingerprint": changes.fingerprint(text),
                              "revision": first["revision"] + 1}


def test_an_unread_new_task_does_not_write_into_the_previous_change(configured, store, monkeypatch):
    """
    Тред прочитал PWD-36, потом оператор назвал PWD-99, и она не открылась.
    План этого хода — не о PWD-36, и в её изменение он не пишется.
    """
    chat = Chat()
    chat.turn(FIRST_PLAN)
    saved = [dict(item) for item in store.rows[(changes.owner(), "PWD-36")]["requirements"]]

    def fetch(key, *args, **kwargs):
        if key == "PWD-99":
            raise jira.JiraError("объект не найден (HTTP 404)")
        return dict(ISSUE, key=key)

    monkeypatch.setattr(jira, "fetch_issue", fetch)
    state = chat.turn("## Требования\n- R-1: Отчёт выгружается в CSV [ВЫВОД]", "теперь PWD-99")

    assert [save["key"] for save in store.saves] == ["PWD-36"]
    assert store.rows[(changes.owner(), "PWD-36")]["requirements"] == saved
    assert state["change"]["key"] == "" and state["change"]["wanted"] == "PWD-99"
    assert "Пароли API" not in state["artifacts"][prep_roles.CHANGE]


def test_numbers_given_while_the_database_was_down_are_not_claims(configured, store, monkeypatch):
    """
    Ход 1: база лежит, номера R-1, R-2 выданы в треде. Пока тред ждал, соседний
    сохранил своё R-1. Ход 2: база поднялась — номера треда не отнимают R-1
    у соседа, а получают свои, и план переписывается до публикации.
    """
    chat = Chat()
    real = store.save

    def down(**_):
        raise db.DatabaseUnavailable("база Orbita не ответила")

    monkeypatch.setattr(store, "save", down)
    chat.turn(FIRST_PLAN)
    monkeypatch.setattr(store, "save", real)
    changes._down.clear()
    store.rows[(changes.owner(), "PWD-36")] = {
        "id": "CH-0001", "title": "", "requirements": existing("Пароли API хранятся в SHA-512 с солью")
    }

    state = chat.turn(SECOND_PLAN)

    row = store.rows[(changes.owner(), "PWD-36")]["requirements"]
    assert [(item["number"], item["text"]) for item in row][0] == (
        1, "Пароли API хранятся в SHA-512 с солью"
    )
    assert [item["number"] for item in row] == [1, 2, 3]
    plan = state["artifacts"]["plan"]
    assert "- R-2: Пароли API хранятся в «SHA-512»" in plan
    assert "- R-3: Смена пользователей постепенная" in plan
    assert "TASK-1 покрывает R-2" in plan


def test_a_plan_without_requirements_does_not_roll_back_a_neighbour(configured, store):
    """
    Ход 1 сохранил требования. Соседний тред переформулировал R-1. Ход 2 выпустил
    план без раздела «Требования»: о требованиях он молчит, и в базу их не пишет.
    """
    chat = Chat()
    chat.turn(FIRST_PLAN)
    neighbour_rewords_the_first(store, "Пароли API хранятся в SHA-512 с солью")

    state = chat.turn("## Задачи\n- TASK-1 без раздела требований")

    assert store.saves[-1]["requirements"] is None
    first = store.rows[(changes.owner(), "PWD-36")]["requirements"][0]
    assert first["text"] == "Пароли API хранятся в SHA-512 с солью"
    assert "settled" not in state["change"]


def test_a_repeated_requirement_does_not_roll_back_a_neighbour(configured, store):
    """План повторил R-1 слово в слово — это не правка, и правка соседа остаётся."""
    chat = Chat()
    chat.turn(FIRST_PLAN)
    neighbour_rewords_the_first(store, "Пароли API хранятся в SHA-512 с солью")

    chat.turn(SECOND_PLAN)

    first = store.rows[(changes.owner(), "PWD-36")]["requirements"][0]
    assert (first["text"], first["revision"]) == ("Пароли API хранятся в SHA-512 с солью", 2)


def test_a_turn_that_never_reached_the_change_leaves_nothing_settled(configured):
    """Ход, остановленный до узла `change`, не передаёт свой список следующему."""
    change = {"key": "PWD-36", "requirements": existing("Пароли в SHA-512"), "settled": True}
    state = {"messages": [HumanMessage("перепиши план по PWD-36")],
             "ticket": {"key": "PWD-36"}, "change": change}

    update = prep_graph.ticket_node(state, {})

    assert update["change"]["settled"] is False
    assert update["change"]["requirements"] == change["requirements"]


@pytest.fixture
def client(store, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("API_ADMIN_TOKEN", "test-only-admin-token-with-32-characters")
    users = {"anna": Principal("sub-anna", "anna"), "tim": Principal("sub-tim", "tim")}
    checked = security.authenticate
    monkeypatch.setattr(
        security,
        "authenticate",
        lambda headers: users.get(headers.get("authorization", "").removeprefix("Bearer "))
        or checked(headers),
    )
    store.rows[("sub-anna", "PWD-36")] = {
        "id": "CH-0001", "title": "Пароли", "requirements": existing("Пароли в SHA-512")
    }
    return TestClient(api.app)


def test_the_owner_sees_the_change_and_its_requirements(client):
    listed = client.get("/api/changes", headers={"Authorization": "Bearer anna"})
    one = client.get("/api/changes/CH-0001", headers={"Authorization": "Bearer anna"})

    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["changes"]] == ["CH-0001"]
    assert one.json()["requirements"][0]["text"] == "Пароли в SHA-512"


def test_a_change_of_another_person_is_not_found(client):
    """Требования пересказывают прочитанное чужим токеном: чужое — как несуществующее."""
    listed = client.get("/api/changes", headers={"Authorization": "Bearer tim"})
    one = client.get("/api/changes/CH-0001", headers={"Authorization": "Bearer tim"})

    assert listed.json()["changes"] == []
    assert one.status_code == 404


def test_changes_need_a_login_and_a_database(client, monkeypatch):
    assert client.get("/api/changes").status_code == 401
    monkeypatch.delenv("POSTGRES_URI")
    response = client.get("/api/changes", headers={"Authorization": "Bearer anna"})
    assert response.status_code == 503
    assert response.json()["error_code"] == "changes_unavailable"


class RecordingConnection:
    """Соединение, которое записывает SQL и отвечает заготовками по порядку."""

    def __init__(self, answers: list) -> None:
        self.answers = list(answers)
        self.statements: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params: tuple = ()):
        # Число подстановок обязано совпасть с числом параметров: ошибку здесь
        # иначе нашла бы только живая база.
        assert sql.count("%s") == len(params), sql
        self.statements.append((sql, params))
        answer = self.answers.pop(0) if self.answers else []
        return type("Cursor", (), {"fetchone": lambda _: answer[0] if answer else None,
                                    "fetchall": lambda _: answer})()

    def transaction(self):
        from contextlib import nullcontext

        return nullcontext()


def test_the_store_writes_the_change_in_one_transaction(monkeypatch):
    from contextlib import contextmanager

    conn = RecordingConnection([[], [("CH-1",)], [(1, "Пароли в SHA-512", changes.fingerprint("Пароли в SHA-512"), "active", 1, [])]])

    @contextmanager
    def connection():
        yield conn

    monkeypatch.setattr(db, "connection", connection)
    item = evidence.for_issue(ISSUE, jira.format_issue(ISSUE))
    saved = changes.PostgresChanges().save(
        owner="sub-anna", key="PWD-36", title="Пароли", thread_id="t-1", graph="prep",
        requirements=[
            {"number": 1, "text": "Пароли в SHA-512", "status": "active", "evidence": []},
            {"number": 2, "text": "Журнал входов", "status": "active", "evidence": [item.id]},
        ],
        evidence=[item.to_dict(text=False)], committed=existing("Пароли в SHA-512"),
    )

    assert saved["id"] == "CH-1"
    written = [sql.split()[2] for sql, _ in conn.statements if sql.startswith("INSERT")]
    # Неизменённое требование в базу не уходит; новое — строкой и историей.
    assert written == ["changes", "requirements", "requirement_history", "evidence", "change_threads"]
    assert [item["change"] for item in saved["requirements"]] == ["untouched", "new"]


def test_new_sources_of_the_same_wording_reach_the_database(monkeypatch):
    """`[EV-a]` → `[EV-b]` при той же формулировке: строка переписывается, история — нет."""
    from contextlib import contextmanager

    row = (1, "Пароли в SHA-512", changes.fingerprint("Пароли в SHA-512"), "active", 1, ["EV-aaaaaa"])
    conn = RecordingConnection([[], [("CH-1",)], [row]])

    @contextmanager
    def connection():
        yield conn

    monkeypatch.setattr(db, "connection", connection)
    before = [{**existing("Пароли в SHA-512")[0], "evidence": ["EV-aaaaaa"]}]
    saved = changes.PostgresChanges().save(
        owner="sub-anna", key="PWD-36", title="Пароли", thread_id="t-1", graph="prep",
        requirements=[{"number": 1, "text": "Пароли в SHA-512 [EV-bbbbbb]", "status": "active",
                       "evidence": ["EV-bbbbbb"]}],
        evidence=[], committed=before,
    )

    written = [(sql.split()[2], params) for sql, params in conn.statements if sql.startswith("INSERT")]
    assert [table for table, _ in written] == ["changes", "requirements", "change_threads"]
    assert ["EV-bbbbbb"] in written[1][1]
    [item] = saved["requirements"]
    assert (item["change"], item["revision"]) == ("cited", 1)


def test_requirements_unseen_by_the_writer_do_not_drop():
    settled = changes.reconcile(
        existing("Пароли в SHA-512", "Журнал входов"), proposed(("1", "Пароли в SHA-512")), seen=[1]
    )

    assert [item["change"] for item in settled["requirements"]] == ["same", "untouched"]
    assert settled["requirements"][1]["status"] == "active"
