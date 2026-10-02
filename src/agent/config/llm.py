"""
Модель: провайдер, ключ, температура, история и инструменты.

Подключений к моделям несколько. Основное — LLM_PROVIDER, LLM_API_BASE,
LLM_API_KEY и LLM_MODEL, как было всегда. Рядом три запасных слота
LLM_ALT1_* … LLM_ALT3_*: другой шлюз или другой провайдер со своим ключом.
Каждый пользователь выбирает себе подключение и модель на странице настроек
(`llm_choice.py`), и выбор действует со следующего обращения к модели, без
перезапуска сервера.

Ключ и адрес из этого модуля не уходят никуда, кроме клиента модели: в
браузер попадают название подключения и список моделей. Пользователь не
может вписать свой адрес — иначе ключ сервера уехал бы туда, куда он скажет.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
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

#: Основное подключение — то, что задают LLM_PROVIDER / LLM_API_BASE / LLM_API_KEY.
MAIN_ENDPOINT = "main"
#: Запасные слоты LLM_ALT1_* … LLM_ALT3_*.
ALT_ENDPOINTS = ("alt1", "alt2", "alt3")
#: Ключ подключения без ключа: vLLM или Ollama в своей сети авторизации не
#: просят, а пустое значение клиент заменил бы «родной» переменной провайдера
#: (OPENAI_API_KEY) — и ключ основного шлюза уехал бы на чужой адрес.
KEYLESS = "EMPTY"
_MODEL_NAME_MAX = 200


def llm_tool_mode() -> str:
    """auto переключается на текстовый протокол при отказе native tool calling."""
    mode = env_str("LLM_TOOL_MODE", "auto").lower()
    if mode not in LLM_TOOL_MODES:
        raise ConfigError(
            f"LLM_TOOL_MODE={mode!r}: поддерживаются " + ", ".join(LLM_TOOL_MODES)
        )
    return mode


def _provider(variable: str, raw: str) -> str:
    name = raw.lower()
    if name not in LLM_PROVIDERS:
        raise ConfigError(f"{variable}={name!r}: поддерживаются " + ", ".join(LLM_PROVIDERS))
    return name


def default_llm_provider() -> str:
    """
    Провайдер основного подключения. Список — LLM_PROVIDERS, сопоставление
    с классами — в `providers.py`.

    Любой OpenAI-совместимый эндпоинт (свой шлюз, vLLM, Ollama, облачный
    провайдер с совместимым API) заводится как `openai` плюс LLM_API_BASE —
    отдельного имени для него не нужно.
    """
    return _provider("LLM_PROVIDER", env_str("LLM_PROVIDER", DEFAULT_PROVIDER))


def default_model_name() -> str:
    """Модель сервера по умолчанию: её получает тот, кто себе модель не выбирал."""
    return env_str("LLM_MODEL", DEFAULT_MODEL)


def llm_provider() -> str:
    """Провайдер, с которым работает текущий пользователь или прогон."""
    return llm_choice().endpoint.provider


def model_name() -> str:
    """
    Модель, с которой работает текущий пользователь или прогон.

    Не просто LLM_MODEL: у каждого может быть своя. По этому имени считается
    тариф, подписываются вызовы в метриках и выбирается клиент — и всё это
    обязано говорить о той модели, которая на самом деле ответила.
    """
    return llm_choice().model


# --------------------------------------------------------------------------
# Подключения и выбор пользователя
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Endpoint:
    """Куда и с каким ключом ходить за моделью."""

    id: str
    title: str
    provider: str
    #: None — адрес провайдера по умолчанию.
    base_url: str | None
    #: None — ключ из «родной» переменной провайдера (только у основного).
    api_key: str | None = field(repr=False)
    #: Объявленный список моделей; пусто — список спрашивается у шлюза.
    models: tuple[str, ...]
    #: Что предложить, пока пользователь не выбрал сам.
    default_model: str
    extra_body: dict = field(default_factory=dict)
    #: Переменные, которыми подключение задано: для сообщений администратору.
    variables: tuple[str, ...] = ()


@dataclass(frozen=True)
class Choice:
    """Подключение и модель, с которыми идёт обращение к модели."""

    endpoint: Endpoint
    model: str
    #: Выбрано пользователем, а не взято по умолчанию.
    chosen: bool


def _endpoint_url(variable: str, endpoint: str) -> str:
    """Адрес API: HTTPS без учётных данных, HTTP — только для своей машины."""
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
    return endpoint


def model_list(variable: str, raw: str) -> tuple[str, ...]:
    """Список моделей через запятую или пробел, без повторов и в порядке записи."""
    names: list[str] = []
    for item in raw.replace(",", " ").split():
        if len(item) > _MODEL_NAME_MAX or any(ord(char) < 32 for char in item):
            raise ConfigError(f"{variable}: имя модели длиннее {_MODEL_NAME_MAX} знаков или с управляющими символами")
        if item not in names:
            names.append(item)
    return tuple(names)


def _main_endpoint() -> Endpoint:
    base_url = env_opt("LLM_API_BASE")
    return Endpoint(
        id=MAIN_ENDPOINT,
        title="Основное подключение",
        provider=default_llm_provider(),
        base_url=_endpoint_url("LLM_API_BASE", base_url) if base_url else None,
        api_key=env_opt("LLM_API_KEY"),
        models=model_list("LLM_MODELS", env_str("LLM_MODELS")),
        default_model=default_model_name(),
        extra_body=llm_extra_body(),
        variables=("LLM_PROVIDER", "LLM_API_BASE", "LLM_API_KEY", "LLM_MODEL", "LLM_MODELS"),
    )


# Слоты читаются именами-константами, а не собираются из номера: схема настроек
# сверяется с кодом разбором вызовов `env_*` (test_settings_schema), и имя,
# собранное f-строкой, прошло бы мимо этой проверки.
_ALT_SLOTS: dict[str, Callable[[], dict]] = {
    "alt1": lambda: {
        "title": env_str("LLM_ALT1_TITLE"),
        "provider": ("LLM_ALT1_PROVIDER", env_str("LLM_ALT1_PROVIDER", "openai")),
        "base_url": ("LLM_ALT1_API_BASE", env_opt("LLM_ALT1_API_BASE")),
        "api_key": env_opt("LLM_ALT1_API_KEY"),
        "models": ("LLM_ALT1_MODELS", env_str("LLM_ALT1_MODELS")),
        "extra_body": ("LLM_ALT1_EXTRA_BODY", env_str("LLM_ALT1_EXTRA_BODY")),
    },
    "alt2": lambda: {
        "title": env_str("LLM_ALT2_TITLE"),
        "provider": ("LLM_ALT2_PROVIDER", env_str("LLM_ALT2_PROVIDER", "openai")),
        "base_url": ("LLM_ALT2_API_BASE", env_opt("LLM_ALT2_API_BASE")),
        "api_key": env_opt("LLM_ALT2_API_KEY"),
        "models": ("LLM_ALT2_MODELS", env_str("LLM_ALT2_MODELS")),
        "extra_body": ("LLM_ALT2_EXTRA_BODY", env_str("LLM_ALT2_EXTRA_BODY")),
    },
    "alt3": lambda: {
        "title": env_str("LLM_ALT3_TITLE"),
        "provider": ("LLM_ALT3_PROVIDER", env_str("LLM_ALT3_PROVIDER", "openai")),
        "base_url": ("LLM_ALT3_API_BASE", env_opt("LLM_ALT3_API_BASE")),
        "api_key": env_opt("LLM_ALT3_API_KEY"),
        "models": ("LLM_ALT3_MODELS", env_str("LLM_ALT3_MODELS")),
        "extra_body": ("LLM_ALT3_EXTRA_BODY", env_str("LLM_ALT3_EXTRA_BODY")),
    },
}


def _alt_endpoint(slot: str) -> Endpoint | None:
    """Запасной слот; не задан ни адрес, ни ключ — слота нет."""
    raw = _ALT_SLOTS[slot]()
    base_name, base_url = raw["base_url"]
    if not base_url and not raw["api_key"]:
        return None
    number = slot.removeprefix("alt")
    models = model_list(*raw["models"])
    prefix = f"LLM_ALT{number}_"
    return Endpoint(
        id=slot,
        title=raw["title"] or f"Подключение {int(number) + 1}",
        provider=_provider(*raw["provider"]),
        base_url=_endpoint_url(base_name, base_url) if base_url else None,
        api_key=raw["api_key"] or KEYLESS,
        models=models,
        default_model=models[0] if models else "",
        extra_body=_extra_body(*raw["extra_body"]),
        variables=tuple(prefix + name for name in ("TITLE", "PROVIDER", "API_BASE", "API_KEY", "MODELS", "EXTRA_BODY")),
    )


def llm_endpoints() -> list[Endpoint]:
    """Все настроенные подключения: основное первым."""
    found = [_main_endpoint()]
    for slot in ALT_ENDPOINTS:
        endpoint = _alt_endpoint(slot)
        if endpoint is not None:
            found.append(endpoint)
    return found


def llm_endpoint(endpoint_id: str) -> Endpoint:
    """Подключение по id; снятое администратором — ошибка, а не тихая замена."""
    if endpoint_id == MAIN_ENDPOINT:
        return _main_endpoint()
    endpoint = _alt_endpoint(endpoint_id) if endpoint_id in _ALT_SLOTS else None
    if endpoint is None:
        raise ConfigError(
            f"подключение {endpoint_id!r} больше не настроено на сервере — "
            "выберите модель заново в настройках («Моя модель»)"
        )
    return endpoint


#: Кто сейчас работает и что он выбрал: `(подключение, модель)` или None.
#: Ставит `llm_choice` — пакет настроек лежит ниже базы и о пользователях не
#: знает. Пока выбора нет (скрипт, тест, сервер без базы), действует основное
#: подключение с LLM_MODEL — ровно то, что было до выбора моделей.
_chooser: Callable[[], tuple[str, str] | None] | None = None


def set_chooser(chooser: Callable[[], tuple[str, str] | None] | None) -> None:
    global _chooser
    _chooser = chooser


def llm_choice() -> Choice:
    """Подключение и модель текущего пользователя, а без выбора — сервера."""
    picked = _chooser() if _chooser is not None else None
    if picked:
        endpoint_id, model = picked
        endpoint = llm_endpoint(endpoint_id)
        model = model or endpoint.default_model
        if not model:
            raise ConfigError(
                f"для подключения «{endpoint.title}» не выбрана модель — выберите её в настройках"
            )
        return Choice(endpoint, model, True)
    return Choice(_main_endpoint(), default_model_name(), False)


def llm_temperature() -> float:
    """Отдельно от llm_kwargs: входит в ключ кеша клиентов в `providers.py`."""
    return env_float("LLM_TEMPERATURE", 0.0, minimum=0.0)


def llm_max_tokens() -> int | None:
    """
    Потолок генерации, токены. Пусто или `0` — использовать лимит провайдера.

    У reasoning-моделей сюда входят и рассуждения: весь лимит может быть
    потрачен до первого символа итогового ответа. Поэтому общий лимит по
    умолчанию не подставляется: пустая настройка сохраняет поведение master.
    Сам по себе обрыв на лимите не означает зацикливания.
    """
    value = env_int("LLM_MAX_TOKENS", 0, minimum=0)
    return value or None


def llm_extra_body() -> dict:
    """Дополнительные параметры OpenAI-совместимого шлюза, например thinking."""
    return _extra_body("LLM_EXTRA_BODY", env_str("LLM_EXTRA_BODY"))


def _extra_body(variable: str, raw: str) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError):
        raise ConfigError(f"{variable}: ожидается корректный JSON-объект") from None
    if not isinstance(value, dict):
        raise ConfigError(f"{variable}: ожидается JSON-объект")
    # SDK сливает extra_body поверх основного запроса. Не даём незаметно
    # заменить задачу, модель, лимит или протокол вызова.
    reserved = {
        "model", "messages", "stream", "stream_options", "tools", "tool_choice",
        "functions", "function_call", "max_tokens", "max_completion_tokens", "temperature",
    }
    if reserved.intersection(value):
        raise ConfigError(f"{variable}: основные параметры запроса задаются отдельно")
    return value


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


def llm_max_retries() -> int:
    """Повторы одного вызова через общую очередь, без повторного запуска узла."""
    return env_int("LLM_MAX_RETRIES", 2, minimum=0)


def llm_kwargs(endpoint: Endpoint | None = None) -> dict:
    """
    Аргументы конструктора чат-модели, кроме `model`.

    `endpoint` — куда идти; не передан — подключение текущего пользователя.
    Ключ, адрес и `extra_body` берутся из подключения, остальное (температура,
    повторы, потолок, таймаут) общее для всех.

    Имена аргументов выбраны так, чтобы совпадать у всех провайдеров: `api_key`
    и `base_url` — это псевдонимы, которые LangChain принимает и для
    ChatDeepSeek, и для ChatOpenAI, и для ChatAnthropic, как бы ни назывались
    поля внутри.

    Незаданные параметры в словарь не кладутся: у каждого класса на ключ и
    адрес стоят свои default_factory поверх вендорных переменных
    (DEEPSEEK_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY), и явный None их бы
    перебил. То есть LLM_API_KEY можно не задавать, если ключ уже лежит в
    "родной" переменной провайдера. Запасному слоту ключ передаётся всегда
    (`KEYLESS`, если не задан): иначе туда уехал бы «родной» ключ основного.

    `max_tokens` и `timeout` меняют ответ модели, но не префикс промпта,
    поэтому на кеш они не влияют.
    """
    endpoint = endpoint or llm_choice().endpoint
    kwargs: dict = {
        "temperature": llm_temperature(),
        "max_retries": llm_max_retries(),
    }

    if endpoint.api_key:
        kwargs["api_key"] = endpoint.api_key

    # Вендорные адреса читает сам класс провайдера, когда адреса нет у
    # подключения, — проверяются они так же, как и наш.
    for variable in ("OPENAI_BASE_URL", "DEEPSEEK_API_BASE", "ANTHROPIC_BASE_URL"):
        vendor = env_opt(variable)
        if vendor:
            _endpoint_url(variable, vendor)
    if endpoint.base_url:
        kwargs["base_url"] = endpoint.base_url

    cap = llm_max_tokens()
    if cap:
        kwargs["max_tokens"] = cap

    if env_opt("LLM_TIMEOUT_S"):
        kwargs["timeout"] = env_float("LLM_TIMEOUT_S", 0.0, minimum=0.0, minimum_exclusive=True)

    if endpoint.extra_body:
        kwargs["extra_body"] = dict(endpoint.extra_body)

    return kwargs
