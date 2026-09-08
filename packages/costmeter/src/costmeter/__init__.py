"""
costmeter — учёт стоимости вызовов LLM с разбивкой по кешу.

Трассировку даёт LangSmith. Разбивку по cache hit и miss с деньгами — почти
никто, а именно она отвечает на вопрос «почему счёт такой».

    from costmeter import CostMeter

    meter = CostMeter(provider="deepseek", model="deepseek-v4-flash")
    result = app.invoke(payload, config={"callbacks": [meter]})
    print(meter.report())

Модуль ничего не знает про конкретный граф: он ловит `on_llm_end` и копит
четыре счётчика — вход из кеша, вход пересчитанный, запись в кеш и выход.
Подробности — в README пакета (packages/costmeter/README.md).
"""

from costmeter.meter import Call, CostMeter
from costmeter.prices import PriceError, Prices, captured_at, for_model
from costmeter.usage import COUNTERS, RULES, Rule, Usage, normalize

__all__ = [
    "COUNTERS",
    "RULES",
    "Call",
    "CostMeter",
    "PriceError",
    "Prices",
    "Rule",
    "Usage",
    "captured_at",
    "for_model",
    "normalize",
]
