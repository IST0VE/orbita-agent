"""
Декларация настроек приложения: имя, тип, ограничения и секретность.

До этого тип настройки выводился разбором исходника `config.py` регулярным
выражением: «читается `env_bool` — значит галка». Работало, пока исходник
доступен и пока имя переменной стоит константой в самом вызове. Ограничений
такой разбор не знает вовсе, секретность угадывал по словам в имени, а
перечисления жили отдельной таблицей, которая могла молча разъехаться с кодом.

Здесь это сказано прямо и в одном месте. Описания и значения по умолчанию
по-прежнему приезжают из `.env.example`: комментарий над переменной там уже
написан и уже поддерживается, и второе место для него было бы вторым местом,
где он устареет.

Расхождение схемы с кодом ловит тест `test_settings_schema`: он находит в
`config.py` все чтения окружения и сверяет их с таблицей. Молча разъехаться
теперь нечему — разъезд красит проверку в красный.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent import config as cfg

#: Типы полей, которые понимает интерфейс. Общий словарь с UI: значение
#: `kind` уезжает в браузер как есть и выбирает виджет.
KINDS = ("bool", "int", "float", "text", "choice")


@dataclass(frozen=True)
class Setting:
    """Одна настройка: чем она является, а не как её прочитали."""

    name: str
    kind: str
    #: Допустимые значения для `kind="choice"`. Списки живут в `config.py` —
    #: там же, где по ним проверяют, — и здесь только называются.
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    #: Значение не покидает сервер: в браузер уходит маска и признак «задано».
    secret: bool = False

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"{self.name}: неизвестный тип поля {self.kind!r}")
        if (self.kind == "choice") != bool(self.choices):
            raise ValueError(f"{self.name}: перечисление без значений или значения без типа")


SETTINGS: tuple[Setting, ...] = (
    Setting("AGENT_INPUT_DIR", kind="text"),
    Setting("AGENT_INPUT_MAX_CHARS", kind="int", minimum=0),
    Setting("AGENT_NAME", kind="text"),
    Setting("API_ADMIN_TOKEN", kind="text", secret=True),
    Setting("API_MAX_REQUEST_BYTES", kind="int", minimum=1024, maximum=10 * 1024 * 1024),
    Setting("ATLASSIAN_BLOCK_BACKOFF_S", kind="float", minimum=0.0),
    Setting("ATLASSIAN_BLOCK_RETRIES", kind="int", minimum=0, maximum=10),
    Setting("ATLASSIAN_REQUEST_INTERVAL_S", kind="float", minimum=0.0),
    Setting("ATLASSIAN_USER_AGENT", kind="text"),
    Setting("BUDGET_UNKNOWN_PRICE", kind="choice", choices=cfg.BUDGET_UNKNOWN_PRICE_MODES),
    Setting("BUDGET_USD_PER_THREAD", kind="float", minimum=0.0),
    Setting("CHECKPOINT_BACKEND", kind="choice", choices=cfg.CHECKPOINT_BACKENDS),
    Setting("CONFLUENCE_ALLOW_INSECURE_HTTP", kind="bool"),
    Setting("CONFLUENCE_API_PATH", kind="text"),
    Setting("CONFLUENCE_API_VERSION", kind="choice", choices=cfg.API_VERSIONS),
    Setting("CONFLUENCE_BASE_URL", kind="text"),
    Setting("CONFLUENCE_EMAIL", kind="text"),
    Setting("CONFLUENCE_INCLUDE_TOOL_OUTPUT", kind="bool"),
    Setting("CONFLUENCE_MASK_PATTERNS", kind="text"),
    Setting("CONFLUENCE_MASK_REPLACEMENT", kind="text"),
    Setting("CONFLUENCE_MAX_TURNS", kind="int", minimum=0),
    Setting("CONFLUENCE_PAGE_TITLE", kind="text"),
    Setting("CONFLUENCE_PARENT_PAGE_ID", kind="text"),
    Setting("CONFLUENCE_PUBLISH", kind="bool"),
    Setting("CONFLUENCE_PUBLISH_MODE", kind="choice", choices=cfg.PUBLISH_MODES),
    Setting("CONFLUENCE_READ_MAX_CHARS", kind="int", minimum=500),
    Setting("CONFLUENCE_REQUEST_INTERVAL_S", kind="float", minimum=0.0),
    Setting("CONFLUENCE_SEARCH_LIMIT", kind="int", minimum=1, maximum=50),
    Setting("CONFLUENCE_SEARCH_PATH", kind="text"),
    Setting("CONFLUENCE_SPACE_ID", kind="text"),
    Setting("CONFLUENCE_SPACE_KEY", kind="text"),
    Setting("CONFLUENCE_TIMEOUT_S", kind="float", minimum=0.0),
    Setting("CONFLUENCE_TITLE_MAX_LEN", kind="int", minimum=1),
    Setting("CONFLUENCE_TOKEN", kind="text", secret=True),
    Setting("CONFLUENCE_VERSION_MESSAGE", kind="text"),
    Setting("DEMO_ANSWER_CHARS", kind="int", minimum=0),
    Setting("DEMO_THREAD_ID", kind="text"),
    Setting("DIAGRAM_MAX_CHARS", kind="int", minimum=0),
    Setting("JIRA_ALLOW_INSECURE_HTTP", kind="bool"),
    Setting("JIRA_API_PATH", kind="text"),
    Setting("JIRA_BASE_URL", kind="text"),
    Setting("JIRA_COMMENTS_LIMIT", kind="int", minimum=0, maximum=100),
    Setting("JIRA_CREATE_ISSUES", kind="bool"),
    Setting("JIRA_CREATE_REQUIRE_APPROVAL", kind="bool"),
    Setting("JIRA_DEFAULT_ISSUE_TYPE", kind="text"),
    Setting("JIRA_EMAIL", kind="text"),
    Setting("JIRA_EPIC_LINK_FIELD", kind="text"),
    Setting("JIRA_EPIC_TYPE", kind="text"),
    Setting("JIRA_JOURNAL_PATH", kind="text"),
    Setting("JIRA_MAX_ISSUES", kind="int", minimum=1, maximum=200),
    Setting("JIRA_PROJECT_KEY", kind="text", secret=True),
    Setting("JIRA_READ_MAX_CHARS", kind="int", minimum=500),
    Setting("JIRA_REQUEST_INTERVAL_S", kind="float", minimum=0.0),
    Setting("JIRA_SEARCH_LIMIT", kind="int", minimum=1, maximum=50),
    Setting("JIRA_SEARCH_PATH", kind="text"),
    Setting("JIRA_TIMEOUT_S", kind="float", minimum=0.0),
    Setting("JIRA_TOKEN", kind="text", secret=True),
    Setting("KNOWLEDGE_DIR", kind="text"),
    Setting("KNOWLEDGE_ENABLED", kind="bool"),
    Setting("KNOWLEDGE_MIN_SCORE", kind="float", minimum=0.0, maximum=1.0),
    Setting("KNOWLEDGE_TOP_K", kind="int", minimum=0),
    Setting("LLM_API_BASE", kind="text"),
    Setting("LLM_API_KEY", kind="text", secret=True),
    Setting("LLM_MAX_HISTORY_TOKENS", kind="int", minimum=0),
    Setting("LLM_MAX_RETRIES", kind="int", minimum=0),
    Setting("LLM_MAX_TOKENS", kind="int", minimum=1),
    Setting("LLM_MODEL", kind="text"),
    Setting("LLM_PROVIDER", kind="choice", choices=cfg.LLM_PROVIDERS),
    Setting("LLM_REQUESTS_PER_MINUTE", kind="int", minimum=0),
    Setting("LLM_TEMPERATURE", kind="float", minimum=0.0),
    Setting("LLM_TIMEOUT_S", kind="float", minimum=0.0),
    Setting("LLM_TOKENS_PER_MINUTE", kind="int", minimum=0),
    Setting("LLM_TOOL_MODE", kind="choice", choices=cfg.LLM_TOOL_MODES),
    Setting("MEMORY_ACCOUNT_PATTERN", kind="text"),
    Setting("MEMORY_ENABLED", kind="bool"),
    Setting("MEMORY_MAX_FACTS", kind="int", minimum=0),
    Setting("MEMORY_NAMESPACE", kind="text"),
    Setting(
        "PIPELINE_APPROVAL_STAGES", kind="choice", choices=cfg.PIPELINE_APPROVAL_STAGES_MODES
    ),
    Setting("PIPELINE_REQUIRE_APPROVAL", kind="bool"),
    Setting("POSTGRES_URI", kind="text", secret=True),
    Setting("PRICE_CACHE_HIT_PER_MTOK", kind="float", minimum=0.0),
    Setting("PRICE_CACHE_MISS_PER_MTOK", kind="float", minimum=0.0),
    Setting("PRICE_CACHE_WRITE_PER_MTOK", kind="float", minimum=0.0),
    Setting("PRICE_OUTPUT_PER_MTOK", kind="float", minimum=0.0),
    Setting("PUBLISH_DIR", kind="text"),
    Setting("PUBLISH_REQUIRE_APPROVAL", kind="bool"),
    Setting("PUBLISH_TARGET", kind="choice", choices=cfg.PUBLISH_TARGETS),
    Setting("TOOL_TURNS_PER_RUN", kind="int", minimum=0),
)

BY_NAME: dict[str, Setting] = {item.name: item for item in SETTINGS}
NAMES: frozenset[str] = frozenset(BY_NAME)


def kind_of(name: str) -> str:
    """Тип поля. Незнакомая переменная — текст: показать её всё равно надо."""
    item = BY_NAME.get(name)
    return item.kind if item else "text"


def limits_of(name: str) -> dict:
    """Ограничения поля для интерфейса; пусто — их нет."""
    item = BY_NAME.get(name)
    if item is None:
        return {}
    found = {}
    if item.minimum is not None:
        found["minimum"] = item.minimum
    if item.maximum is not None:
        found["maximum"] = item.maximum
    if item.choices:
        found["choices"] = list(item.choices)
    return found
