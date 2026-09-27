"""Bounded retries of a single model request, outside the provider SDK.

Every attempt enters LangChain again, so the pacing callback accounts for it.
Graph nodes and tools are never replayed here. Do not add SDK retries on top:
that would multiply attempts and hide HTTP requests from the minute budget.
"""

from __future__ import annotations

import logging
import random
from time import sleep as _sleep
from typing import Any

import httpx
import httpx2
import openai

from agent import config as cfg
from agent import llm_pacing

logger = logging.getLogger(__name__)

#: Чем провайдеры помечают ответ, оборванный на потолке длины.
_CUT_REASONS = frozenset({"length", "max_tokens"})


class ResponseTruncated(RuntimeError):
    """
    Ответ модели упёрся в LLM_MAX_TOKENS: документ неполный, принимать его нельзя.

    Оплачен он при этом целиком, поэтому исключение несёт сам ответ: узел,
    который его поймал, обязан записать расход в тред до остановки
    (`nodes.truncation_charge`). Иначе ворота бюджета не видят оборванных
    вызовов и пускают следующий.
    """

    def __init__(self, message: str, response: Any = None) -> None:
        super().__init__(message)
        self.response = response
        # Обновление состояния с расходом узла: `usage`, `spend`, `cost` и то,
        # что узел успел принять до обрыва. Заполняет узел, у которого до
        # оборванного вызова были свои (переспрос роли), — см. `make_role_node`.
        self.update: dict | None = None


def _truncated(result: Any) -> bool:
    """
    Ответ оборван на потолке длины.

    OpenAI-совместимые API пишут `finish_reason: length`, Anthropic —
    `stop_reason: max_tokens`; LangChain кладёт оба в `response_metadata`.
    """
    meta = getattr(result, "response_metadata", None) or {}
    return meta.get("finish_reason") in _CUT_REASONS or meta.get("stop_reason") in _CUT_REASONS


def _truncation_message(result: Any) -> str:
    """Диагностика по счётчикам; текст задачи, ответа и рассуждений не раскрывается."""
    meta = getattr(result, "response_metadata", None) or {}
    usage = getattr(result, "usage_metadata", None) or {}
    raw = meta.get("token_usage") or meta.get("usage") or {}
    output = usage.get("output_tokens", raw.get("completion_tokens", raw.get("output_tokens")))
    reasoning = (usage.get("output_token_details") or {}).get("reasoning")
    if reasoning is None:
        reasoning = (raw.get("completion_tokens_details") or {}).get("reasoning_tokens")
    cap = cfg.llm_max_tokens()
    setting = "LLM_MAX_TOKENS=0" if cfg.env_opt("LLM_MAX_TOKENS") else "LLM_MAX_TOKENS не задан"
    limit = f"LLM_MAX_TOKENS={cap}" if cap is not None else f"лимит модели/шлюза ({setting})"
    counts = []
    if isinstance(output, int):
        counts.append(f"выходных токенов: {output}")
    if isinstance(reasoning, int):
        counts.append(f"из них reasoning: {reasoning}")
    details = f" ({'; '.join(counts)})" if counts else ""
    message = f"Генерация остановлена по лимиту: {limit}{details}. "
    if isinstance(reasoning, int) and reasoning > 0:
        message += (
            "В лимит входят рассуждения модели, поэтому он может закончиться до готового ответа. "
            "Настройте thinking/reasoning на шлюзе или через LLM_EXTRA_BODY, "
            "либо увеличьте LLM_MAX_TOKENS. "
        )
    else:
        message += (
            "Ответ не завершён. Проверьте лимит вывода и контекста модели на шлюзе; "
            "при необходимости увеличьте LLM_MAX_TOKENS. "
        )
    return message + (
        "При увеличении лимита учитывайте LLM_TIMEOUT_S. "
        "Прогон остановлен; неполный ответ не передан дальше. "
        "Остановка по лимиту сама по себе не доказывает зацикливание."
    )


def _retryable(error: Exception) -> bool:
    connection_errors: tuple[type[Exception], ...] = (
        openai.APIConnectionError,
        # Once a streamed response starts, SDKs may propagate the transport
        # error directly instead of wrapping it in APIConnectionError.
        httpx.NetworkError, httpx.TimeoutException, httpx.RemoteProtocolError,
        httpx2.NetworkError, httpx2.TimeoutException, httpx2.RemoteProtocolError,
    )
    status_errors: tuple[type[Exception], ...] = (openai.APIStatusError,)
    # Anthropic is an optional dependency; DeepSeek uses the OpenAI SDK.
    try:
        import anthropic
    except ImportError:
        pass
    else:
        connection_errors += (anthropic.APIConnectionError,)
        status_errors += (anthropic.APIStatusError,)
    if isinstance(error, connection_errors):
        return True
    if not isinstance(error, status_errors):
        return False
    if error.response.headers.get("x-should-retry") == "false":
        return False
    # A depleted paid quota also returns 429, but waiting cannot restore it.
    body = error.body
    if isinstance(body, dict):
        detail = body.get("error", body)
        if isinstance(detail, dict) and any(
            detail.get(key) in ("insufficient_quota", "billing_hard_limit_reached")
            for key in ("code", "type")
        ):
            return False
    return error.status_code in (408, 409, 429) or 500 <= error.status_code < 600


def _diagnostic(error: Exception) -> str:
    """Keep transport cause types, never URLs, headers, body or prompt text."""
    chain = []
    seen: set[int] = set()
    cause: BaseException | None = error
    while cause is not None and id(cause) not in seen and len(chain) < 8:
        seen.add(id(cause))
        label = type(cause).__name__
        number = getattr(cause, "errno", None)
        if isinstance(number, int):
            label += f"(errno={number})"
        chain.append(label)
        cause = cause.__cause__ or (
            None if cause.__suppress_context__ else cause.__context__
        )
    status = getattr(error, "status_code", None)
    prefix = f"HTTP {status}; " if isinstance(status, int) else ""
    return prefix + " <- ".join(chain)


def invoke(model: Any, messages: Any) -> Any:
    """Retry transient failures of this request up to LLM_MAX_RETRIES times."""
    retries = cfg.llm_max_retries()
    for attempt in range(retries + 1):
        try:
            result = model.invoke(messages)
        except Exception as error:
            if not _retryable(error):
                raise
            diagnostic = _diagnostic(error)
            if attempt == retries:
                logger.error(
                    "Модель недоступна после %d попыток: %s", attempt + 1, diagnostic
                )
                error.add_note(
                    f"Запрос к модели не выполнен после {attempt + 1} попыток "
                    f"(LLM_MAX_RETRIES={retries}). Причина: {diagnostic}. "
                    "Проверьте доступность API/шлюза модели и его журналы."
                )
                raise
            delay = min(60.0, 5.0 * 2 ** min(attempt, 4) + random.uniform(0.0, 1.0))
            response = getattr(error, "response", None)
            asked = llm_pacing.retry_after_headers_s(getattr(response, "headers", None))
            if getattr(error, "status_code", None) == 429:
                asked = llm_pacing.retry_after_s(error)
            if asked is not None:
                delay = max(delay, asked)
            logger.warning(
                "Временная ошибка модели (%s); повтор %d/%d через %.1f с",
                diagnostic, attempt + 1, retries, delay,
            )
            _sleep(delay)
        else:
            if attempt:
                logger.info("Соединение с моделью восстановлено на попытке %d", attempt + 1)
            if _truncated(result):
                # Повтор без изменения настроек снова может потратить весь лимит.
                message = _truncation_message(result)
                logger.warning("%s", message)
                raise ResponseTruncated(message, result)
            return result
