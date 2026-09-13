"""Confluence: реквизиты, режимы записи и маскирование."""

from __future__ import annotations

from agent.config.env import (  # noqa: F401
    ConfigError,
    env_bool,
    env_float,
    env_int,
    env_opt,
    env_str,
)
from agent.config.runtime import agent_name

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
