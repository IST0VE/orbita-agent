"""Модель: провайдер, ключ, температура, история и инструменты."""

from __future__ import annotations

from urllib.parse import urlsplit

from agent.config.env import (  # noqa: F401
    ConfigError,
    env_bool,
    env_float,
    env_int,
    env_opt,
    env_str,
)

# --------------------------------------------------------------------------
# LLM
#
# Значения по умолчанию повторяют то, что было зашито в коде до вынесения
# в окружение, поэтому пустой `.env` (кроме ключа) даёт прежнее поведение:
# DeepSeek и deepseek-v4-flash.
# --------------------------------------------------------------------------
LLM_PROVIDERS = ("deepseek", "openai", "anthropic")
LLM_TOOL_MODES = ("auto", "native", "prompt")

DEFAULT_PROVIDER = "deepseek"
DEFAULT_MODEL = "deepseek-v4-flash"


def llm_tool_mode() -> str:
    """auto переключается на текстовый протокол при отказе native tool calling."""
    mode = env_str("LLM_TOOL_MODE", "auto").lower()
    if mode not in LLM_TOOL_MODES:
        raise ConfigError(
            f"LLM_TOOL_MODE={mode!r}: поддерживаются " + ", ".join(LLM_TOOL_MODES)
        )
    return mode


def llm_provider() -> str:
    """
    Какой класс LangChain поднимать. Список — LLM_PROVIDERS, сопоставление
    с классами — в `providers.py`.

    Любой OpenAI-совместимый эндпоинт (свой шлюз, vLLM, Ollama, облачный
    провайдер с совместимым API) заводится как `openai` плюс LLM_API_BASE —
    отдельного имени для него не нужно.
    """
    name = env_str("LLM_PROVIDER", DEFAULT_PROVIDER).lower()
    if name not in LLM_PROVIDERS:
        raise ConfigError(f"LLM_PROVIDER={name!r}: поддерживаются " + ", ".join(LLM_PROVIDERS))
    return name


def model_name() -> str:
    return env_str("LLM_MODEL", DEFAULT_MODEL)


def llm_temperature() -> float:
    """Отдельно от llm_kwargs: входит в ключ кеша клиентов в `providers.py`."""
    return env_float("LLM_TEMPERATURE", 0.0, minimum=0.0)


def max_history_tokens() -> int:
    """
    Потолок на историю треда перед вызовом модели, в токенах. 0 — не подрезать.

    Тримминг ставит на полку объём входа, но не стоимость: каждый выброшенный
    ранний ход сдвигает префикс, и следующий запрос считается заново. На замере
    из 20 ходов (README, «Когда кеша перестаёт хватать») тред с лимитом 2000
    обошёлся в 1.66 раза дороже, чем без подрезки: hit rate на ходах со сдвигом
    падал до 40 %, а hit и miss отличаются в 50 раз.

    Поэтому по умолчанию выключено. Включать имеет смысл там, где кеша нет или
    он не помогает: редкий трафик, провайдер без кеширования префикса, упор
    в окно контекста.
    """
    return env_int("LLM_MAX_HISTORY_TOKENS", 0, minimum=0)


def llm_requests_per_minute() -> int:
    """
    Сколько запросов к модели шлюз пропускает за минуту. 0 — не ограничивать.

    Это не наш лимит, а чужой: столько разрешает шлюз перед моделью, и цифра
    берётся из его ответа (`x-ratelimit-*-limit-requests`) или у того, кто
    выдал ключ. Ставить надо чуть меньше объявленного: заголовок считает
    минуту по часам шлюза, а мы — по своим.

    Ноль означает «шлюз частоту не считает», а не «считать нулём»: без цифры
    очередь перед моделью не выстраивается вовсе и поведение прежнее.
    """
    return env_int("LLM_REQUESTS_PER_MINUTE", 0, minimum=0)


def llm_tokens_per_minute() -> int:
    """
    Сколько токенов шлюз пропускает за минуту. 0 — не ограничивать.

    Считается вход вместе с ответом: шлюзы тарифицируют минутное окно по
    `total_tokens`. Поэтому лимит, сопоставимый с размером одной истории, —
    это не «медленнее», а «не пройдёт»: роль с инструментами тащит переписку
    целиком, и на десятом ходу один запрос съедает всё окно. В этом случае
    вместе с лимитом задают и `LLM_MAX_HISTORY_TOKENS`.
    """
    return env_int("LLM_TOKENS_PER_MINUTE", 0, minimum=0)


def llm_kwargs() -> dict:
    """
    Аргументы конструктора чат-модели, кроме `model`.

    Имена аргументов выбраны так, чтобы совпадать у всех провайдеров: `api_key`
    и `base_url` — это псевдонимы, которые LangChain принимает и для
    ChatDeepSeek, и для ChatOpenAI, и для ChatAnthropic, как бы ни назывались
    поля внутри.

    Незаданные параметры в словарь не кладутся: у каждого класса на ключ и
    адрес стоят свои default_factory поверх вендорных переменных
    (DEEPSEEK_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY), и явный None их бы
    перебил. То есть LLM_API_KEY можно не задавать, если ключ уже лежит в
    "родной" переменной провайдера.

    `max_tokens` и `timeout` меняют ответ модели, но не префикс промпта,
    поэтому на кеш они не влияют.
    """
    kwargs: dict = {
        "temperature": llm_temperature(),
        "max_retries": env_int("LLM_MAX_RETRIES", 2, minimum=0),
    }

    api_key = env_opt("LLM_API_KEY")
    if api_key:
        kwargs["api_key"] = api_key

    for variable in ("LLM_API_BASE", "OPENAI_BASE_URL", "DEEPSEEK_API_BASE", "ANTHROPIC_BASE_URL"):
        endpoint = env_opt(variable)
        if endpoint:
            try:
                parsed = urlsplit(endpoint)
                valid = (
                    parsed.scheme in {"https", "http"}
                    and parsed.hostname
                    and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
                    and not any(char.isspace() or ord(char) < 32 for char in endpoint)
                    and (parsed.scheme == "https" or parsed.hostname in {"localhost", "127.0.0.1", "::1"})
                )
                _ = parsed.port  # Validate malformed ports without exposing the URL.
            except ValueError:
                valid = False
            if not valid:
                raise ConfigError(f"{variable}: нужен HTTPS URL без credentials/query/fragment; HTTP только для loopback")
    base_url = env_opt("LLM_API_BASE")
    if base_url:
        kwargs["base_url"] = base_url

    if env_opt("LLM_MAX_TOKENS"):
        kwargs["max_tokens"] = env_int("LLM_MAX_TOKENS", 0, minimum=1)

    if env_opt("LLM_TIMEOUT_S"):
        kwargs["timeout"] = env_float("LLM_TIMEOUT_S", 0.0, minimum=0.0, minimum_exclusive=True)

    return kwargs
