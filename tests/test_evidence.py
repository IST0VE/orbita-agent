"""
Запись Evidence: что прочитано, какой версии, кем и как это видит модель.

Сеть не участвует: задачи и страницы — словари той же формы, что отдают
`jira.fetch_issue` и `confluence.fetch_page`.
"""

from __future__ import annotations

from langchain_core.messages import ToolMessage

from agent import confluence, evidence, jira, sources, tools

ISSUE = {
    "key": "PWD-36",
    "url": "https://jira.example.com/browse/PWD-36",
    "summary": "Смена алгоритма паролей",
    "description": "Перейти на SHA-512.",
    "updated": "2026-09-20T10:00:00.000+0300",
    "comments": [],
    "comments_read": True,
}
PAGE = {
    "id": "900002",
    "title": "authgw-user-mgmt",
    "url": "https://wiki.example.com/x/900002",
    "text": "Пользователи API лежат в /app/etc/.apiusers.",
    "truncated": False,
    "version": 7,
}
OWN_TEXT = (
    "Страница собрана автоматически конвейером подготовки задачи Orbita. "
    "Обновлено: 2026-09-24T10:00:00. Правки руками затрёт следующий прогон треда.\n"
    "Статус задачи: Done"
)


def test_the_same_source_of_the_same_version_gets_the_same_id():
    """Id не зависит от порядка чтения: кодом до ролей и инструментом — один и тот же."""
    first = evidence.for_issue(ISSUE, jira.format_issue(ISSUE))
    again = evidence.for_issue(dict(ISSUE), jira.format_issue(ISSUE))

    assert first.id == again.id
    assert evidence.ID.fullmatch(first.id)


def test_a_new_version_is_a_new_source():
    first = evidence.for_page(PAGE, confluence.format_page(PAGE))
    newer = evidence.for_page({**PAGE, "version": 8}, confluence.format_page(PAGE))

    assert first.id != newer.id
    assert (first.version, newer.version) == ("7", "8")


def test_the_record_keeps_the_hash_of_exactly_what_was_read():
    text = confluence.format_page(PAGE)
    item = evidence.for_page(PAGE, text)

    assert item.hash == evidence.digest(text)
    assert item.text == text
    assert item.location == "страница целиком"


def test_a_truncated_page_says_what_part_was_read():
    item = evidence.for_page({**PAGE, "truncated": True}, "кусок")

    assert "обрезано" in item.location
    assert item.meta["truncated"] is True


def test_the_reader_is_the_shared_token_outside_a_user_run():
    """Без пользователя прогона читает общий токен из `.env` — так и записано."""
    item = evidence.for_issue(ISSUE, jira.format_issue(ISSUE))

    assert item.reader == evidence.SHARED


def test_the_reader_is_the_user_whose_token_was_used(monkeypatch):
    from agent import credentials

    monkeypatch.setattr(credentials, "current_subject", lambda: "anna")
    item = evidence.for_issue(ISSUE, jira.format_issue(ISSUE))

    assert item.reader == "anna"


def test_a_page_published_by_orbita_is_marked_as_its_own():
    item = evidence.for_page({**PAGE, "text": OWN_TEXT}, OWN_TEXT)

    assert item.own
    assert evidence.OWN_WARNING in evidence.header(item)


def test_an_ordinary_page_is_not_own():
    assert not evidence.for_page(PAGE, confluence.format_page(PAGE)).own


def test_comments_are_counted_for_the_critic():
    item = evidence.for_issue(ISSUE, jira.format_issue(ISSUE))

    assert item.meta == {"comments": 0, "comments_read": True, "truncated": False}
    unasked = evidence.for_issue({**ISSUE, "comments_read": False}, "x" * 10_000)
    assert "не запрашивались" in unasked.location


def test_the_header_names_the_id_first():
    item = evidence.for_page(PAGE, confluence.format_page(PAGE))

    head = evidence.header(item)
    assert head.startswith(f"Источник {item.tag}: страница Confluence 900002")
    assert "версия 7" in head


def test_a_tool_answer_carries_the_record_without_a_second_copy_of_the_text():
    item = evidence.for_page(PAGE, confluence.format_page(PAGE))
    content, meta = evidence.attach(item)
    message = ToolMessage(content=content, tool_call_id="c1", artifact={"evidence": meta})

    assert "text" not in meta
    assert evidence.from_message(message) == item


def test_an_edited_tool_answer_is_not_trusted():
    item = evidence.for_page(PAGE, confluence.format_page(PAGE))
    content, meta = evidence.attach(item)
    message = ToolMessage(
        content=content + " дописано", tool_call_id="c1", artifact={"evidence": meta}
    )

    assert evidence.from_message(message) is None


def test_gather_keeps_the_longer_read_of_the_same_source():
    whole = evidence.for_page(PAGE, confluence.format_page(PAGE))
    cut = evidence.EvidenceItem.from_dict({**whole.to_dict(), "text": whole.text[:10]})
    content, meta = evidence.attach(whole)
    message = ToolMessage(content=content, tool_call_id="c1", artifact={"evidence": meta})

    found = evidence.gather({cut.id: cut.to_dict()}, [message])

    assert found[whole.id].text == whole.text


def test_the_location_of_a_quote_in_an_issue_is_its_section():
    issue = {**ISSUE, "comments": [
        {"author": "Пётр", "created": "2026-08-20", "text": "Меняем постепенно."}
    ]}
    item = evidence.for_issue(issue, jira.format_issue(issue))
    lines = item.text.split("\n")

    assert evidence.locate(item, next(i for i, x in enumerate(lines) if "SHA-512" in x)) == "описание"
    assert evidence.locate(
        item, next(i for i, x in enumerate(lines) if "постепенно" in x)
    ) == "комментарий 2026-08-20 Пётр"


# --------------------------------------------------------------------------
# Кто отдаёт записи
# --------------------------------------------------------------------------
def test_sources_read_an_issue_with_its_record(monkeypatch):
    monkeypatch.setattr(jira, "fetch_issue", lambda key, *a, **k: dict(ISSUE, key=key))

    issue, item = sources.read_issue("PWD-36")

    assert item.source_id == issue["key"] == "PWD-36"
    assert item.text == jira.format_issue(issue)


def test_files_of_the_chat_come_with_a_record_per_file(monkeypatch):
    from agent import inputs

    folder = inputs.ensure_root() / "t"
    folder.mkdir(parents=True)
    (folder / "встреча.md").write_text("договорились о SHA-512", encoding="utf-8")

    found = sources.from_files("по встреча.md", "t")

    record = found["evidence"]["встреча.md"]
    assert record["system"] == "file"
    assert record["text"] == "договорились о SHA-512"


def test_the_cited_tools_show_the_id_and_the_plain_ones_do_not(monkeypatch):
    """Шапку с id видит только конвейер, который ссылается на Evidence."""
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://wiki.example.com")
    monkeypatch.setenv("CONFLUENCE_TOKEN", "tok")
    monkeypatch.setenv("CONFLUENCE_SPACE_KEY", "DOC")
    monkeypatch.setattr(confluence, "fetch_page", lambda page_id, *a, **k: dict(PAGE))
    call = {"type": "tool_call", "name": "confluence_page", "args": {"page_id": "900002"}, "id": "t"}

    cited = tools.confluence_page_cited.invoke(call)
    plain = tools.confluence_page.invoke(call)

    assert cited.content.startswith("Источник [EV-")
    assert plain.content == confluence.format_page(PAGE)
    assert evidence.from_message(cited).id == evidence.from_message(plain).id


def test_the_cited_toolset_has_the_same_schemas():
    """Схемы уезжают в кешируемый префикс: вариант с Evidence их не меняет."""
    def schemas(toolset):
        return [(t.name, t.description, t.args) for t in toolset]

    assert schemas(tools.CITED_TOOLS) == schemas(tools.RESEARCH_TOOLS)
