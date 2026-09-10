"""
Поднятие чат-модели по имени провайдера из окружения.

Проект написан вокруг контекстного кеша DeepSeek, но привязки к нему в коде нет:
провайдер, модель, адрес API и ключ приходят из `.env` (`LLM_*`), а здесь лежит
только сопоставление имени провайдера с классом LangChain.

Импорты ленивые и лежат внутри фабрик: тому, кто работает на DeepSeek, пакет
langchain-anthropic не нужен, и ставить его ради строчки в словаре незачем.
Пакета нет — ошибка скажет, что именно поставить.

Имена аргументов у фабрик общие (`api_key`, `base_url`, `temperature`,
`max_tokens`, `timeout`, `max_retries`): LangChain принимает их как псевдонимы
во всех трёх классах, как бы ни назывались поля внутри. Поэтому маппинг здесь
не нужен — словарь из `config.llm_kwargs()` уходит в конструктор как есть.

Про кеш при смене провайдера. `extract_usage()` в graph.py сначала ищет
специфичные для DeepSeek поля prompt_cache_hit_tokens / prompt_cache_miss_tokens,
а если их нет — падает на нормализованное поле LangChain
(`input_token_details.cache_read`). Счётчики продолжат работать, но:
  * семантика hit/miss станет провайдерской;
  * цены в .env надо переписать под тариф нового провайдера, иначе отчёт
    будет считать чужие деньги.

Запись в кеш учитывается отдельной статьёй `cache_write` — она приезжает из
`input_token_details.cache_creation` и тарифицируется по
PRICE_CACHE_WRITE_PER_MTOK. У Anthropic она дороже обычного входа, у DeepSeek
её нет вовсе, и счётчик остаётся нулевым.
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from agent import config as cfg


def _deepseek(model: str, kwargs: dict) -> BaseChatModel:
    from langchain_deepseek import ChatDeepSeek

    return ChatDeepSeek(model=model, **kwargs)


def _openai(model: str, kwargs: dict) -> BaseChatModel:
    """
    Он же — любой OpenAI-совместимый эндпоинт: свой шлюз, vLLM, Ollama,
    сторонний провайдер с совместимым API. Отличие только в LLM_API_BASE.
    """
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=model, **kwargs)


def _anthropic(model: str, kwargs: dict) -> BaseChatModel:
    """
    Anthropic требует max_tokens на уровне API; ChatAnthropic подставляет свой
    дефолт, если LLM_MAX_TOKENS не задан. Здесь это единственный провайдер,
    где переменная влияет на длину ответа всегда, а не только как потолок.
    """
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError as exc:  # пакет не в зависимостях проекта — он опционален
        raise cfg.ConfigError(
            "LLM_PROVIDER=anthropic: нужен пакет langchain-anthropic "
            "(pip install langchain-anthropic)"
        ) from exc

    return ChatAnthropic(model=model, **kwargs)


_FACTORIES = {
    "deepseek": _deepseek,
    "openai": _openai,
    "anthropic": _anthropic,
}

# Клиенты по ключу (провайдер, модель, temperature). Кеш, а не одиночка:
# клиент дорогой и создаётся один раз на комбинацию, но сама комбинация
# выбирается в момент вызова ноды, а не при импорте модуля.
#
# Если серверная LLM_MODEL изменится, новый ключ создаст нового клиента.
# Старый клиент из кеша не должен сохранять прежнюю модель после смены настройки.
_CLIENTS: dict[tuple[str, str, float], BaseChatModel] = {}


def resolve_key(
    provider: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
) -> tuple[str, str, float]:
    """
    Нормализовать тройку «провайдер, модель, temperature».

    Пустое значение означает «взять из окружения» — так переопределение
    одного параметра не требует передавать остальные два.
    """
    name = (provider or cfg.llm_provider()).lower()
    if name not in _FACTORIES:
        raise cfg.ConfigError(
            f"провайдер {name!r}: поддерживаются " + ", ".join(_FACTORIES)
        )
    return (
        name,
        model or cfg.model_name(),
        cfg.llm_temperature() if temperature is None else float(temperature),
    )


def build_llm(
    provider: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
) -> BaseChatModel:
    """Чат-модель по ключу; без аргументов — целиком по текущему окружению."""
    key = resolve_key(provider, model, temperature)
    client = _CLIENTS.get(key)
    if client is None:
        name, model_name, temp = key
        client = _FACTORIES[name](model_name, dict(cfg.llm_kwargs(), temperature=temp))
        _CLIENTS[key] = client
    return client
