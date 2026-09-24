"""Ритм обращений к модели: шлюз считает частоту, а прогон об этом не знает.

Перед моделью в этом проекте обычно стоит не вендор, а шлюз (LiteLLM, свой
прокси, vLLM за балансировщиком), и лимит у него не на тред и не на день, а на
минуту: столько-то запросов и столько-то токенов. Конвейер упирается в такой
лимит не из-за объёма работы, а из-за формы: роль с инструментами делает ходы
подряд, без пауз, и каждый следующий ход тащит с собой всю переписку. Десяток
ходов за полминуты — и шлюз отвечает 429, хотя задача маленькая.

Отбивается это дорого. Повторы ограничены `LLM_MAX_RETRIES` и ждут паузу
из заголовка `retry-after`, но когда попытки кончаются, ошибка всё равно
прерывает прогон. Поэтому повторы дополняются очередью перед моделью.

Поэтому здесь не повтор, а ритм: запрос ждёт своей очереди ДО отправки, пока в
минутном окне не освободится место. Окно скользящее и общее на процесс: лимит
шлюз считает по ключу, а ключ у всех графов один.

Отдельно — запрос, который не пройдёт никогда. Если одна история весит больше
всего минутного бюджета токенов, ждать бессмысленно: через минуту она будет
такой же большой. Такому запросу здесь отказывают сразу и называют причину —
иначе оператор видит 429 и идёт просить у администратора лимит побольше, хотя
лечится это подрезкой истории.

Включается лимитами в окружении (`LLM_REQUESTS_PER_MINUTE`,
`LLM_TOKENS_PER_MINUTE`); оба по умолчанию нулевые, то есть ритма нет и
поведение прежнее. Ноль здесь означает «шлюз не считает», а не «считать нулём».
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC
from email.utils import parsedate_to_datetime
from threading import Lock
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.outputs import LLMResult

from agent import config as cfg

logger = logging.getLogger(__name__)

#: Окно, по которому считает шлюз. Минута — то, что пишут в заголовках и в
#: сообщениях об отказе и LiteLLM, и облачные провайдеры.
WINDOW_S = 60.0

#: Сколько символов считать токеном при оценке запроса ДО отправки. У langchain
#: по умолчанию четыре — это про английский текст. Документы и переписка здесь
#: русские, а кириллица токенизируется примерно втрое короче длины, и оценка по
#: четырём занижала бы вход ровно там, где решается, ждать или нет. Заниженная
#: оценка — это тот самый 429, ради которого всё написано.
CHARS_PER_TOKEN = 3.0

#: Ответ модели в бюджет тоже входит (шлюз считает total_tokens), а его длина
#: до запроса неизвестна. Если `LLM_MAX_TOKENS` не задан, резервируется это.
DEFAULT_COMPLETION_RESERVE = 1000


class RateLimitTooSmall(cfg.ConfigError):
    """Запрос больше всего минутного бюджета: пауза его не спасёт."""


class MinuteBudget:
    """Скользящее минутное окно: сколько запросов и токенов уже потрачено."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._lock = Lock()
        # [момент, токены] — список, а не кортеж: оценка заменяется фактом,
        # когда придёт ответ с usage.
        self._events: deque[list[float]] = deque()
        self._blocked_until = 0.0

    def _prune(self, now: float) -> tuple[int, float]:
        while self._events and self._events[0][0] <= now - WINDOW_S:
            self._events.popleft()
        return len(self._events), sum(item[1] for item in self._events)

    def _delay(self, tokens: float, requests: int, budget: int, now: float) -> float:
        """Сколько ждать, чтобы запрос поместился в окно. 0 — можно слать."""
        if budget and tokens > budget:
            raise RateLimitTooSmall(
                f"запрос к модели ({tokens:.0f} токенов) больше минутного бюджета шлюза "
                f"({budget}, LLM_TOKENS_PER_MINUTE): пауза его не спасёт. Подрежьте историю "
                "(LLM_MAX_HISTORY_TOKENS) или уменьшите объём чтения источников "
                "(CONFLUENCE_READ_MAX_CHARS, JIRA_READ_MAX_CHARS, *_SEARCH_LIMIT)."
            )
        if self._blocked_until > now:
            return self._blocked_until - now
        used_requests, used_tokens = self._prune(now)
        if requests and used_requests >= requests:
            return self._events[0][0] + WINDOW_S - now
        if not budget or used_tokens + tokens <= budget:
            return 0.0
        # Освобождать окно по одному событию, а не ждать минуту целиком: ранние
        # ходы конвейера мелкие, и часто хватает выбывания двух-трёх.
        freed = 0.0
        for moment, spent in self._events:
            freed += spent
            if used_tokens - freed + tokens <= budget:
                return moment + WINDOW_S - now
        return self._events[-1][0] + WINDOW_S - now

    def reserve(self, tokens: float, *, requests: int, budget: int) -> list[float]:
        """
        Занять место в окне, при необходимости дождавшись его.

        Проверка и запись атомарны, но сон отпускает замок: ответы других
        запросов должны обновлять usage и паузу шлюза даже во время ожидания.
        После сна место проверяется заново, прежде чем занять его.
        """
        while True:
            with self._lock:
                now = self._clock()
                delay = self._delay(tokens, requests, budget, now)
                if delay <= 0:
                    event = [now, float(tokens)]
                    self._events.append(event)
                    return event
            logger.info("Пауза перед вызовом модели %.1f с: минутный лимит шлюза", delay)
            self._sleep(delay)

    def settle(self, event: list[float], tokens: float) -> None:
        """Заменить оценку фактом из `usage`: дальше окно считает по правде."""
        with self._lock:
            event[1] = float(tokens)

    def block(self, seconds: float) -> None:
        """Шлюз всё-таки отбил запрос: не слать следующий до сброса лимита."""
        if seconds <= 0:
            return
        with self._lock:
            self._blocked_until = max(self._blocked_until, self._clock() + seconds)


_budget = MinuteBudget()


def budget() -> MinuteBudget:
    """Окно процесса. Отдельной функцией — чтобы тесты видели подмену."""
    return _budget


def reset(window: MinuteBudget | None = None) -> None:
    """Забыть накопленное окно. Нужно тестам: бюджет живёт на весь процесс."""
    global _budget
    _budget = window or MinuteBudget()


def retry_after_headers_s(headers: Any, *, now: float | None = None) -> float | None:
    """Пауза из HTTP-заголовков; None означает, что сервер её не указал."""
    getter = getattr(headers, "get", None)
    if getter is None:
        return None

    for name, divisor in (("retry-after-ms", 1000.0), ("retry-after", 1.0)):
        raw = getter(name)
        if raw is None:
            raw = getter(name.title())
        try:
            seconds = float(raw) / divisor
        except (TypeError, ValueError, OverflowError):
            if name != "retry-after" or not isinstance(raw, str):
                continue
            try:
                moment = parsedate_to_datetime(raw)
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=UTC)
                seconds = moment.timestamp() - (time.time() if now is None else now)
            except (TypeError, ValueError, OverflowError, OSError):
                continue
        if math.isfinite(seconds):
            return max(seconds, 0.0)
    return None


def retry_after_s(error: BaseException) -> float:
    """
    Сколько шлюз просит подождать после 429, в секундах. 0 — это не 429.

    Класс исключения не проверяется по имени провайдера: у клиентов OpenAI,
    DeepSeek и Anthropic отказ выглядит одинаково — `status_code` и ответ с
    заголовками, — а импортировать сюда чужой пакет ради isinstance значило бы
    требовать его от тех, кто этим провайдером не пользуется.
    """
    response = getattr(error, "response", None)
    status = getattr(error, "status_code", None) or getattr(response, "status_code", None)
    if status != 429:
        return 0.0
    asked = retry_after_headers_s(getattr(response, "headers", None))
    return WINDOW_S if asked is None else asked


def estimate_tokens(
    batches: list[list[Any]], *, invocation_params: dict | None = None
) -> float:
    """Вход запроса в токенах плюс резерв на ответ: шлюз считает оба."""
    prompt = sum(
        count_tokens_approximately(batch, chars_per_token=CHARS_PER_TOKEN) for batch in batches
    )
    params = invocation_params or {}
    for name in ("tools", "functions"):
        if params.get(name):
            schema = json.dumps(params[name], ensure_ascii=False, default=str)
            prompt += math.ceil(len(schema) / CHARS_PER_TOKEN) * len(batches)
    reserve = next(
        (
            params[name]
            for name in ("max_completion_tokens", "max_output_tokens", "max_tokens")
            if isinstance(params.get(name), int) and params[name] > 0
        ),
        None,
    )
    if reserve is None:
        reserve = cfg.env_int("LLM_MAX_TOKENS", 0, minimum=0) or DEFAULT_COMPLETION_RESERVE
    return prompt + reserve


def usage_tokens(response: LLMResult) -> float | None:
    """Фактический расход из ответа, если провайдер его прислал."""
    output = response.llm_output or {}
    usage = output.get("token_usage") or output.get("usage") or {}
    total = usage.get("total_tokens") if isinstance(usage, dict) else None
    if total:
        return float(total)
    for generations in response.generations:
        for generation in generations:
            message = getattr(generation, "message", None)
            counted = (getattr(message, "usage_metadata", None) or {}).get("total_tokens")
            if counted:
                return float(counted)
    return None


class Pacer(BaseCallbackHandler):
    """
    Ритм как callback модели, а не как обёртка над каждым вызовом.

    Вызовов модели в проекте с десяток мест — узлы ролей, текстовый протокол
    инструментов, расследование НТ, точечное обновление документа, — и обойти
    ритм означало бы просто позвать модель напрямую, мимо обёртки. Callback
    вешается на клиента в `providers.build_llm` один раз и срабатывает на
    каждом запросе, включая привязанные инструменты: `bind_tools` возвращает
    обёртку над тем же клиентом.

    `raise_error = True` здесь обязателен: без него langchain проглатывает
    исключение обработчика и пишет его в лог. Отказ «запрос больше бюджета»
    должен доходить до оператора, а не теряться в предупреждении.
    """

    raise_error = True

    def __init__(self) -> None:
        self._pending: dict[UUID, list[float]] = {}
        self._lock = Lock()

    def on_chat_model_start(
        self, serialized: dict, messages: list[list[Any]], *, run_id: UUID, **kwargs: Any
    ) -> None:
        requests = cfg.llm_requests_per_minute()
        limit = cfg.llm_tokens_per_minute()
        if not requests and not limit:
            return
        event = budget().reserve(
            estimate_tokens(messages, invocation_params=kwargs.get("invocation_params")),
            requests=requests,
            budget=limit,
        )
        with self._lock:
            self._pending[run_id] = event

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        with self._lock:
            event = self._pending.pop(run_id, None)
        counted = usage_tokens(response)
        if event is not None and counted is not None:
            budget().settle(event, counted)

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        with self._lock:
            self._pending.pop(run_id, None)
        asked = retry_after_s(error)
        if asked:
            logger.warning("Шлюз ограничил частоту запросов к модели; пауза %.0f с", asked)
            budget().block(asked)
