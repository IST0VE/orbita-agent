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
    """
    Стоимость счётчиков по тарифу, который действует прямо сейчас.

    Годится для одного вызова и для отчёта «сколько стоил бы этот расход».
    Для накопленного расхода треда так считать нельзя: смена `LLM_MODEL`
    переоценила бы им всю историю задним числом — см. `charge` и `spent_usd`.
    """
    return sum(usage.get(key, 0) / 1_000_000 * price for key, price in cfg.price_per_mtok().items())


def price_state() -> dict:
    """
    Тариф в виде, который уезжает в состояние треда и в интерфейс.

    Неизвестная цена обязана выглядеть неизвестной. Раньше она выглядела
    нулём — и ноль был неотличим от бесплатного вызова.
    """
    price = cfg.price_info()
    return {
        "known": price.known,
        "complete": price.complete,
        "source": price.source,
        "version": price.version,
        "missing": list(price.missing),
        "model": cfg.model_name(),
        "provider": cfg.llm_provider(),
    }


def charge(usage: dict, *, state: dict | None = None) -> dict:
    """
    Деньги за один вызов по тарифу, действующему в момент вызова.

    Возвращается приращением: состояние складывает его редьюсером. Считать
    накопленный расход умножением итоговых счётчиков на текущую цену нельзя —
    смена модели или прайса переоценивает все прошлые вызовы разом, и тред,
    который вчера стоил доллар, сегодня стоит десять, не сделав ни одного
    нового запроса.

    Вызов по неизвестному тарифу считается неоценённым, а не бесплатным:
    ноль в деньгах и ноль в «мы не знаем» — разные вещи, и ворота бюджета
    обязаны их различать.
    """
    price = cfg.price_info()
    if not price.complete:
        money = {"usd": 0.0, "naive_usd": 0.0, "priced_calls": 0, "unpriced_calls": 1}
    else:
        money = {"usd": estimate_cost(usage), "naive_usd": naive_cost(usage),
                 "priced_calls": 1, "unpriced_calls": 0}
    # Старый checkpoint содержит только токены. Фиксируем прежнюю оценку один
    # раз, вместе с первым новым начислением. Нулевой словарь reducer тоже
    # означает отсутствие истории; priced/unpriced_calls отличают бесплатный
    # уже учтённый вызов от ещё не мигрированной истории.
    old = (state or {}).get("usage") or {}
    if old and not any(((state or {}).get("spend") or {}).values()):
        calls = int(old.get("calls", 0))
        if price.complete:
            money["usd"] += estimate_cost(old)
            money["naive_usd"] += naive_cost(old)
            money["estimated_calls"] = calls
        else:
            money["unpriced_calls"] += calls
    return money


def spent_usd(state: dict) -> float:
    """
    Потрачено в треде. Накопленное, если оно есть; иначе оценка по счётчикам.

    Вторая ветка — для чекпоинтов, сделанных до накопления: там, кроме
    счётчиков, ничего нет, и оценка текущим тарифом остаётся единственным
    доступным ответом.
    """
    spend = state.get("spend") or {}
    if any(spend.values()):
        return float(spend.get("usd", 0.0))
    return estimate_cost(state.get("usage") or {})


def unpriced_calls(state: dict) -> int:
    """Сколько вызовов треда прошло по неизвестному тарифу."""
    return int((state.get("spend") or {}).get("unpriced_calls", 0))


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


def cost_summary(usage: dict, spend: dict | None = None) -> dict:
    """
    Накопленные счётчики треда, переведённые в деньги по текущему тарифу.

    Зачем это в состоянии, если счётчики там уже есть: тариф лежит в `.env` и
    остаётся на сервере. Отдать браузеру цены — значит завести им второе место
    хранения, которое рано или поздно разъедется с тем, по которому считает
    граф. Поэтому перевод делается здесь, а интерфейсы показывают готовое.
    """
    spend = spend if spend and any(spend.values()) else {}
    return {
        # Накопленное по вызовам, если оно есть: тариф мог меняться по дороге,
        # и пересчёт истории текущей ценой — это другая история, не эта.
        "usd": float(spend.get("usd", 0.0)) if spend else estimate_cost(usage),
        "naive_usd": float(spend.get("naive_usd", 0.0)) if spend else naive_cost(usage),
        # Тариф: известен ли, откуда и какой версии. Без этого нулевая
        # стоимость в интерфейсе читается как «бесплатно».
        "price": price_state(),
        "unpriced_calls": int(spend.get("unpriced_calls", 0)),
        "estimated_calls": int(spend.get("estimated_calls", 0)),
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

