"""
Поднятие чат-модели по имени провайдера из окружения.

Проект написан вокруг контекстного кеша DeepSeek, но привязки к нему в коде нет:
провайдер, модель, адрес API и ключ приходят из `.env` (`LLM_*`), а здесь лежит
только сопоставление имени провайдера с классом LangChain.

Импорты ленивые и лежат внутри фабрик: тому, кто работает на DeepSeek, пакет
langchain-anthropic не нужен, и ставить его ради строчки в словаре незачем.
Пакета нет — ошибка скажет, что именно поставить.

Имена аргументов у фабрик общие (`api_key`, `base_url`, `temperature`,
`max_tokens`, `timeout`): LangChain принимает их как псевдонимы
во всех трёх классах, как бы ни назывались поля внутри. Поэтому маппинг здесь
не нужен. Исключение — `max_retries`: SDK-повторы отключены, чтобы повторы
через `llm_retry.invoke` проходили общую очередь RPM/TPM.

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

import hashlib
import json

from langchain_core.language_models import BaseChatModel

from agent import config as cfg
from agent import llm_pacing, metrics

# Импорт ради побочного действия: `llm_choice` ставит в `config` выбор
# пользователя, и `cfg.llm_choice()` начинает отвечать «модель этого человека»,
# а не только LLM_MODEL.
from agent import llm_choice  # noqa: F401, E402  isort: skip

# Ритм обращений к модели: один обработчик на процесс, потому что минутное окно
# шлюз считает по ключу, а не по клиенту. Клиентов ниже несколько — по одному на
# сочетание провайдера, модели и temperature, — и свой счётчик у каждого означал
# бы превышение ровно там, где конвейер переключает роль на другую модель.
_pacer = llm_pacing.Pacer()


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
    Anthropic требует max_tokens на уровне API. Если LLM_MAX_TOKENS пуст
    или равен 0, ChatAnthropic подставляет свой дефолт — у этого провайдера
    ответ без потолка не бывает.
    """
    if kwargs.get("extra_body"):
        raise cfg.ConfigError("LLM_EXTRA_BODY поддерживается только OpenAI-совместимыми API")
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

# Клиенты по ключу (провайдер, модель, temperature, подключение). Кеш, а не
# одиночка: клиент дорогой и создаётся один раз на комбинацию, но сама
# комбинация выбирается в момент вызова ноды, а не при импорте модуля.
#
# Подключение входит в ключ отпечатком всего, что уходит в конструктор: адреса,
# ключа, extra_body, таймаута. Пользователь выбрал другое подключение или
# администратор сменил ключ из интерфейса — со следующего вызова работает новый
# клиент, без перезапуска сервера. Старый клиент из кеша не должен сохранять
# прежний адрес или ключ после смены настройки.
_CLIENTS: dict[tuple[str, str, float, str], BaseChatModel] = {}
#: Больше комбинаций не держим: каждая смена ключа или адреса — новая запись, и
#: без потолка кеш рос бы вместе с числом правок настроек.
_MAX_CLIENTS = 64


def resolve_key(
    provider: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
) -> tuple[str, str, float]:
    """
    Нормализовать тройку «провайдер, модель, temperature».

    Пустое значение означает «взять у текущего пользователя»: его подключение
    и его модель, а без выбора — основное подключение и LLM_MODEL. Так
    переопределение одного параметра не требует передавать остальные два.
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


def connection_key(endpoint: cfg.Endpoint | None = None) -> str:
    """
    Отпечаток подключения: id плюс всё, что уходит в конструктор клиента.

    Ключ API в отпечаток входит хешем — сам он в ключе словаря не нужен.
    """
    endpoint = endpoint or cfg.llm_choice().endpoint
    kwargs = cfg.llm_kwargs(endpoint)
    if "api_key" in kwargs:
        kwargs["api_key"] = hashlib.sha256(str(kwargs["api_key"]).encode()).hexdigest()
    payload = json.dumps([endpoint.id, endpoint.provider, kwargs], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def build_llm(
    provider: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
) -> BaseChatModel:
    """Чат-модель по ключу; без аргументов — целиком по выбору текущего пользователя."""
    endpoint = cfg.llm_choice().endpoint
    key = (*resolve_key(provider, model, temperature), connection_key(endpoint))
    client = _CLIENTS.get(key)
    if client is None:
        name, model_name, temp, _ = key
        # callbacks — не наблюдение, а управление: `Pacer` придерживает запрос
        # до отправки, пока в минутном окне шлюза не освободится место. Вешается
        # он здесь, на клиента, а не на вызовы: звать модель в проекте умеют
        # больше десяти мест, и каждое из них обошло бы обёртку.
        # Retries must re-enter the callback. SDK-internal retries bypass it
        # and can exceed TPM/RPM; application calls use llm_retry.invoke.
        # Метрики — после ритма: время ответа не должно включать очередь шлюза.
        kwargs = dict(
            cfg.llm_kwargs(endpoint), temperature=temp, callbacks=[_pacer, metrics.LLM], max_retries=0
        )
        client = _FACTORIES[name](model_name, kwargs)
        if len(_CLIENTS) >= _MAX_CLIENTS:
            _CLIENTS.clear()
        _CLIENTS[key] = client
    return client


def build_probe(endpoint: cfg.Endpoint, model: str, *, timeout: float) -> BaseChatModel:
    """
    Клиент для проверки модели со страницы настроек: не из кеша и без повторов.

    Через `Pacer` проверка идёт так же, как прогон: минутное окно шлюза у них
    общее, и проверка не должна выбивать прогону 429.
    """
    kwargs = dict(
        cfg.llm_kwargs(endpoint), timeout=timeout, max_retries=0, max_tokens=64, callbacks=[_pacer]
    )
    return _FACTORIES[endpoint.provider](model, kwargs)
