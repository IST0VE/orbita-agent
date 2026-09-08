"""Общая очередь и повторы HTTP-запросов Jira и Confluence в процессе сервера.

Перед корпоративными Jira и Confluence обычно стоит защитный шлюз, и конвейер
для него выглядит роботом: десяток запросов подряд с одного адреса, пачкой,
без пауз. Отбивается такой всплеск не как отказ приложения: шлюз отвечает
HTML-страницей «Access Blocked» с кодом 403, до сервера запрос не доходит.
Объяснить это правами токена или настройками Confluence нельзя — приложение
запроса не видело, и подсказки про учётные данные в такой ответ дописывать
запрещено: они уводят разбор в сторону.

Поэтому здесь два механизма, общие для обоих клиентов:

    очередь   один запрос за раз и пауза после каждого ответа, включая
              ошибку, — чтобы конвейер не выглядел всплеском;
    повтор    отказ шлюза на безопасном методе пережидается с удвоением
              паузы: блокировка снимается сама, а прогон к этому моменту уже
              написан и оплачен моделью, ронять его из-за чужого лимита дорого.

Небезопасные методы (POST, PUT, DELETE) вслепую здесь не повторяются: вторая
страница или вторая карточка хуже честного отказа. Повтор записи делает
вызывающий код там, где перед следующей попыткой может проверить, не создан
ли объект уже, — так устроен `confluence.create_draft`.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from threading import Lock

import requests

from agent import config as cfg

# Методы без побочных эффектов: повторить их безопасно.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Коды, которыми отвечает шлюз или балансировщик, а не приложение: блокировка,
# лимит частоты, перегрузка. 520–527 — семейство кодов Cloudflare.
BLOCK_STATUSES = frozenset({403, 429, 502, 503, 504, *range(520, 528)})

# Дольше этого ждать внутри прогона бессмысленно: лучше внятный отказ, чтобы
# оператор перезапустил этап сам.
MAX_BLOCK_DELAY_S = 300.0

_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_OBJECT_REPR = re.compile(r"<[^<>]+ object at 0x[0-9a-fA-F]+>:?\s*")
_WITH_URL = re.compile(r"with url: \S+")
_DNS_MARKS = ("NameResolution", "gaierror", "getaddrinfo")
# Разрыв соединения приходит то классом urllib3, то кодом операционной
# системы: 10054 у Windows, ECONNRESET у остальных.
_RESET_NAMES = ("ConnectionReset", "RemoteDisconnected", "ProtocolError")
_RESET_MARKS = ("10054", "ECONNRESET", "reset by peer")


class RequestPacer:
    """Не допускает параллельных запросов и выдерживает паузу после ответа/ошибки."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._lock = Lock()
        self._finished_at: float | None = None

    @contextmanager
    def slot(self, interval: float) -> Iterator[None]:
        # Блокировка удерживается и во время HTTP-запроса: иначе долгий ответ
        # позволил бы другим графам открыть несколько запросов одновременно.
        with self._lock:
            if self._finished_at is not None:
                remaining = self._finished_at + interval - self._clock()
                while remaining > 0:
                    self._sleep(remaining)
                    remaining = self._finished_at + interval - self._clock()
            try:
                yield
            finally:
                # Сетевая ошибка тоже считается обращением к серверу/WAF.
                self._finished_at = self._clock()

    def pause(self, seconds: float) -> None:
        """Ожидание внутри уже занятого слота: откат после отказа шлюза."""
        if seconds > 0:
            self._sleep(seconds)


_pacer = RequestPacer()

# Одна сессия на процесс — не оптимизация, а то же самое требование не
# выглядеть роботом. Отдельный `requests.request()` на каждый запрос открывает
# новое TLS-соединение и приходит без cookie, поэтому защитный шлюз видит не
# клиента, который уже прошёл проверку, а поток незнакомцев с одного адреса.
# Сессия хранит cookie проверки и держит соединение открытым; очередь выше
# гарантирует, что пользуется ею один поток за раз.
_session = requests.Session()


def pause(seconds: float) -> None:
    """Пауза общей очереди: отдельная функция, чтобы тесты подменяли сон."""
    _pacer.pause(seconds)


def answered_by_api(response: requests.Response) -> bool:
    """
    Ответ пришёл от Jira/Confluence, а не от посредника.

    Признак — JSON: REST обеих систем отвечает им и на отказ тоже, а шлюзы,
    балансировщики и страницы входа отдают HTML. Точнее различить нечем:
    собственного признака у чужого ответа нет.
    """
    if "json" in (response.headers.get("Content-Type") or "").lower():
        return True
    try:
        response.json()
    except ValueError:
        return False
    return True


def html_title(response: requests.Response) -> str:
    """Заголовок страницы отказа: «Access Blocked» вместо простыни разметки."""
    match = _TITLE.search(response.text[:4000])
    return " ".join(match.group(1).split())[:80] if match else ""


def retry_after_s(response: requests.Response) -> float | None:
    """`Retry-After` в секундах: заголовок разрешает и число, и дату."""
    raw = (response.headers.get("Retry-After") or "").strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        moment = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0.0, (moment - datetime.now(UTC)).total_seconds())


def block_reason(response: requests.Response) -> str:
    """
    Почему отказ похож на шлюз или лимит частоты. Пусто — обычный отказ API.

    Отсюда берут текст оба клиента: сообщение об ошибке обязано называть
    источник отказа правильно, иначе оператор идёт проверять права токена,
    которые ни при чём.
    """
    if response.status_code < 400:
        return ""
    wait = retry_after_s(response)
    asked = f"; сервер просит подождать {wait:.0f} с" if wait else ""
    advice = "; повторите позже или увеличьте паузу ATLASSIAN_REQUEST_INTERVAL_S"
    if answered_by_api(response):
        if response.status_code == 429:
            return "сервер ограничил частоту запросов" + asked + advice
        return ""
    if response.status_code not in BLOCK_STATUSES:
        return ""
    title = html_title(response)
    page = f"HTML-страница «{title}»" if title else "HTML-страница"
    return (
        f"вместо ответа API пришла {page}: запрос отклонён защитным шлюзом "
        "перед сервером, приложение его не видело" + asked + advice
    )


def _causes(exc: BaseException) -> list[BaseException]:
    """
    Цепочка исключений от внешнего к самому внутреннему.

    У urllib3 внутренняя причина лежит не в `__cause__`, а в поле `reason`
    (`MaxRetryError`), и без него разбор останавливается на сообщении, где
    записан весь адрес запроса вместе с процентным кодированием заголовка.
    """
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and not any(current is item for item in chain):
        chain.append(current)
        reason = getattr(current, "reason", None)
        if isinstance(reason, BaseException):
            current = reason
        else:
            current = current.__cause__ or current.__context__
    return chain


def transport_detail(exc: BaseException) -> str:
    """Самая внутренняя причина: без адреса запроса и без repr объектов."""
    text = " ".join(str(_causes(exc)[-1]).split())
    text = _OBJECT_REPR.sub("", text)
    text = _WITH_URL.sub("with url: …", text)
    return text[:160]


def transport_reason(exc: BaseException) -> str:
    """
    Короткая причина сетевого отказа вместо простыни urllib3.

    В сообщении requests лежат и «Max retries exceeded», и весь URL: оператору
    это нечитаемо, а полезного там одно слово о том, что именно не вышло.
    """
    names = " ".join(type(item).__name__ for item in _causes(exc))
    detail = transport_detail(exc)
    if any(mark in names or mark in detail for mark in _DNS_MARKS):
        return "имя хоста не разрешается"
    if "SSL" in names:
        return "TLS-соединение не установилось"
    if any(mark in names for mark in _RESET_NAMES) or any(mark in detail for mark in _RESET_MARKS):
        return "сервер разорвал соединение"
    if isinstance(exc, requests.Timeout):
        return "сервер не ответил за отведённое время"
    if isinstance(exc, requests.ConnectionError):
        return "соединение не установлено"
    return type(exc).__name__


def transport_block(exc: BaseException) -> bool:
    """
    Похоже ли на защитный шлюз, а не на ошибку настройки.

    Набрав частоту, шлюз перестаёт отвечать страницей отказа и начинает рвать
    соединение — такое ожидание имеет смысл переждать. Неразрешимое имя хоста
    ждать бесполезно: пауза его не исправит.
    """
    names = " ".join(type(item).__name__ for item in _causes(exc))
    if any(mark in names or mark in transport_detail(exc) for mark in _DNS_MARKS):
        return False
    return isinstance(exc, requests.ConnectionError | requests.Timeout)


def block_delay(attempt: int, response: requests.Response | None = None) -> float | None:
    """
    Сколько ждать перед повторной попыткой; None — повторять уже не нужно.

    Паузы удваиваются: короткая блокировка снимается за первую, длинная — за
    последнюю, и обе не превращают прогон в бесконечное ожидание.
    """
    if attempt >= cfg.atlassian_block_retries():
        return None
    delay = cfg.atlassian_block_backoff_s() * (2**attempt)
    if response is not None:
        asked = retry_after_s(response)
        if asked is not None:
            delay = max(delay, asked)
    return None if delay > MAX_BLOCK_DELAY_S else delay


def send(
    method: str, url: str, *, timeout: float, interval: float | None = None, **kwargs
) -> requests.Response:
    """
    Запрос в общей очереди. Безопасный метод пережидает отказ шлюза, остальные — нет.

    Пережидаются оба вида отказа: и страница «Access Blocked» с кодом 403, и
    разорванное соединение. Второе — та же блокировка на шаг раньше, до HTTP,
    и разница для прогона только в том, что ответа с кодом не будет вовсе.

    Ожидание проходит внутри занятого слота намеренно: пока шлюз отбивает один
    запрос, соседним графам туда тем более не надо.

    Пауза — свойство запроса, а не очереди: `interval` задаёт её вызывающая
    система, и медленная wiki не обязана замедлять Jira. Очередь при этом
    остаётся одна на процесс, потому что за обоими хостами стоит один
    корпоративный периметр и считать он может по адресу источника.
    """
    repeatable = method.upper() in SAFE_METHODS
    # Redirects can forward document bodies to a different host or downgrade TLS.
    kwargs["allow_redirects"] = False
    headers = dict(kwargs.pop("headers", None) or {})
    agent = cfg.atlassian_user_agent()
    if agent:
        headers.setdefault("User-Agent", agent)
    if interval is None:
        interval = cfg.atlassian_request_interval_s()
    with _pacer.slot(interval):
        attempt = 0
        while True:
            try:
                response = _session.request(method, url, timeout=timeout, headers=headers, **kwargs)
            except requests.RequestException as exc:
                delay = block_delay(attempt) if repeatable and transport_block(exc) else None
                if delay is None:
                    raise
                _pacer.pause(delay)
                attempt += 1
                continue
            if not repeatable or not block_reason(response):
                return response
            delay = block_delay(attempt, response)
            if delay is None:
                return response
            _pacer.pause(delay)
            attempt += 1
