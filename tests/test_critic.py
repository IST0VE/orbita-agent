"""
Первый критик: каждое правило — на случае, где его нарушили 27 сентября 2026.

Документы здесь короткие и написаны руками, источники — записи Evidence той же
формы, что собирает конвейер. Модель не участвует нигде: критик её не зовёт.
"""

from __future__ import annotations

import pytest

from agent import confluence, critic, evidence, jira

ISSUE = {
    "key": "PWD-36",
    "url": "https://jira.example.com/browse/PWD-36",
    "summary": "Смена алгоритма паролей в AUTHGW",
    "description": "Пароли API перевести на SHA-512 в файле /app/etc/.apiusers.",
    "updated": "2026-09-20T10:00:00.000+0300",
    "comments": [],
    "comments_read": True,
}


def page(page_id: str, title: str, text: str, **extra) -> evidence.EvidenceItem:
    data = {"id": page_id, "title": title, "url": f"https://wiki.example.com/x/{page_id}",
            "text": text, "truncated": False, "version": 3, **extra}
    return evidence.for_page(data, confluence.format_page(data))


TICKET = evidence.for_issue(ISSUE, jira.format_issue(ISSUE))
AUTHGW = page("900002", "AUTHGW: authgw-user-mgmt", "Пользователи API лежат в /app/etc/.apiusers, хеш sha512.")
SYNCGW = page("900003", "SYNCGW: синхронизация конфигурации",
            "Изменения идут через sync config в syncgw-proxy, пользователи в users_config.")
OWN = page(
    "900001",
    "PWD-36 подготовка",
    "Страница собрана автоматически конвейером подготовки задачи Orbita. Обновлено: x. "
    "Правки руками затрёт следующий прогон треда.\nСтатус: Done",
)
ITEMS = {item.id: item for item in (TICKET, AUTHGW, SYNCGW, OWN)}


def check(document: str, **kwargs) -> critic.Report:
    return critic.Critic(ITEMS, primary=TICKET.id, **kwargs).check(document, stage="draft")


def kinds(report: critic.Report) -> list[str]:
    return [finding.kind for finding in report.findings]


def test_a_quote_found_in_the_cited_source_is_confirmed_with_its_place():
    report = check(f"- Хранилище — «/app/etc/.apiusers» {AUTHGW.tag}.")

    assert kinds(report) == []
    assert (report.quotes, report.verified) == (1, 1)
    assert report.places[0]["ref"] == AUTHGW.id


def test_quotes_ignore_case_dashes_and_line_breaks():
    report = check(f"- «пользователи API ЛЕЖАТ в /app/etc/.apiusers» {AUTHGW.tag}")

    assert report.verified == 1


def test_an_unknown_id_is_named():
    report = check("- Решение принято [EV-123456], см. также EV-12.")

    assert kinds(report) == ["unknown_ref", "unknown_ref"]
    assert "EV-123456" in report.findings[0].detail


def test_a_quote_from_nowhere_is_named():
    report = check(f"- В задаче написано «пароли хранятся в LDAP» {TICKET.tag}.")

    assert kinds(report) == ["quote_missing"]


def test_an_invented_file_name_in_code_is_named():
    """Имя в обратных кавычках — тоже слова источника: `.htpasswd` в задаче нет."""
    report = check(f"- Пароли лежат в `/app/etc/.htpasswd` {TICKET.tag}.")

    assert kinds(report) == ["quote_missing"]


def test_a_quote_from_another_source_is_named_with_that_source():
    """Цитата о SYNCGW, выданная за AUTHGW: слова есть, но не там, куда ссылка."""
    report = check(f"- Изменения вносятся через «sync config в syncgw-proxy» {AUTHGW.tag}.")

    assert kinds(report) == ["quote_elsewhere"]
    assert report.findings[0].ref == SYNCGW.id


def test_a_fact_about_one_system_attributed_to_another_is_named():
    """27 сентября: «В AUTHGW …» со ссылкой на страницу про SYNCGW, где AUTHGW нет вовсе."""
    report = check(f"- В AUTHGW конфигурация синхронизируется через sync config {SYNCGW.tag}.")

    assert kinds(report) == ["foreign_subject"]
    assert "authgw" in report.findings[0].detail


def test_a_transfer_marked_as_inference_is_not_a_violation():
    report = check(
        f"- Страница про SYNCGW, применимость к AUTHGW не подтверждена {SYNCGW.tag} [ВЫВОД]."
    )

    assert kinds(report) == []


def test_the_own_draft_of_orbita_is_not_a_source():
    report = check(f"- Расхождение: статус Done {OWN.tag}, а в задаче In Progress {TICKET.tag}.")

    assert kinds(report) == ["own_source"]


def test_a_line_that_calls_the_own_draft_a_draft_is_fine():
    report = check(f"- {OWN.tag} — черновик Orbita прошлого прогона, как источник не взят.")

    assert "own_source" not in kinds(report)


@pytest.mark.parametrize(
    "line",
    [
        f"- В комментариях договорились менять постепенно {TICKET.tag}.",
        "- Комментарии к задаче не прочитаны — уточнить у автора.",
        "- Непрочитанные комментарии PWD-36 могут менять требования.",
    ],
)
def test_claims_about_comments_of_an_issue_without_them_are_named(line):
    assert kinds(check(line)) == ["phantom_comment"]


@pytest.mark.parametrize(
    "line",
    [
        f"- Комментариев у задачи нет {TICKET.tag}.",
        f"- У задачи 0 комментариев {TICKET.tag}.",
        "- Комментарии: нет.",
    ],
)
def test_saying_there_are_no_comments_is_true(line):
    assert kinds(check(line)) == []


def test_comments_of_an_issue_that_has_them_are_not_questioned():
    issue = {**ISSUE, "comments": [{"author": "П", "created": "2026-09-01", "text": "ок"}]}
    item = evidence.for_issue(issue, jira.format_issue(issue))
    report = critic.Critic({item.id: item}, primary=item.id).check(
        f"- В комментариях согласовали {item.tag}.", stage="intake"
    )

    assert kinds(report) == []


def test_unrequested_comments_are_named_as_unrequested():
    issue = {**ISSUE, "comments_read": False}
    item = evidence.for_issue(issue, jira.format_issue(issue))
    report = critic.Critic({item.id: item}, primary=item.id).check(
        f"- В комментариях решили иначе {item.tag}.", stage="intake"
    )

    assert "не запрашивались" in report.findings[0].detail


def test_a_legacy_tag_resolves_to_the_read_source():
    report = check("- Хранилище — «/app/etc/.apiusers» [WIKI 900002].")

    assert kinds(report) == []
    assert report.verified == 1


def test_a_legacy_tag_to_an_unread_page_is_named_unless_the_line_says_so():
    assert kinds(check("- Формат описан [WIKI 555].")) == ["unread_ref"]
    assert kinds(check("- Найдено, но не открыто: [WIKI 555].")) == []


def test_words_of_the_operator_are_not_words_of_the_ticket():
    report = check(
        f"- Задача требует «сделать без простоя» {TICKET.tag}.",
        request="нужно сделать без простоя до пятницы",
    )

    assert kinds(report) == ["quote_elsewhere"]
    assert "запроса оператора" in report.findings[0].detail


def test_headings_and_code_blocks_are_not_checked():
    document = "## В AUTHGW «всё иначе» [EV-000000]\n```\n«несуществующее» [EV-000000]\n```"

    assert kinds(check(document)) == []


def test_known_phrases_are_not_quotes():
    report = check(
        f"- См. раздел «Что нужно выяснить» {TICKET.tag}.", ignore={"Что нужно выяснить"}
    )

    assert report.quotes == 0


def test_the_render_counts_and_lists_findings():
    good = check(f"- «/app/etc/.apiusers» {AUTHGW.tag}").to_dict()
    bad = check(f"- Статус Done {OWN.tag}").to_dict()
    text = critic.render(
        {"intake": good, "draft": bad}, {"intake": "01. Разбор", "draft": "05. Документация"}
    )

    assert "| 01. Разбор | 1 | 1 | 1 | 1 | 0 |" in text
    assert "### Замечания: 1" in text
    assert "собрана самой Orbita" in text
    assert critic.summary({"intake": good, "draft": bad})["kinds"] == {"own_source": 1}
