"""Jira: чтение задач и заведение новых."""

from __future__ import annotations

from agent.config.env import (  # noqa: F401
    ConfigError,
    env_bool,
    env_float,
    env_int,
    env_opt,
    env_str,
)

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
