import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import requests

from agent import config as cfg
from agent import confluence, jira, request_pacing

BLOCK_PAGE = (
    '<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">'
    "<title>Access Blocked</title></head><body>Доступ ограничен</body></html>"
)


def reply(status: int, *, json_body=None, text: str = "", headers: dict | None = None):
    """Готовый ответ сервера: JSON от приложения или HTML-страница шлюза."""
    response = requests.Response()
    response.status_code = status
    response.encoding = "utf-8"
    response.headers.update(headers or {})
    if json_body is not None:
        response._content = json.dumps(json_body).encode()
        response.headers["Content-Type"] = "application/json"
    else:
        response._content = text.encode()
        response.headers.setdefault("Content-Type", "text/html; charset=utf-8")
    return response


class Server:
    """Подставная сессия: очередь ответов по порядку, последний повторяется."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []
        self.headers = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        self.headers.append(kwargs.get("headers") or {})
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply

    def methods(self):
        return [method for method, _ in self.calls]


def connection_lost(cause: Exception | None = None) -> requests.ConnectionError:
    """Отказ уровня соединения в той же обёртке, что приходит из urllib3."""
    failure = requests.ConnectionError("Max retries exceeded with url: /rest/api/content?title=…")
    failure.__cause__ = cause
    return failure


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def pacer(self):
        return request_pacing.RequestPacer(clock=lambda: self.now, sleep=self.sleep)


def test_first_request_is_immediate_and_pause_starts_after_completion():
    clock = Clock()
    pacer = clock.pacer()
    with pacer.slot(5):
        assert clock.now == 0
        clock.now += 12  # Медленный ответ всё равно требует паузы после него.
    with pacer.slot(5):
        assert clock.now == 17
    clock.now += 9  # После достаточного простоя дополнительного ожидания нет.
    with pacer.slot(5):
        assert clock.now == 26
    assert clock.sleeps == [5]


def test_parallel_workers_share_one_queue_without_bursts():
    clock = Clock()
    pacer = clock.pacer()
    starts = []

    def request(_):
        with pacer.slot(5):
            starts.append(clock.now)
            clock.now += 2

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(request, range(12)))
    assert starts == list(range(0, 84, 7))
    assert clock.sleeps == [5] * 11


def test_failure_releases_queue_and_next_attempt_still_waits():
    clock = Clock()
    pacer = clock.pacer()
    with pytest.raises(requests.Timeout), pacer.slot(5):
        clock.now += 30
        raise requests.Timeout("slow server")
    with pacer.slot(5):
        assert clock.now == 35


@pytest.mark.parametrize("failed", [False, True])
def test_both_transports_share_pause_for_get_post_put_and_network_errors(monkeypatch, failed):
    clock = Clock()
    monkeypatch.setattr(request_pacing, "_pacer", clock.pacer())
    starts = []

    def request(method, url, **kwargs):
        starts.append((method, clock.now, kwargs["timeout"]))
        clock.now += 2
        if failed and len(starts) == 1:
            # Отказ, который повторять бесполезно: очередь обязана выдержать
            # паузу и после него. Повторяемые проверяются отдельными тестами.
            raise requests.ConnectionError("[Errno 11001] getaddrinfo failed")
        response = requests.Response()
        response.status_code = 200
        response._content = b'{}'
        return response

    monkeypatch.setattr(request_pacing, "_session", SimpleNamespace(request=request))
    wiki = confluence.Settings(base_url="https://wiki.example.com", token="test", timeout_s=30)
    tracker = jira.Settings(base_url="https://jira.example.com", token="test", timeout_s=40)
    if failed:
        with pytest.raises(confluence.ConfluenceError):
            confluence._call("GET", "/search", wiki)
    else:
        confluence._call("GET", "/search", wiki)
    jira.call("POST", "/issue", tracker)
    confluence._call("PUT", "/content/123", wiki)
    jira.call("GET", "/search", tracker)
    assert starts == [("GET", 0, 30), ("POST", 12, 40), ("PUT", 24, 30), ("GET", 36, 40)]


def test_interval_defaults_to_ten_and_can_be_changed_or_disabled(monkeypatch):
    assert cfg.atlassian_request_interval_s() == 10
    clock = Clock()
    monkeypatch.setattr(request_pacing, "_pacer", clock.pacer())
    monkeypatch.setattr(
        request_pacing, "_session", SimpleNamespace(request=lambda *a, **kw: reply(200, json_body={}))
    )
    request_pacing.send("GET", "https://wiki.example.com", timeout=30)
    monkeypatch.setenv("ATLASSIAN_REQUEST_INTERVAL_S", "7.5")
    request_pacing.send("GET", "https://wiki.example.com", timeout=30)
    assert clock.now == 7.5
    monkeypatch.setenv("ATLASSIAN_REQUEST_INTERVAL_S", "0")
    request_pacing.send("GET", "https://wiki.example.com", timeout=30)
    assert clock.now == 7.5


def test_each_system_keeps_its_own_pause_in_one_queue(monkeypatch):
    """
    Медленная wiki не обязана замедлять трекер.

    Очередь при этом одна: за обоими хостами стоит один периметр, и общая
    частота от разделения очередей выросла бы вдвое.
    """
    monkeypatch.setenv("CONFLUENCE_REQUEST_INTERVAL_S", "25")
    clock = Clock()
    monkeypatch.setattr(request_pacing, "_pacer", clock.pacer())
    server = Server(reply(200, json_body={}))
    monkeypatch.setattr(request_pacing, "_session", server)
    wiki = confluence.Settings(base_url="https://wiki.example.com", token="test", interval_s=25)
    tracker = jira.Settings(base_url="https://jira.example.com", token="test", interval_s=10)
    jira.call("GET", "/myself", tracker)
    confluence._call("GET", "/rest/api/content", wiki)  # ждёт 25 — правило wiki
    jira.call("GET", "/myself", tracker)  # ждёт 10 — правило трекера
    assert clock.sleeps == [25, 10]
    assert cfg.confluence_request_interval_s() == 25
    assert cfg.jira_request_interval_s() == 10  # своей переменной нет — общая пауза


@pytest.mark.parametrize("value", ["-1", "nan", "inf", "-inf", "invalid"])
def test_invalid_interval_is_rejected(monkeypatch, value):
    monkeypatch.setenv("ATLASSIAN_REQUEST_INTERVAL_S", value)
    with pytest.raises(cfg.ConfigError):
        cfg.atlassian_request_interval_s()


# --------------------------------------------------------------------------
# Отказ защитного шлюза
#
# Шлюз перед корпоративным контуром отвечает HTML-страницей с кодом 403, и
# запрос до приложения не доходит. Проверяется главное: такой отказ назван
# своим именем, не превращается в совет про учётные данные, пережидается на
# чтении и не заводит второй черновик на записи.
# --------------------------------------------------------------------------
def test_html_block_is_named_by_the_gateway_and_never_by_credentials(monkeypatch):
    server = Server(reply(403, text=BLOCK_PAGE))
    monkeypatch.setattr(request_pacing, "_session", server)
    wiki = confluence.Settings(base_url="https://wiki.example.com", token="test")
    with pytest.raises(confluence.ConfluenceBlocked) as blocked:
        confluence._call("POST", "/rest/api/content", wiki)
    message = str(blocked.value)
    assert "Access Blocked" in message
    assert "ATLASSIAN_REQUEST_INTERVAL_S" in message
    assert "CONFLUENCE_EMAIL" not in message
    assert "<html" not in message and "DOCTYPE" not in message


def test_jira_reports_the_same_block_without_advising_the_email(monkeypatch):
    server = Server(reply(403, text=BLOCK_PAGE))
    monkeypatch.setattr(request_pacing, "_session", server)
    tracker = jira.Settings(base_url="https://jira.example.com", token="test")
    with pytest.raises(jira.JiraBlocked) as blocked:
        jira.call("POST", "/rest/api/2/issue", tracker)
    message = str(blocked.value)
    assert "Access Blocked" in message
    assert "JIRA_EMAIL" not in message


@pytest.mark.parametrize("status", [401, 403])
def test_denial_from_the_application_still_blames_the_token(monkeypatch, status):
    server = Server(reply(status, json_body={"message": "нет прав на пространство"}))
    monkeypatch.setattr(request_pacing, "_session", server)
    wiki = confluence.Settings(base_url="https://wiki.example.com", token="test")
    with pytest.raises(confluence.ConfluenceError) as failure:
        confluence._call("GET", "/rest/api/content", wiki)
    assert not isinstance(failure.value, confluence.ConfluenceBlocked)
    assert "нет прав на пространство" in str(failure.value)
    assert "CONFLUENCE_EMAIL" in str(failure.value)
    assert server.methods() == ["GET"]  # отказ приложения повторять нечего


def test_blocked_read_waits_and_repeats_until_the_gateway_lets_it_through(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(request_pacing, "_pacer", clock.pacer())
    server = Server(
        reply(403, text=BLOCK_PAGE),
        reply(503, text=BLOCK_PAGE),
        reply(200, json_body={"results": []}),
    )
    monkeypatch.setattr(request_pacing, "_session", server)
    wiki = confluence.Settings(base_url="https://wiki.example.com", token="test")
    assert confluence._call("GET", "/rest/api/content", wiki) == {"results": []}
    assert server.methods() == ["GET", "GET", "GET"]
    assert clock.sleeps == [15, 30]  # пауза удваивается, очередь ждёт вместе с запросом


def test_read_gives_up_after_the_configured_number_of_attempts(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_BLOCK_RETRIES", "1")
    clock = Clock()
    monkeypatch.setattr(request_pacing, "_pacer", clock.pacer())
    server = Server(reply(403, text=BLOCK_PAGE))
    monkeypatch.setattr(request_pacing, "_session", server)
    tracker = jira.Settings(base_url="https://jira.example.com", token="test")
    with pytest.raises(jira.JiraBlocked):
        jira.call("GET", "/rest/api/2/search", tracker)
    assert server.methods() == ["GET", "GET"]
    assert clock.sleeps == [15]


def test_retry_after_wins_over_the_doubling_backoff(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(request_pacing, "_pacer", clock.pacer())
    server = Server(
        reply(503, text=BLOCK_PAGE, headers={"Retry-After": "45"}),
        reply(200, json_body={}),
    )
    monkeypatch.setattr(request_pacing, "_session", server)
    wiki = confluence.Settings(base_url="https://wiki.example.com", token="test")
    confluence._call("GET", "/rest/api/content", wiki)
    assert clock.sleeps == [45]


def test_rate_limit_from_the_application_is_a_block_too_but_a_long_wait_is_reported(monkeypatch):
    server = Server(reply(429, json_body={"message": "too many"}, headers={"Retry-After": "3600"}))
    monkeypatch.setattr(request_pacing, "_session", server)
    tracker = jira.Settings(base_url="https://jira.example.com", token="test")
    with pytest.raises(jira.JiraBlocked) as blocked:
        jira.call("GET", "/rest/api/2/search", tracker)
    assert "3600 с" in str(blocked.value)
    assert server.methods() == ["GET"]  # столько ждать внутри прогона незачем


def test_draft_creation_finds_the_page_the_block_hid_instead_of_creating_it_twice(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(request_pacing, "_pacer", clock.pacer())
    created = {"id": "12345", "status": "draft", "title": "Схема"}
    server = Server(
        reply(200, json_body={"results": []}),  # черновика ещё нет
        reply(403, text=BLOCK_PAGE),  # POST отбит шлюзом...
        reply(200, json_body={"results": [created]}),  # ...но страница уже создана
        reply(200, json_body={"value": "shared"}),
    )
    monkeypatch.setattr(request_pacing, "_session", server)
    wiki = confluence.Settings(base_url="https://wiki.example.com", token="test", space_key="DOC")
    draft = confluence.create_draft("Схема", "<p>тело</p>", wiki)
    assert server.methods() == ["GET", "POST", "GET", "GET"]
    assert draft["page_id"] == "12345"
    assert "draftShareId=shared" in draft["url"]
    # Пауза очереди, откат после блокировки, снова пауза очереди: повторный
    # поиск ждать не пришлось — откат уже перекрыл интервал.
    assert clock.sleeps == [10, 15, 10]


def test_draft_creation_repeats_the_post_when_the_page_was_never_created(monkeypatch):
    server = Server(
        reply(200, json_body={"results": []}),
        reply(403, text=BLOCK_PAGE),
        reply(200, json_body={"results": []}),
        reply(200, json_body={"id": "777", "status": "draft"}),
        reply(200, json_body={"value": "shared"}),
    )
    monkeypatch.setattr(request_pacing, "_session", server)
    wiki = confluence.Settings(base_url="https://wiki.example.com", token="test", space_key="DOC")
    draft = confluence.create_draft("Схема", "<p>тело</p>", wiki)
    assert server.methods() == ["GET", "POST", "GET", "POST", "GET"]
    assert draft["page_id"] == "777"


def test_publication_waits_out_the_block_and_updates_the_page_it_finds(monkeypatch):
    """Публикация — upsert по заголовку, и после блокировки она остаётся им же."""
    page = {"id": "42", "status": "current", "title": "Схема", "version": {"number": 3}}
    server = Server(
        reply(200, json_body={"results": []}),  # страницы ещё нет
        reply(403, text=BLOCK_PAGE),  # POST отбит шлюзом...
        reply(200, json_body={"results": [page]}),  # ...но страница создана
        reply(200, json_body={"id": "42", "version": {"number": 4}}),
    )
    monkeypatch.setattr(request_pacing, "_session", server)
    wiki = confluence.Settings(base_url="https://wiki.example.com", token="test", space_key="DOC")
    result = confluence.publish_page("Схема", "<p>тело</p>", wiki)
    assert server.methods() == ["GET", "POST", "GET", "PUT"]
    assert result == {
        "status": "updated",
        "page_id": "42",
        "version": 4,
        "title": "Схема",
        "url": "https://wiki.example.com/pages/viewpage.action?pageId=42",
    }


def test_draft_creation_reports_the_block_that_never_lifts(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_BLOCK_RETRIES", "1")
    server = Server(
        reply(200, json_body={"results": []}),
        reply(403, text=BLOCK_PAGE),
        reply(200, json_body={"results": []}),
        reply(403, text=BLOCK_PAGE),
    )
    monkeypatch.setattr(request_pacing, "_session", server)
    wiki = confluence.Settings(base_url="https://wiki.example.com", token="test", space_key="DOC")
    with pytest.raises(confluence.ConfluenceBlocked):
        confluence.create_draft("Схема", "<p>тело</p>", wiki)
    assert server.methods() == ["GET", "POST", "GET", "POST"]


# --------------------------------------------------------------------------
# Блокировка на уровне соединения
#
# Набрав частоту, шлюз перестаёт отвечать страницей и начинает рвать
# соединение. Для прогона это тот же отказ, только без HTTP-кода, и ошибка
# requests приходит вместе с адресом запроса, который читать невозможно.
# --------------------------------------------------------------------------
def test_broken_connection_is_read_as_a_block_and_waited_out(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(request_pacing, "_pacer", clock.pacer())
    server = Server(
        connection_lost(ConnectionResetError(10054, "соединение разорвано")),
        reply(200, json_body={"results": []}),
    )
    monkeypatch.setattr(request_pacing, "_session", server)
    wiki = confluence.Settings(base_url="https://wiki.example.com", token="test")
    assert confluence._call("GET", "/rest/api/content", wiki) == {"results": []}
    assert server.methods() == ["GET", "GET"]
    assert clock.sleeps == [15]


def test_broken_connection_message_names_the_cause_without_the_request_url(monkeypatch):
    server = Server(connection_lost(ConnectionResetError(10054, "connection reset by peer")))
    monkeypatch.setattr(request_pacing, "_session", server)
    wiki = confluence.Settings(base_url="https://wiki.example.com", token="test")
    with pytest.raises(confluence.ConfluenceBlocked) as blocked:
        confluence._call("POST", "/rest/api/content", wiki)
    message = str(blocked.value)
    assert "сервер разорвал соединение" in message
    assert "connection reset by peer" in message
    assert "Max retries exceeded" not in message
    assert "?title=" not in message
    assert "ATLASSIAN_REQUEST_INTERVAL_S" in message


def test_unresolvable_host_is_not_waited_out(monkeypatch):
    """Пауза не чинит опечатку в адресе: такой отказ отдаётся сразу."""
    clock = Clock()
    monkeypatch.setattr(request_pacing, "_pacer", clock.pacer())
    server = Server(connection_lost(OSError("[Errno 11001] getaddrinfo failed")))
    monkeypatch.setattr(request_pacing, "_session", server)
    tracker = jira.Settings(base_url="https://jira.example.com", token="test")
    with pytest.raises(jira.JiraError) as failure:
        jira.call("GET", "/rest/api/2/search", tracker)
    assert not isinstance(failure.value, jira.JiraBlocked)
    assert "имя хоста не разрешается" in str(failure.value)
    assert server.methods() == ["GET"]
    assert clock.sleeps == []


def test_draft_creation_survives_a_broken_connection_on_the_post(monkeypatch):
    server = Server(
        reply(200, json_body={"results": []}),
        connection_lost(ConnectionResetError(10054, "сброс")),
        reply(200, json_body={"results": []}),
        reply(200, json_body={"id": "88", "status": "draft"}),
        reply(404, json_body={"message": "no property"}),
    )
    monkeypatch.setattr(request_pacing, "_session", server)
    wiki = confluence.Settings(base_url="https://wiki.example.com", token="test", space_key="DOC")
    draft = confluence.create_draft("Схема", "<p>тело</p>", wiki)
    assert server.methods() == ["GET", "POST", "GET", "POST", "GET"]
    assert draft["page_id"] == "88"


def test_requests_go_through_one_session_and_introduce_themselves(monkeypatch):
    """Cookie проверки и keep-alive живут в сессии; агент называет себя."""
    assert isinstance(request_pacing._session, requests.Session)
    server = Server(reply(200, json_body={}))
    monkeypatch.setattr(request_pacing, "_session", server)
    wiki = confluence.Settings(base_url="https://wiki.example.com", token="test")
    confluence._call("GET", "/rest/api/content", wiki)
    monkeypatch.setenv("ATLASSIAN_USER_AGENT", "T1-Orbita/1.0")
    confluence._call("GET", "/rest/api/content", wiki)
    monkeypatch.setenv("ATLASSIAN_USER_AGENT", " ")
    confluence._call("GET", "/rest/api/content", wiki)
    agents = [headers.get("User-Agent") for headers in server.headers]
    assert agents[0] == "Orbita (Jira/Confluence integration)"
    assert agents[1] == "T1-Orbita/1.0"
    assert agents[2] == "Orbita (Jira/Confluence integration)"  # пробелы — не значение


def test_missing_share_id_is_asked_about_once_per_instance(monkeypatch):
    """Свойства нет на Server/DC: спрашивать его на каждой странице незачем."""
    server = Server(
        reply(200, json_body={"results": []}),
        reply(200, json_body={"id": "1", "status": "draft"}),
        reply(404, json_body={"message": "no property"}),
        reply(200, json_body={"results": []}),
        reply(200, json_body={"id": "2", "status": "draft"}),
    )
    monkeypatch.setattr(request_pacing, "_session", server)
    wiki = confluence.Settings(base_url="https://wiki.example.com", token="test", space_key="DOC")
    first = confluence.create_draft("Первая", "<p>тело</p>", wiki)
    second = confluence.create_draft("Вторая", "<p>тело</p>", wiki)
    assert server.methods() == ["GET", "POST", "GET", "GET", "POST"]
    assert "draftShareId" not in first["url"] and "draftShareId" not in second["url"]
    assert "владельцем токена" not in second["reason"]


def test_block_on_the_share_id_is_not_taken_for_a_missing_property(monkeypatch):
    """Отказ шлюза ничего не говорит об инстансе: следующая страница спросит снова."""
    monkeypatch.setenv("ATLASSIAN_BLOCK_RETRIES", "0")
    server = Server(
        reply(200, json_body={"results": []}),
        reply(200, json_body={"id": "1", "status": "draft"}),
        reply(403, text=BLOCK_PAGE),
        reply(200, json_body={"results": []}),
        reply(200, json_body={"id": "2", "status": "draft"}),
        reply(200, json_body={"value": "shared"}),
    )
    monkeypatch.setattr(request_pacing, "_session", server)
    wiki = confluence.Settings(base_url="https://wiki.example.com", token="test", space_key="DOC")
    confluence.create_draft("Первая", "<p>тело</p>", wiki)
    second = confluence.create_draft("Вторая", "<p>тело</p>", wiki)
    assert "draftShareId=shared" in second["url"]


def test_block_knobs_have_defaults_and_are_validated(monkeypatch):
    assert cfg.atlassian_block_retries() == 3
    assert cfg.atlassian_block_backoff_s() == 15
    monkeypatch.setenv("ATLASSIAN_BLOCK_RETRIES", "0")
    monkeypatch.setenv("ATLASSIAN_BLOCK_BACKOFF_S", "2.5")
    assert cfg.atlassian_block_retries() == 0
    assert cfg.atlassian_block_backoff_s() == 2.5
    monkeypatch.setenv("ATLASSIAN_BLOCK_RETRIES", "-1")
    monkeypatch.setenv("ATLASSIAN_BLOCK_BACKOFF_S", "-1")
    with pytest.raises(cfg.ConfigError):
        cfg.atlassian_block_retries()
    with pytest.raises(cfg.ConfigError):
        cfg.atlassian_block_backoff_s()
