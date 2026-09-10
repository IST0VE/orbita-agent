"""
Конфигурация из окружения — единственное место, где проект читает `os.environ`.

Правило простое: всё, что может отличаться между машинами, стендами и тарифами —
провайдер, адрес API, ключ, модель, цены, таймауты, пути REST — приходит из
`.env`, а в коде остаются только значения по умолчанию. Полный список переменных
с описанием лежит в `.env.example`.

Имена переменных не привязаны к вендору: `LLM_*`, а не `DEEPSEEK_*`. Проект
написан вокруг кеша DeepSeek, но подставить можно любого поддерживаемого
провайдера — сопоставление имени с классом LangChain лежит в `providers.py`.

Значения читаются функциями, а не константами модуля, чтобы тесты и окружение
процесса могли менять их без повторного импорта. Сам `.env` загружается один раз
при старте Python-процесса, а клиенты LLM создаются лениво и кешируются по
провайдеру, модели и temperature. Изменения файла через интерфейс применяются
после перезапуска сервера.

Неразобранное значение — это ошибка, а не повод молча взять default:
`LLM_TEMPERATURE=ноль` должно падать на старте, а не тихо работать нулём.
"""

from __future__ import annotations

import math
import os
from urllib.parse import urlsplit

from dotenv import dotenv_values, find_dotenv, load_dotenv

from costmeter import prices

_DOTENV_PATH = find_dotenv(usecwd=True)
load_dotenv(_DOTENV_PATH)


def _drop_empty_vars() -> None:
    """
    `VAR=` в .env означает «не задана», а не «пустая строка».

    Это важно не только для нашего кода: часть значений по умолчанию живёт
    внутри библиотек. Классы LangChain читают собственные переменные
    (DEEPSEEK_API_BASE, OPENAI_API_KEY и подобные) через default_factory, и
    объявленная, но пустая переменная перебила бы их дефолт пустой строкой.
    Поэтому объявленные в .env, но незаполненные переменные убираются из
    окружения целиком.

    Трогаем только имена, перечисленные в самом .env, и только если снаружи
    (в реальном окружении процесса, CI, docker) значения тоже нет: переменная
    из окружения всегда важнее файла.
    """
    if not _DOTENV_PATH:
        return
    for name, value in dotenv_values(_DOTENV_PATH).items():
        if (value or "").strip():
            continue
        if not (os.environ.get(name) or "").strip():
            os.environ.pop(name, None)


_drop_empty_vars()

_TRUTHY = {"1", "true", "yes", "on", "y"}
_FALSY = {"0", "false", "no", "off", "n"}


class ConfigError(RuntimeError):
    """Переменная окружения задана, но её значение нельзя разобрать."""


# --------------------------------------------------------------------------
# Примитивы чтения
# --------------------------------------------------------------------------
def env_str(name: str, default: str = "") -> str:
    """Строка без окружающих пробелов. Пустая переменная считается незаданной."""
    return (os.getenv(name) or "").strip() or default


def env_opt(name: str) -> str | None:
    """То же самое, но незаданное значение — None: для необязательных параметров."""
    return env_str(name) or None


def env_int(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = env_str(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}={raw!r}: ожидается целое число") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name}={raw!r}: значение должно быть не меньше {minimum}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{name}={raw!r}: значение должно быть не больше {maximum}")
    return value


def env_float(
    name: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    minimum_exclusive: bool = False,
) -> float:
    raw = env_str(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}={raw!r}: ожидается число") from exc
    if not math.isfinite(value):
        raise ConfigError(f"{name}={raw!r}: ожидается конечное число")
    if minimum is not None:
        below = value <= minimum if minimum_exclusive else value < minimum
        if below:
            relation = "больше" if minimum_exclusive else "не меньше"
            raise ConfigError(f"{name}={raw!r}: значение должно быть {relation} {minimum}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{name}={raw!r}: значение должно быть не больше {maximum}")
    return value


def env_bool(name: str, default: bool) -> bool:
    raw = env_str(name).lower()
    if not raw:
        return default
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    raise ConfigError(f"{name}={raw!r}: ожидается 1/0, true/false, yes/no, on/off")


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


# --------------------------------------------------------------------------
# Цены, $ за 1M токенов
#
# Значения по умолчанию — deepseek-v4-flash на момент написания. Тарифы
# меняются регулярно, а при смене провайдера меняются целиком — это ровно тот
# случай, когда правка не должна требовать коммита: сверились с прайсом
# провайдера, вписали в .env.
# --------------------------------------------------------------------------
def price_per_mtok() -> dict:
    """
    Цена на каждый счётчик: сначала таблица `costmeter/prices.toml` по паре
    (провайдер, модель), поверх — переменные `PRICE_*`, если заданы.

    Три переменные хороши ровно для одной модели: у каждой свой тариф, и при
    смене `LLM_MODEL` их пришлось бы переписывать руками. Таблица снимает эту
    работу, а переменные остаются как переопределение — для стенда, скидки
    или свежего прайса, до которого не дошли руки.

    Модели нет в таблице и переменные не заданы — `costmeter` предупредит,
    а стоимость будет нулевой. Это лучше молчаливого неверного счёта.
    """
    return prices.for_model(llm_provider(), model_name()).per_counter()


def budget_usd_per_thread() -> float:
    """
    Потолок расхода на один тред в долларах. 0 — без потолка (по умолчанию).

    Учёт, который только отчитывается постфактум, — половина ценности. Вторая
    половина: при превышении граф уходит в конец с честным сообщением вместо
    следующего вызова модели.
    """
    return env_float("BUDGET_USD_PER_THREAD", 0.0, minimum=0.0)


# --------------------------------------------------------------------------
# Папки задач
#
# Одна папка внутри AGENT_INPUT_DIR — одна задача: оператор кладёт туда файлы,
# выбирает папку в интерфейсе и запускает прогон. Выбранная папка приезжает в
# `configurable.input_dir`, и инструменты чтения видят только её.
# --------------------------------------------------------------------------
def input_root() -> str:
    """Корень с папками задач. Относительный путь — от рабочей папки процесса."""
    return env_str("AGENT_INPUT_DIR", "input")


def input_max_chars() -> int:
    """
    Потолок на один файл, символы. 0 — читать целиком.

    Файл уходит в промпт, то есть в деньги на каждом ходе, где он всплывает.
    Потолок здесь, а не в инструменте: у него нет причин знать про тарифы.
    """
    return env_int("AGENT_INPUT_MAX_CHARS", 20000, minimum=0)


def diagram_max_chars() -> int:
    """
    Потолок на разобранную схему в сообщении роли, символы. 0 — без потолка.

    Схема на тысячу элементов разбирается в сотни килобайт JSON, и все четыре
    роли конвейера получают его целиком: это четыре оплаченных копии одного
    файла. Потолок здесь же, где потолок на файл задачи, и по той же причине —
    данные, которые не поместились, честно помечаются обрезанными в тексте
    (`drawio.as_json`), а не исчезают молча.
    """
    return env_int("DIAGRAM_MAX_CHARS", 60000, minimum=0)


# --------------------------------------------------------------------------
# База знаний
#
# Политика продукта живёт в системном промпте — это осознанный выбор ради
# кеша: префикс длинный, стабильный и целиком уходит в кеш со второго запроса.
# Выше двух-трёх тысяч токенов так не масштабируется, и тогда включается
# retrieval: политика уезжает в базу знаний, а найденные куски подставляются
# в КОНЕЦ вопроса оператора — префикс при этом не двигается.
# --------------------------------------------------------------------------
def knowledge_enabled() -> bool:
    """
    По умолчанию выключено: на текущем объёме политики целиком в префиксе
    дешевле, чем retrieval. Включать, когда база знаний перестаёт помещаться.
    """
    return env_bool("KNOWLEDGE_ENABLED", False)


def knowledge_dir() -> str | None:
    """
    Папка с документами базы знаний (`*.md`, один файл — один документ).
    Не задана — берутся встроенные разделы из системного промпта.
    """
    return env_opt("KNOWLEDGE_DIR")


def knowledge_top_k() -> int:
    """Сколько документов подставлять в вопрос. Больше — дороже каждый ход."""
    return env_int("KNOWLEDGE_TOP_K", 2, minimum=0)


def knowledge_min_score() -> float:
    """
    Порог релевантности. Ниже порога документ не подставляется вообще: пустая
    справка дешевле и честнее случайной.
    """
    return env_float("KNOWLEDGE_MIN_SCORE", 0.05, minimum=0.0, maximum=1.0)


# --------------------------------------------------------------------------
# Долгая память между тредами
#
# Чекпоинтер помнит один тред. Store живёт поверх тредов, и это ровно то
# место, где хранится «что уже обсуждали по этому аккаунту».
# --------------------------------------------------------------------------
def memory_enabled() -> bool:
    """Без store (например, в тестах без него) память сама себя выключает."""
    return env_bool("MEMORY_ENABLED", True)


def memory_namespace() -> str:
    """Корень пространства имён в store: разные стенды не смешиваются."""
    return env_str("MEMORY_NAMESPACE", "orbita")


def memory_max_facts() -> int:
    """
    Сколько фактов помнить на аккаунт. Память подставляется в промпт, поэтому
    её объём — это деньги на каждом ходе; хранится последнее.
    """
    return env_int("MEMORY_MAX_FACTS", 5, minimum=0)


def memory_account_pattern() -> str:
    """
    Чем в тексте опознаётся аккаунт. Демо-данные используют `acc-1024`, у себя
    подставляется свой формат — иначе память привязывать не к чему.
    """
    return env_str("MEMORY_ACCOUNT_PATTERN", r"acc-[0-9]+")


# --------------------------------------------------------------------------
# Публикация: куда уходит документ треда
#
# Confluence — не единственная возможная цель, и требовать его ради запуска
# проекта незачем. `auto` означает «в Confluence, если он настроен, иначе на
# диск»: человек без корпоративной wiki видит готовый документ, а не строку
# «этап пропущен».
# --------------------------------------------------------------------------
PUBLISH_TARGETS = ("auto", "confluence", "file", "none")


def publish_target() -> str:
    target = env_str("PUBLISH_TARGET", "auto").lower()
    if target not in PUBLISH_TARGETS:
        raise ConfigError(
            f"PUBLISH_TARGET={target!r}: поддерживаются " + ", ".join(PUBLISH_TARGETS)
        )
    return target


def publish_dir() -> str:
    """Куда складывать документы при PUBLISH_TARGET=file. Одна страница — один файл."""
    return env_str("PUBLISH_DIR", "published")


def publish_require_approval() -> bool:
    """
    Спрашивать ли оператора перед публикацией.

    Включено — граф останавливается перед публикацией через `interrupt()`,
    показывает черновик каждой будущей страницы и ждёт `Command(resume=...)`.
    Это стандартный human-in-the-loop LangGraph: в Studio и в чат-интерфейсе
    он работает без дополнительного кода.

    Включено по умолчанию: любой прогон, который что-то создаёт снаружи,
    останавливается на верификацию. Раньше здесь стоял ноль, и довод был
    в том, что страницу перезапишет следующий прогон. Довод оказался неполным:
    перезаписывается своя страница, а с чужой, совпавшей заголовком, версия
    уже уехала всем подписчикам пространства. Теперь страницу показывают
    до записи — и видно, создастся она или перезапишет существующую.

    Выключать стоит там, где оператора нет: cron, скрипт, CI. Тред без ответа
    на `interrupt()` там просто зависнет.
    """
    return env_bool("PUBLISH_REQUIRE_APPROVAL", True)


def pipeline_require_approval() -> bool:
    """
    Спрашивать ли оператора между этапами конвейера.

    Выключено по умолчанию: конвейер задуман как один запуск от задачи до пяти
    документов, и остановка на каждом этапе превращает его в переписку.

    Включать стоит на дорогой модели и на незнакомой задаче. Требования,
    написанные по неверно понятому входу, оплачиваются четыре раза — по разу
    на каждый следующий этап, — и ворота дают срезать это на первой странице.
    Механика та же, что у подтверждения публикации: `interrupt()` и
    `Command(resume=...)`, без кнопок в коде.
    """
    return env_bool("PIPELINE_REQUIRE_APPROVAL", False)


# --------------------------------------------------------------------------
# Персистентность собственного рантайма
#
# `langgraph dev` держит своё хранилище сам, и графу чекпоинтер не нужен —
# см. компиляцию в конце graph.py. Эти настройки нужны там, где граф
# запускается своим кодом: run_demo.py, скрипт в cron, свой сервис.
# --------------------------------------------------------------------------
CHECKPOINT_BACKENDS = ("memory", "postgres")


def checkpoint_backend() -> str:
    backend = env_str("CHECKPOINT_BACKEND", "memory").lower()
    if backend not in CHECKPOINT_BACKENDS:
        raise ConfigError(
            f"CHECKPOINT_BACKEND={backend!r}: поддерживаются " + ", ".join(CHECKPOINT_BACKENDS)
        )
    return backend


def postgres_uri() -> str:
    """Строка подключения для CHECKPOINT_BACKEND=postgres."""
    return env_str("POSTGRES_URI", "postgresql://orbita:orbita@localhost:5432/orbita")


# --------------------------------------------------------------------------
# Агент
# --------------------------------------------------------------------------
def agent_name() -> str:
    """Имя агента: идёт в заголовок страницы и в подпись под документацией."""
    return env_str("AGENT_NAME", "Orbita")


# --------------------------------------------------------------------------
# Confluence
#
# Обязательные переменные перечислены в CONFLUENCE_REQUIRED_VARS: без любой
# из них этап публикации пропускается, а не падает.
# --------------------------------------------------------------------------
CONFLUENCE_REQUIRED_VARS = (
    "CONFLUENCE_BASE_URL",
    "CONFLUENCE_TOKEN",
    "CONFLUENCE_SPACE_KEY",
)

# v2 создаёт страницу по числовому spaceId, ключ пространства там не принимается.
CONFLUENCE_REQUIRED_VARS_V2 = (
    "CONFLUENCE_BASE_URL",
    "CONFLUENCE_TOKEN",
    "CONFLUENCE_SPACE_ID",
)


def confluence_required_vars() -> tuple[str, ...]:
    """Что обязано быть в окружении — зависит от версии REST."""
    return (
        CONFLUENCE_REQUIRED_VARS
        if confluence_api_version() == "v1"
        else CONFLUENCE_REQUIRED_VARS_V2
    )


def confluence_base_url() -> str:
    return env_str("CONFLUENCE_BASE_URL").rstrip("/")


def confluence_allow_insecure_http() -> bool:
    """Разрешить токен Confluence по незашифрованному HTTP не на loopback."""
    return env_bool("CONFLUENCE_ALLOW_INSECURE_HTTP", False)


def confluence_token() -> str:
    return env_str("CONFLUENCE_TOKEN")


def confluence_space_key() -> str:
    return env_str("CONFLUENCE_SPACE_KEY")


def confluence_space_id() -> str | None:
    """
    Числовой идентификатор пространства. Нужен только для REST v2: там при
    создании страницы передаётся `spaceId`, а ключ пространства не принимается.
    """
    return env_opt("CONFLUENCE_SPACE_ID")


def confluence_email() -> str | None:
    """Задан — Basic (Cloud), не задан — Bearer с PAT (Server / Data Center)."""
    return env_opt("CONFLUENCE_EMAIL")


def confluence_parent_id() -> str | None:
    return env_opt("CONFLUENCE_PARENT_PAGE_ID")


def confluence_page_title() -> str | None:
    """Фиксированный заголовок вместо генерации из первого вопроса оператора."""
    return env_opt("CONFLUENCE_PAGE_TITLE")


def confluence_publish() -> bool:
    """Рубильник: 0 — этап не ходит в сеть вообще."""
    return env_bool("CONFLUENCE_PUBLISH", True)


PUBLISH_MODES = ("each", "changed", "manual")


def confluence_publish_mode() -> str:
    """
    Когда именно ходить в Confluence.

      each    — после каждого ответа оператору (историческое поведение);
      changed — только если документ изменился с прошлой публикации;
      manual  — только по явному флагу `publish` в configurable.

    `each` на треде из десяти ходов — это десять пар GET+PUT и десять версий
    страницы в истории, из которых девять никому не нужны.
    """
    mode = env_str("CONFLUENCE_PUBLISH_MODE", "each").lower()
    if mode not in PUBLISH_MODES:
        raise ConfigError(
            f"CONFLUENCE_PUBLISH_MODE={mode!r}: поддерживаются " + ", ".join(PUBLISH_MODES)
        )
    return mode


def confluence_max_turns() -> int:
    """
    Сколько последних ходов показывать на странице целиком. 0 — все.

    Тред живёт долго, а `render_document` проходит по всем сообщениям: без
    потолка тело PUT растёт линейно вместе с историей. Ходы за пределами
    потолка сворачиваются в макрос `expand` — там остаются только вопросы
    оператора, без ответов и без выводов инструментов.
    """
    return env_int("CONFLUENCE_MAX_TURNS", 10, minimum=0)


def confluence_include_tool_output() -> bool:
    """
    Класть ли на страницу сырые ответы инструментов.

    По умолчанию нет. Сейчас за инструментами фейковая база, но как только
    появится настоящая, на корпоративную wiki начнут автоматически уезжать
    данные клиентов: статусы оплат, идентификаторы аккаунтов, просрочки.
    Включать это должно быть отдельным осознанным действием.
    """
    return env_bool("CONFLUENCE_INCLUDE_TOOL_OUTPUT", False)


def confluence_mask_patterns() -> list[str]:
    """
    Регулярные выражения, которые маскируются в тексте перед публикацией.

    Несколько шаблонов разделяются двумя вертикальными чертами `||` — одна
    занята альтернативой внутри самих регулярок.
    """
    raw = env_str("CONFLUENCE_MASK_PATTERNS")
    return [part.strip() for part in raw.split("||") if part.strip()]


def confluence_mask_replacement() -> str:
    return env_str("CONFLUENCE_MASK_REPLACEMENT", "***")


API_VERSIONS = ("v1", "v2")


def confluence_api_version() -> str:
    """
    Какой диалект REST использовать: v1 (`/rest/api/content`, Cloud и DC) или
    v2 (`/api/v2/pages`, только Cloud). У версий отличается не только путь, но
    и формат тела, поиск страницы и место идентификатора пространства —
    поэтому одной CONFLUENCE_API_PATH переезд не решается.
    """
    version = env_str("CONFLUENCE_API_VERSION", "v1").lower()
    if version not in API_VERSIONS:
        raise ConfigError(
            f"CONFLUENCE_API_VERSION={version!r}: поддерживаются " + ", ".join(API_VERSIONS)
        )
    return version


def confluence_api_path() -> str:
    """
    База REST. По умолчанию зависит от CONFLUENCE_API_VERSION: `/rest/api/content`
    для v1 и `/api/v2` для v2. Переменная нужна, чтобы переезд на другой префикс
    (обратный прокси, урезанный шлюз) не требовал правки кода.
    """
    default = "/rest/api/content" if confluence_api_version() == "v1" else "/api/v2"
    return env_str("CONFLUENCE_API_PATH", default)


def confluence_timeout_s() -> float:
    return env_float("CONFLUENCE_TIMEOUT_S", 30.0, minimum=0.0, minimum_exclusive=True)


def atlassian_request_interval_s() -> float:
    """
    Общая пауза между HTTP-запросами Jira/Confluence, включая чтение и запись.

    Десять секунд — не осторожность вообще, а мера против защитного шлюза
    перед корпоративным контуром: на паузе в пять секунд он отбивал четвёртую
    подряд запись HTML-страницей «Access Blocked».
    """
    return env_float("ATLASSIAN_REQUEST_INTERVAL_S", 10.0, minimum=0.0)


def confluence_request_interval_s() -> float:
    """
    Пауза перед запросом к Confluence, если она отличается от общей.

    Отличается она по факту: один и тот же конвейер с одной и той же паузой
    ходит в Jira беспрепятственно и получает блокировку на wiki. Хосты разные,
    и политика шлюза на них тоже разная, поэтому замедлять из-за одного второй
    незачем. Пусто — общая ATLASSIAN_REQUEST_INTERVAL_S.
    """
    return env_float(
        "CONFLUENCE_REQUEST_INTERVAL_S", atlassian_request_interval_s(), minimum=0.0
    )


def jira_request_interval_s() -> float:
    """Пауза перед запросом к Jira; пусто — общая ATLASSIAN_REQUEST_INTERVAL_S."""
    return env_float("JIRA_REQUEST_INTERVAL_S", atlassian_request_interval_s(), minimum=0.0)


def atlassian_block_retries() -> int:
    """Сколько раз пережидать отказ защитного шлюза, прежде чем сдаться."""
    return env_int("ATLASSIAN_BLOCK_RETRIES", 3, minimum=0, maximum=10)


def atlassian_block_backoff_s() -> float:
    """Первая пауза после отказа шлюза; каждая следующая вдвое длиннее."""
    return env_float("ATLASSIAN_BLOCK_BACKOFF_S", 15.0, minimum=0.0)


def atlassian_user_agent() -> str:
    """
    Как агент представляется Jira и Confluence.

    Умолчание requests — `python-requests/2.x`: для защитного шлюза это
    подпись робота, и попросить админов разрешить именно этот трафик по ней
    нельзя. Своё имя решает обе задачи. Пустое значение убирает заголовок.
    """
    return env_str("ATLASSIAN_USER_AGENT", f"{agent_name()} (Jira/Confluence integration)")


def confluence_version_message() -> str:
    """Комментарий к версии страницы — виден в истории правок Confluence."""
    return env_str("CONFLUENCE_VERSION_MESSAGE", f"обновлено агентом {agent_name()}")


def confluence_title_max_len() -> int:
    """Сколько символов первого вопроса влезает в автозаголовок страницы."""
    return env_int("CONFLUENCE_TITLE_MAX_LEN", 80, minimum=1)


def confluence_search_path() -> str:
    """
    Путь CQL-поиска. Он один на обе версии REST, и это не оплошность: у v2
    полнотекстового поиска нет вообще, есть только выборка страниц по
    идентификаторам и по пространству. Поэтому искать приходится v1-эндпоинтом
    даже там, где страницы потом читаются и пишутся через v2.
    """
    return env_str("CONFLUENCE_SEARCH_PATH", "/rest/api/content/search")


def confluence_search_limit() -> int:
    """Сколько страниц возвращать на один поисковый запрос агента."""
    return env_int("CONFLUENCE_SEARCH_LIMIT", 8, minimum=1, maximum=50)


def confluence_read_max_chars() -> int:
    """
    Потолок на текст одной прочитанной страницы.

    Инструмент отдаёт результат в историю роли, а история уезжает в каждый
    следующий запрос этого этапа. Страница внутренней wiki на сто килобайт
    без потолка оплачивается столько раз, сколько роль сходит за следующей.
    """
    return env_int("CONFLUENCE_READ_MAX_CHARS", 12000, minimum=500)


# --------------------------------------------------------------------------
# Чтение Jira
#
# Публикация в Confluence и чтение из Jira — разные системы с разными
# токенами, поэтому переменные тоже свои. Общее у них одно: без базового
# адреса и токена этап не работает, и узнать об этом надо до вызова модели.
# --------------------------------------------------------------------------
JIRA_REQUIRED_VARS = ("JIRA_BASE_URL", "JIRA_TOKEN")


def jira_required_vars() -> tuple[str, ...]:
    return JIRA_REQUIRED_VARS


def jira_base_url() -> str:
    return env_str("JIRA_BASE_URL").rstrip("/")


def jira_allow_insecure_http() -> bool:
    """Разрешить токен Jira по незашифрованному HTTP не на loopback."""
    return env_bool("JIRA_ALLOW_INSECURE_HTTP", False)


def jira_token() -> str:
    return env_str("JIRA_TOKEN")


def jira_email() -> str | None:
    """Задан — Basic (Cloud), не задан — Bearer с PAT (Server / Data Center)."""
    return env_opt("JIRA_EMAIL")


def jira_api_path() -> str:
    """
    База REST. У Cloud это `/rest/api/3`, у Server / Data Center — `/rest/api/2`.
    Версии отличаются форматом описания задачи: v3 отдаёт ADF-документ, v2 —
    wiki-разметку строкой. Разбирает оба `jira.field_text`.
    """
    return env_str("JIRA_API_PATH", "/rest/api/3")


def jira_search_path() -> str:
    """
    Путь поиска по JQL. По умолчанию зависит от JIRA_API_PATH, и это не
    косметика: Cloud перевёл поиск на `/search/jql`, а Server / Data Center
    остался на `/search`. Промахнуться версией здесь — это 404 на каждом
    поиске при полностью рабочем чтении задач по ключу.
    """
    default = "/search/jql" if jira_api_path().rstrip("/").endswith("3") else "/search"
    return env_str("JIRA_SEARCH_PATH", default)


def jira_timeout_s() -> float:
    return env_float("JIRA_TIMEOUT_S", 30.0, minimum=0.0, minimum_exclusive=True)


def jira_search_limit() -> int:
    """Сколько задач возвращать на один поисковый запрос агента."""
    return env_int("JIRA_SEARCH_LIMIT", 10, minimum=1, maximum=50)


def jira_comments_limit() -> int:
    """Сколько последних комментариев прикладывать к прочитанной задаче."""
    return env_int("JIRA_COMMENTS_LIMIT", 20, minimum=0, maximum=100)


def jira_read_max_chars() -> int:
    """Потолок на текст одной задачи — по той же причине, что и у Confluence."""
    return env_int("JIRA_READ_MAX_CHARS", 12000, minimum=500)


# --------------------------------------------------------------------------
# Заведение задач в Jira
#
# Обратное направление: до сих пор проект в Jira только читал. Заведённая
# задача — внешний побочный эффект, и обходится он дороже опубликованной
# страницы: страницу перезаписывает следующий прогон, а тридцать задач,
# заведённых не в тот проект, приходится закрывать руками по одной.
#
# Поэтому рубильников три, и они разного назначения: `JIRA_CREATE_ISSUES` —
# ходить ли в трекер на запись вообще, `JIRA_CREATE_REQUIRE_APPROVAL` —
# спрашивать ли человека перед этим (по умолчанию да), `JIRA_MAX_ISSUES` —
# потолок на одну пачку, чтобы ошибка в декомпозиции стоила десятков задач,
# а не сотен.
# --------------------------------------------------------------------------
def jira_project_key() -> str:
    """
    Проект по умолчанию, в котором заводятся задачи.

    Пусто — проект спрашивается у оператора: он единственное, чего конвейер не
    может вывести из аналитики, и единственное, что у оператора спрашивают.
    """
    return env_str("JIRA_PROJECT_KEY").strip().upper()


def jira_create_issues() -> bool:
    """Рубильник: 0 — конвейер не заводит задачи, а только выпускает план."""
    return env_bool("JIRA_CREATE_ISSUES", True)


def jira_create_require_approval() -> bool:
    """
    Спрашивать ли оператора перед заведением задач.

    По умолчанию включено, в отличие от публикации страницы. Разница в цене
    ошибки: страница перезаписывается следующим прогоном, задачи — нет.
    """
    return env_bool("JIRA_CREATE_REQUIRE_APPROVAL", True)


def jira_max_issues() -> int:
    """Потолок на число задач, заводимых за один прогон."""
    return env_int("JIRA_MAX_ISSUES", 50, minimum=1, maximum=200)


def jira_epic_type() -> str:
    """Имя типа «эпик» в проекте. В русифицированных инстансах оно другое."""
    return env_str("JIRA_EPIC_TYPE", "Epic")


def jira_default_issue_type() -> str:
    """Тип, которым заменяется отсутствующий в проекте (например `Story`)."""
    return env_str("JIRA_DEFAULT_ISSUE_TYPE", "Task")


def jira_epic_link_field() -> str:
    """
    Поле связи с эпиком для Server / Data Center (например `customfield_10014`).

    В Cloud эпик — обычный родитель (`fields.parent`), и поле не нужно.
    В Data Center до нового интерфейса связь ведётся кастомным полем, имя
    которого у каждого инстанса своё, и угадать его нельзя.
    """
    return env_str("JIRA_EPIC_LINK_FIELD").strip()


# --------------------------------------------------------------------------
# Локальный служебный HTTP API
# --------------------------------------------------------------------------
def api_admin_token() -> str | None:
    """Обязательный Bearer-токен для всего HTTP API; задаётся только на сервере."""
    return env_opt("API_ADMIN_TOKEN")


def api_max_request_bytes() -> int:
    """Жёсткий потолок JSON-тела служебного API."""
    return env_int("API_MAX_REQUEST_BYTES", 65536, minimum=1024, maximum=10 * 1024 * 1024)


# --------------------------------------------------------------------------
# Демо-скрипт
# --------------------------------------------------------------------------
def demo_thread_id() -> str:
    """
    Один thread_id на весь прогон: история дописывается в конец, префикс
    только растёт и остаётся закешированным.
    """
    return env_str("DEMO_THREAD_ID", "demo-thread-1")


def demo_answer_chars() -> int:
    """Сколько символов ответа модели печатать в консоль."""
    return env_int("DEMO_ANSWER_CHARS", 600, minimum=0)
