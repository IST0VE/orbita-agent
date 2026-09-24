"""
Ритм обращений к модели: очередь перед минутным лимитом шлюза.

Проверяется не «вызвался ли sleep», а три решения, ради которых модуль написан:
запрос ждёт места в окне ДО отправки; окно освобождается по одному ходу, а не
целой минутой; запрос, который не помещается в пустое окно, получает отказ с
причиной вместо бесконечного ожидания.

Часы и сон здесь поддельные: тест о том, сколько ждать, а не о том, как долго
идти тестам.
"""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import format_datetime
from threading import Event, Thread
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from agent import llm_pacing, providers


class Clock:
    """Часы, которые двигает сон: так видно, сколько прогон простоял."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def window(clock: Clock) -> llm_pacing.MinuteBudget:
    return llm_pacing.MinuteBudget(clock=clock, sleep=clock.sleep)


def test_no_limits_no_queue(window: llm_pacing.MinuteBudget, clock: Clock) -> None:
    """Лимиты не заданы — очереди нет: поведение прежнее, как до модуля."""
    for _ in range(50):
        window.reserve(10_000, requests=0, budget=0)
    assert clock.slept == []


def test_requests_wait_for_the_oldest_to_age_out(
    window: llm_pacing.MinuteBudget, clock: Clock
) -> None:
    """Третий запрос при лимите два ждёт ровно до выбывания первого."""
    window.reserve(10, requests=2, budget=0)
    window.reserve(10, requests=2, budget=0)
    assert clock.slept == []

    window.reserve(10, requests=2, budget=0)
    assert clock.now == pytest.approx(1000.0 + llm_pacing.WINDOW_S)


def test_tokens_free_the_window_one_turn_at_a_time(
    window: llm_pacing.MinuteBudget, clock: Clock
) -> None:
    """
    Ждём выбывания стольких ходов, скольких хватает, а не минуты целиком.

    Ранние ходы роли мелкие: место под следующий запрос освобождают два первых,
    и ждать выбывания третьего — это лишние секунды простоя на каждом ходе.
    """
    window.reserve(20, requests=0, budget=100)
    clock.now += 10
    window.reserve(20, requests=0, budget=100)
    clock.now += 10
    window.reserve(50, requests=0, budget=100)

    # В окне 90 из 100. Запрос на 40 помещается после выбывания двух первых
    # ходов (они в 1000 и 1010), то есть на отметке 1010 + 60.
    window.reserve(40, requests=0, budget=100)
    assert clock.now == pytest.approx(1010.0 + llm_pacing.WINDOW_S)


def test_request_larger_than_the_whole_budget_is_refused(
    window: llm_pacing.MinuteBudget,
) -> None:
    """Пауза не уменьшает историю: такому запросу отказывают сразу и по делу."""
    with pytest.raises(llm_pacing.RateLimitTooSmall) as failure:
        window.reserve(5000, requests=0, budget=4000)

    message = str(failure.value)
    assert "LLM_TOKENS_PER_MINUTE" in message
    assert "LLM_MAX_HISTORY_TOKENS" in message


def test_oversized_request_does_not_wait_for_other_limits(
    window: llm_pacing.MinuteBudget, clock: Clock
) -> None:
    window.reserve(10, requests=1, budget=100)
    window.block(120)

    with pytest.raises(llm_pacing.RateLimitTooSmall):
        window.reserve(101, requests=1, budget=100)

    assert clock.slept == []


def test_settle_replaces_the_estimate_with_the_fact(
    window: llm_pacing.MinuteBudget, clock: Clock
) -> None:
    """Оценка до запроса грубая; после ответа окно считает по usage."""
    event = window.reserve(90, requests=0, budget=100)
    window.settle(event, 10)

    # Будь в окне прежние 90, запрос на 80 ждал бы; по факту там 10.
    window.reserve(80, requests=0, budget=100)
    assert clock.slept == []


def test_waiting_caller_allows_usage_and_backoff_updates(clock: Clock) -> None:
    """Другой ответ обновляет окно во время сна, и ожидающий видит новую паузу."""
    sleeping, wake, updated = Event(), Event(), Event()
    results: list[list[float]] = []

    def sleep(seconds: float) -> None:
        sleeping.set()
        assert wake.wait(2), "test did not release the waiting request"
        clock.sleep(seconds)

    window = llm_pacing.MinuteBudget(clock=clock, sleep=sleep)
    event = window.reserve(90, requests=0, budget=100)
    waiter = Thread(
        target=lambda: results.append(window.reserve(20, requests=0, budget=100)),
        daemon=True,
    )

    def update() -> None:
        window.settle(event, 10)
        window.block(120)
        updated.set()

    updater = Thread(target=update, daemon=True)
    waiter.start()
    try:
        assert sleeping.wait(1), "request never reached the token limit"
        updater.start()
        updated_during_sleep = updated.wait(1)
    finally:
        wake.set()
        waiter.join(2)
        if updater.ident is not None:
            updater.join(2)

    assert updated_during_sleep, "a waiting request prevented another response from settling"
    assert not waiter.is_alive()
    assert len(results) == 1
    assert results[0][0] == pytest.approx(1120.0)


def test_block_holds_the_next_request(window: llm_pacing.MinuteBudget, clock: Clock) -> None:
    """Шлюз всё-таки отбил запрос — следующий не уходит до сброса лимита."""
    window.block(30)
    window.reserve(1, requests=0, budget=0)
    assert clock.now == pytest.approx(1030.0)


def response_429(retry_after: str | None) -> Exception:
    """Отказ шлюза в том виде, в каком его отдаёт клиент провайдера."""
    headers = {} if retry_after is None else {"retry-after": retry_after}
    error = RuntimeError("rate limit")
    error.response = SimpleNamespace(status_code=429, headers=headers)  # type: ignore[attr-defined]
    return error


def test_retry_after_is_read_without_shortening_the_server_pause() -> None:
    assert llm_pacing.retry_after_s(response_429("42")) == pytest.approx(42.0)
    assert llm_pacing.retry_after_s(response_429("600")) == pytest.approx(600.0)
    # Без указания шлюза ждём окно целиком.
    assert llm_pacing.retry_after_s(response_429(None)) == pytest.approx(llm_pacing.WINDOW_S)


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "nonsense"])
def test_invalid_retry_after_uses_a_finite_fallback(value: str) -> None:
    assert llm_pacing.retry_after_s(response_429(value)) == llm_pacing.WINDOW_S


def test_retry_after_accepts_http_dates_and_milliseconds(monkeypatch: pytest.MonkeyPatch) -> None:
    moment = datetime(2026, 9, 24, 13, 0, tzinfo=UTC)
    monkeypatch.setattr(llm_pacing.time, "time", lambda: moment.timestamp() - 180)
    assert llm_pacing.retry_after_s(response_429(format_datetime(moment, usegmt=True))) == 180
    assert llm_pacing.retry_after_headers_s(
        {"retry-after-ms": "1250", "retry-after": "60"}
    ) == 1.25
    assert llm_pacing.retry_after_headers_s(
        {"retry-after-ms": "nan", "Retry-After": "90"}
    ) == 90
    assert llm_pacing.retry_after_headers_s(
        {"Retry-After": format_datetime(moment, usegmt=True)}, now=moment.timestamp() + 10
    ) == 0
    assert llm_pacing.retry_after_headers_s({"retry-after": "-5"}) == 0
    assert llm_pacing.retry_after_headers_s({}) is None


def test_other_failures_are_not_rate_limits() -> None:
    """Таймаут и 500 — не лимит частоты: придерживать следующий запрос не за что."""
    error = RuntimeError("boom")
    error.response = SimpleNamespace(status_code=500, headers={})  # type: ignore[attr-defined]
    assert llm_pacing.retry_after_s(error) == 0.0
    assert llm_pacing.retry_after_s(RuntimeError("no response at all")) == 0.0


def test_estimate_counts_the_answer_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """Шлюз тарифицирует total_tokens, поэтому ответ резервируется вместе с входом."""
    monkeypatch.setenv("LLM_MAX_TOKENS", "500")
    small = llm_pacing.estimate_tokens([[HumanMessage("привет")]])
    assert small >= 500

    monkeypatch.delenv("LLM_MAX_TOKENS", raising=False)
    assert llm_pacing.estimate_tokens([[HumanMessage("привет")]]) >= (
        llm_pacing.DEFAULT_COMPLETION_RESERVE
    )


@pytest.mark.parametrize("key", ["max_tokens", "max_completion_tokens", "max_output_tokens"])
def test_estimate_uses_the_effective_output_limit(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    monkeypatch.setenv("LLM_MAX_TOKENS", "500")
    messages = [[HumanMessage("привет")]]
    before = llm_pacing.estimate_tokens(messages)
    after = llm_pacing.estimate_tokens(messages, invocation_params={key: 5000})
    assert after - before == 4500


@pytest.mark.parametrize("key", ["tools", "functions"])
def test_estimate_accounts_for_bound_tool_schemas(key: str) -> None:
    messages = [[HumanMessage("привет")]]
    tools = [{"type": "function", "function": {"name": "lookup", "description": "я" * 9000}}]
    before = llm_pacing.estimate_tokens(messages)
    after = llm_pacing.estimate_tokens(messages, invocation_params={key: tools})
    assert after - before >= 3000


def test_pacer_checks_bound_parameters_before_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_TOKENS_PER_MINUTE", "2000")
    pacer = llm_pacing.Pacer()
    with pytest.raises(llm_pacing.RateLimitTooSmall):
        pacer.on_chat_model_start(
            {}, [[HumanMessage("маленький запрос")]], run_id=uuid4(),
            invocation_params={"max_completion_tokens": 3000},
        )


def test_pacer_queues_and_settles(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> None:
    """Обработчик модели: занять место перед запросом, поправить его по ответу."""
    monkeypatch.setenv("LLM_REQUESTS_PER_MINUTE", "2")
    monkeypatch.setenv("LLM_TOKENS_PER_MINUTE", "0")
    llm_pacing.reset(llm_pacing.MinuteBudget(clock=clock, sleep=clock.sleep))
    pacer = llm_pacing.Pacer()

    for _ in range(2):
        run = uuid4()
        pacer.on_chat_model_start({}, [[HumanMessage("задача")]], run_id=run)
        pacer.on_llm_end(
            LLMResult(
                generations=[[ChatGeneration(message=AIMessage("готово"))]],
                llm_output={"token_usage": {"total_tokens": 12}},
            ),
            run_id=run,
        )
    assert clock.slept == []

    pacer.on_chat_model_start({}, [[HumanMessage("задача")]], run_id=uuid4())
    assert clock.now == pytest.approx(1000.0 + llm_pacing.WINDOW_S)

    llm_pacing.reset()


def test_pacer_is_idle_without_limits(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> None:
    """Без лимитов обработчик не трогает окно вовсе: ни пауз, ни учёта."""
    monkeypatch.delenv("LLM_REQUESTS_PER_MINUTE", raising=False)
    monkeypatch.delenv("LLM_TOKENS_PER_MINUTE", raising=False)
    llm_pacing.reset(llm_pacing.MinuteBudget(clock=clock, sleep=clock.sleep))
    pacer = llm_pacing.Pacer()

    for _ in range(100):
        pacer.on_chat_model_start({}, [[HumanMessage("x" * 5000)]], run_id=uuid4())
    assert clock.slept == []

    llm_pacing.reset()


def test_error_from_the_gateway_pauses_the_queue(
    monkeypatch: pytest.MonkeyPatch, clock: Clock
) -> None:
    """429 мимо очереди (её обходят повторы клиента) всё равно придерживает следующий ход."""
    monkeypatch.setenv("LLM_REQUESTS_PER_MINUTE", "10")
    llm_pacing.reset(llm_pacing.MinuteBudget(clock=clock, sleep=clock.sleep))
    pacer = llm_pacing.Pacer()

    pacer.on_llm_error(response_429("30"), run_id=uuid4())
    pacer.on_chat_model_start({}, [[HumanMessage("задача")]], run_id=uuid4())
    assert clock.now == pytest.approx(1030.0)

    llm_pacing.reset()


def test_client_is_built_with_the_pacer(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Ритм висит на клиенте, а не на вызове.

    Звать модель в проекте умеют больше десяти мест; обёртка над вызовом
    означала бы, что новое место о ней просто не узнает.
    """
    captured: dict = {}

    def factory(model: str, kwargs: dict):
        captured["model"] = model
        captured["kwargs"] = kwargs
        return SimpleNamespace(model=model)

    monkeypatch.setitem(providers._FACTORIES, "openai", factory)
    monkeypatch.setattr(providers, "_CLIENTS", {})
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    providers.build_llm()
    assert [type(item) for item in captured["kwargs"]["callbacks"]] == [llm_pacing.Pacer]
