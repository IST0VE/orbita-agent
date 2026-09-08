"""
Учёт кеша и денег: сколько стоил вызов и сколько стоил бы без кеша.

Ради этого модуля написан весь проект, поэтому он отдельным файлом и стоит
ниже графа: цифры нужны и нодам, и документу, и воротам бюджета, а сам он
не знает ни про роли, ни про состояние — только про словарь счётчиков.

Разбор ответа провайдера живёт не здесь, а в пакете `costmeter`: у DeepSeek
свои поля, у OpenAI свои, у Anthropic третьи, и место, где эта разница
описана, должно быть одно на проект. Здесь остаётся то, что делают с уже
разобранными числами: складывают, переводят в деньги и превращают в строку
для человека.

Наивная стоимость — это счёт, который пришёл бы, не будь кеша: те же токены
по цене промаха. Разница между ней и настоящей — единственное честное
измерение того, работает ли префикс.
"""

from __future__ import annotations

from typing import Any

import costmeter
from agent import config as cfg


def extract_usage(message: Any) -> dict:
    """
    Счётчики из ответа модели — в словарь для редьюсера состояния.

    Сам разбор живёт в пакете `costmeter`: у DeepSeek свои поля
    (`prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`), у Anthropic —
    `cache_read_input_tokens` и `cache_creation_input_tokens`, у OpenAI —
    `prompt_tokens_details.cached_tokens`, и всё это дополняется
    нормализованными полями LangChain. Здесь остался только переходник:
    достать оба словаря из сообщения и отдать результат в том виде, в каком
    его копит состояние графа.

    Второй путь учёта — `CostMeter` как callback handler; он ловит те же
    ответы и обязан считать то же самое. Это проверяется тестом.
    """
    meta = getattr(message, "response_metadata", None) or {}
    raw = meta.get("token_usage") or meta.get("usage") or {}
    normalized = getattr(message, "usage_metadata", None) or {}
    return costmeter.normalize(raw, normalized, cfg.llm_provider()).as_dict()


def estimate_cost(usage: dict) -> float:
    """Стоимость в долларах по накопленным счётчикам и текущему тарифу."""
    return sum(usage.get(key, 0) / 1_000_000 * price for key, price in cfg.price_per_mtok().items())


def input_tokens(usage: dict) -> int:
    """Весь вход: отданное из кеша, посчитанное заново и записанное в кеш."""
    return usage.get("cache_hit", 0) + usage.get("cache_miss", 0) + usage.get("cache_write", 0)


def hit_rate(usage: dict) -> float:
    inp = input_tokens(usage)
    return usage.get("cache_hit", 0) / inp * 100 if inp else 0.0


def naive_cost(usage: dict) -> float:
    """Во что обошёлся бы тот же тред без кеша: весь вход по цене пересчёта."""
    price = cfg.price_per_mtok()
    return (
        input_tokens(usage) / 1_000_000 * price["cache_miss"]
        + usage.get("output", 0) / 1_000_000 * price["output"]
    )


def cost_summary(usage: dict) -> dict:
    """
    Накопленные счётчики треда, переведённые в деньги по текущему тарифу.

    Зачем это в состоянии, если счётчики там уже есть: тариф лежит в `.env` и
    остаётся на сервере. Отдать браузеру цены — значит завести им второе место
    хранения, которое рано или поздно разъедется с тем, по которому считает
    граф. Поэтому перевод делается здесь, а интерфейсы показывают готовое.
    """
    return {
        "usd": estimate_cost(usage),
        "naive_usd": naive_cost(usage),
        "hit_rate": hit_rate(usage),
        # Лимит треда: по нему интерфейс рисует, сколько бюджета уже съедено.
        # 0 означает «без лимита» — ровно как в BUDGET_USD_PER_THREAD.
        "limit_usd": cfg.budget_usd_per_thread(),
        "input": input_tokens(usage),
        "output": usage.get("output", 0),
        "cache_hit": usage.get("cache_hit", 0),
        "calls": usage.get("calls", 0),
    }


def format_usage(usage: dict) -> str:
    inp = input_tokens(usage)
    naive = naive_cost(usage)
    real = estimate_cost(usage)

    # Строка про запись в кеш появляется, только если счётчик ненулевой:
    # на DeepSeek его не существует, и вывод не должен обрастать нулями.
    written = usage.get("cache_write", 0)
    write_part = f", записано в кеш {written}" if written else ""

    return (
        f"вызовов LLM: {usage.get('calls', 0)} | "
        f"вход {inp} ток. (из кеша {usage.get('cache_hit', 0)}, "
        f"пересчитано {usage.get('cache_miss', 0)}{write_part}) | "
        f"выход {usage.get('output', 0)} ток.\n"
        f"  cache hit rate: {hit_rate(usage):5.1f}%  |  "
        f"стоимость ${real:.6f}  (без кеша было бы ${naive:.6f})"
    )


def format_publication(publication: dict | None) -> str:
    """
    Итог этапа публикации для консоли.

    Страниц у конвейера столько, сколько состоялось этапов, поэтому итог из
    одной строки превратился в строку плюс список. Свести его обратно к одной
    строке — значит спрятать, какая именно из пяти страниц не уехала.
    """
    if not publication:
        return "публикация: этап не выполнялся"

    status = publication.get("status")
    pages = publication.get("pages") or []

    if not pages:
        return f"публикация: {status} — {publication.get('reason', 'без причины')}"

    head = {
        "created": f"публикация: страниц создано {len(pages)}",
        "updated": f"публикация: страниц обновлено {len(pages)}",
        "partial": "публикация: уехало не всё",
        "failed": "публикация: не удалась",
    }.get(status, f"публикация: {status}")

    lines = [head]
    for page in pages:
        if page.get("status") == "failed":
            lines.append(f"  ✗ {page.get('title')} — {page.get('reason', 'без причины')}")
        else:
            # Версия есть у страницы в Confluence и не бывает у файла на диске:
            # печатать «vNone» не за чем.
            version = page.get("version")
            stamp = f" (v{version})" if version else ""
            lines.append(f"  ✓ {page.get('title')}{stamp} — {page.get('url')}")
    return "\n".join(lines)

