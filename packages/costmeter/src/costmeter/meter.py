"""
`CostMeter` — счётчик денег как callback handler.

Ловит `on_llm_end` и копит статистику, ничего не зная ни про ноды, ни про
состояние графа. Поэтому прикручивается к любой цепочке или графу LangChain,
а не только к тому, ради которого написан:

    from costmeter import CostMeter

    meter = CostMeter(provider="deepseek", model="deepseek-v4-flash")
    result = app.invoke(payload, config={"callbacks": [meter]})
    print(meter.report())
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from costmeter.prices import Prices, for_model
from costmeter.usage import Usage, normalize


@dataclass
class Call:
    """Один вызов модели: что израсходовано и во сколько это обошлось."""

    at: str
    provider: str
    model: str
    usage: Usage
    cost_usd: float
    naive_cost_usd: float
    thread_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "provider": self.provider,
            "model": self.model,
            **self.usage.as_dict(),
            "hit_rate": round(self.usage.hit_rate, 1),
            "cost_usd": round(self.cost_usd, 8),
            "naive_cost_usd": round(self.naive_cost_usd, 8),
            "thread_id": self.thread_id,
        }


@dataclass
class CostMeter(BaseCallbackHandler):
    """
    Накопитель расхода по вызовам модели.

    provider и model нужны, чтобы найти тариф. Модель handler умеет достать
    и сам — из ответа провайдера; явное значение важнее, потому что имя
    в ответе бывает длиннее того, что лежит в таблице цен.

    log_path — путь до JSONL: одна строка на вызов модели. Файл дописывается,
    поэтому несколько прогонов ложатся в один лог и разбираются по thread_id.

    budget_usd — потолок расхода. Сам handler ничего не останавливает: он
    только знает, что потолок пройден. Решение принимает тот, кто его завёл.
    """

    provider: str = "deepseek"
    model: str = ""
    prices: Prices | None = None
    log_path: str | Path | None = None
    budget_usd: float | None = None

    calls: list[Call] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _threads: dict[Any, str] = field(default_factory=dict, repr=False)

    # ----------------------------------------------------------------------
    # Callback API
    # ----------------------------------------------------------------------
    def on_chat_model_start(self, serialized, messages, **kwargs: Any) -> None:
        self._remember_thread(kwargs)

    def on_llm_start(self, serialized, prompts, **kwargs: Any) -> None:
        self._remember_thread(kwargs)

    def on_llm_end(self, response, **kwargs: Any) -> None:
        raw, normalized, model = _dig_out(response)
        usage = normalize(raw, normalized, self.provider)
        prices = self.prices_for(model)

        with self._lock:
            thread_id = self._threads.pop(kwargs.get("run_id"), None)
        call = Call(
            at=datetime.now(UTC).isoformat(timespec="seconds"),
            provider=self.provider,
            model=self.model or model or "",
            usage=usage,
            cost_usd=prices.cost(usage),
            naive_cost_usd=prices.naive_cost(usage),
            thread_id=thread_id,
        )
        with self._lock:
            self.calls.append(call)
            self._write(call)

    def _remember_thread(self, kwargs: dict) -> None:
        metadata = kwargs.get("metadata") or {}
        thread_id = metadata.get("thread_id") or metadata.get("langgraph_thread_id")
        if thread_id is not None:
            with self._lock:
                self._threads[kwargs.get("run_id")] = str(thread_id)

    # ----------------------------------------------------------------------
    # Итоги
    # ----------------------------------------------------------------------
    def prices_for(self, model: str | None = None) -> Prices:
        """Тариф: заданный явно или найденный по паре (провайдер, модель)."""
        if self.prices is not None:
            return self.prices
        return for_model(self.provider, self.model or model or "")

    @property
    def usage(self) -> Usage:
        """Суммарный расход по всем перехваченным вызовам."""
        total = Usage()
        for call in self.calls:
            total = total + call.usage
        return total

    @property
    def cost(self) -> float:
        return sum(call.cost_usd for call in self.calls)

    @property
    def naive_cost(self) -> float:
        """Во сколько обошёлся бы тот же расход без кеша."""
        return sum(call.naive_cost_usd for call in self.calls)

    @property
    def saved(self) -> float:
        return self.naive_cost - self.cost

    @property
    def over_budget(self) -> bool:
        return self.budget_usd is not None and self.cost >= self.budget_usd

    def reset(self) -> None:
        with self._lock:
            self.calls.clear()
        self._threads.clear()

    def as_dict(self) -> dict[str, Any]:
        usage = self.usage
        return {
            "provider": self.provider,
            "model": self.model,
            **usage.as_dict(),
            "hit_rate": round(usage.hit_rate, 1),
            "cost_usd": round(self.cost, 8),
            "naive_cost_usd": round(self.naive_cost, 8),
            "saved_usd": round(self.saved, 8),
            "prices": self.prices_for().source,
        }

    def report(self) -> str:
        """Человекочитаемый итог — то же, что печатает демо."""
        usage = self.usage
        prices = self.prices_for()
        written = f", записано в кеш {usage.cache_write}" if usage.cache_write else ""
        lines = [
            f"вызовов LLM: {usage.calls} | "
            f"вход {usage.input} ток. (из кеша {usage.cache_hit}, "
            f"пересчитано {usage.cache_miss}{written}) | "
            f"выход {usage.output} ток.",
            f"  cache hit rate: {usage.hit_rate:5.1f}%  |  "
            f"стоимость ${self.cost:.6f}  (без кеша было бы ${self.naive_cost:.6f})",
            f"  тариф: {prices.source}",
        ]
        if not prices.known:
            lines.append("  ВНИМАНИЕ: тариф неизвестен, стоимость посчитана как ноль")
        if self.budget_usd is not None:
            state = "превышен" if self.over_budget else "в пределах"
            lines.append(f"  бюджет ${self.budget_usd:.6f}: {state}")
        return "\n".join(lines)

    # ----------------------------------------------------------------------
    # Лог
    # ----------------------------------------------------------------------
    def _write(self, call: Call) -> None:
        if not self.log_path:
            return
        path = Path(self.log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(call.as_dict(), ensure_ascii=False) + "\n")


def _dig_out(response: Any) -> tuple[dict, dict, str | None]:
    """
    Вытащить из ответа сырой usage, нормализованный usage и имя модели.

    Провайдеры кладут статистику то в `llm_output` целиком, то в
    `response_metadata` каждого сообщения. Проверяются оба места.
    """
    llm_output = getattr(response, "llm_output", None) or {}
    raw = llm_output.get("token_usage") or llm_output.get("usage") or {}
    model = llm_output.get("model_name") or llm_output.get("model")
    normalized: dict = {}

    for batch in getattr(response, "generations", None) or []:
        for generation in batch:
            message = getattr(generation, "message", None)
            if message is None:
                continue
            meta = getattr(message, "response_metadata", None) or {}
            raw = raw or meta.get("token_usage") or meta.get("usage") or {}
            model = model or meta.get("model_name") or meta.get("model")
            normalized = normalized or (getattr(message, "usage_metadata", None) or {})

    return raw, normalized, model
