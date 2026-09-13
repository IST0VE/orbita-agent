"""
U2: настройки объявлены, а не выведены из текста кода.

Тип настройки раньше снимался регулярным выражением с исходника `config.py`:
«читается `env_bool` — значит галка». Работало, пока исходник доступен и пока
имя переменной стоит константой прямо в вызове. Ограничений такой разбор не
знал вовсе, секретность угадывал по словам в имени, а перечисления жили
отдельной таблицей, которая могла разъехаться с кодом молча.

Теперь это объявлено в `settings_schema`, а разъезд ловит эта проверка: она
находит в `config.py` все чтения окружения и сверяет их со схемой. Разбор
исходника никуда не делся — он переехал туда, где его отказ виден.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pathlib

import pytest

from agent import config as cfg
from agent import settings_io, settings_schema
from costmeter import prices

READERS = {"env_bool": "bool", "env_int": "int", "env_float": "float",
           "env_str": "text", "env_opt": "text"}


def config_modules():
    """Все модули пакета настроек, а не один файл.

    `config` стал пакетом, и `inspect.getsource` по нему отдаёт только
    `__init__.py`. Проверка, читающая один файл, не заметила бы ни одной
    настройки из остальных восьми — и молчала бы об этом.
    """
    found = [cfg]
    for name in dir(cfg):
        item = getattr(cfg, name)
        if getattr(item, "__name__", "").startswith("agent.config."):
            found.append(item)
    package = pathlib.Path(cfg.__file__).parent
    for path in sorted(package.glob("*.py")):
        module = importlib.import_module(f"agent.config.{path.stem}")
        if module not in found:
            found.append(module)
    return found


def read_from_config() -> dict[str, dict]:
    """Как настройки на самом деле читаются — разбором дерева, а не текста."""
    found: dict[str, dict] = {}
    for module in config_modules():
        try:
            tree = ast.parse(inspect.getsource(module))
        except (OSError, TypeError):
            continue
        found.update(_readers(tree))
    return found


def _readers(tree) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        kind = READERS.get(node.func.id)
        if not kind or not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        name = node.args[0].value
        if not isinstance(name, str) or not name.isupper():
            continue
        # Значение бывает выражением (`10 * 1024 * 1024`), поэтому вычисляется,
        # а не читается литералом: сравнивать надо числа, а не их запись.
        limits = {
            keyword.arg: eval(compile(ast.Expression(keyword.value), "<config>", "eval"))  # noqa: S307
            for keyword in node.keywords
            if keyword.arg in {"minimum", "maximum"}
        }
        found[name] = {"kind": kind, **limits}
    return found


CONFIG = read_from_config()


def test_every_setting_read_by_the_code_is_declared():
    missing = sorted(set(CONFIG) - settings_schema.NAMES)

    assert missing == [], "нет в settings_schema: " + ", ".join(missing)


def test_no_declared_setting_is_invented():
    """Схема описывает настройки, а не пожелания: лишнего в ней быть не должно."""
    known = set(CONFIG) | set(prices.ENV_BY_ARTICLE.values())
    extra = sorted(settings_schema.NAMES - known)

    assert extra == [], "объявлено, но не читается: " + ", ".join(extra)


@pytest.mark.parametrize("name", sorted(CONFIG))
def test_the_declared_type_matches_the_reader(name: str):
    declared = settings_schema.BY_NAME[name]
    expected = CONFIG[name]["kind"]

    # Перечисление читается как строка и проверяется по списку: для интерфейса
    # это отдельный тип, для `config.py` — обычный `env_str`.
    assert declared.kind == expected or (declared.kind == "choice" and expected == "text")


@pytest.mark.parametrize("name", sorted(CONFIG))
def test_the_declared_limits_match_the_reader(name: str):
    declared = settings_schema.BY_NAME[name]
    actual = CONFIG[name]

    assert declared.minimum == actual.get("minimum")
    assert declared.maximum == actual.get("maximum")


def test_choices_come_from_the_lists_the_code_checks_against():
    """Список значений один: тот, по которому `config.py` и проверяет."""
    assert settings_schema.BY_NAME["LLM_PROVIDER"].choices is cfg.LLM_PROVIDERS
    assert settings_schema.BY_NAME["PUBLISH_TARGET"].choices is cfg.PUBLISH_TARGETS
    assert settings_schema.BY_NAME["BUDGET_UNKNOWN_PRICE"].choices is cfg.BUDGET_UNKNOWN_PRICE_MODES


def test_an_impossible_declaration_is_refused():
    with pytest.raises(ValueError, match="неизвестный тип"):
        settings_schema.Setting("X", kind="колесо")
    with pytest.raises(ValueError, match="перечисление"):
        settings_schema.Setting("X", kind="choice")


# --------------------------------------------------------------------------
# Контракт с интерфейсом
# --------------------------------------------------------------------------
def test_the_interface_gets_the_declared_type_and_limits():
    fields = {
        field["name"]: field
        for section in settings_io.describe()["sections"]
        for field in section["fields"]
    }

    assert fields["LLM_PROVIDER"]["kind"] == "enum"
    assert fields["LLM_PROVIDER"]["choices"] == list(cfg.LLM_PROVIDERS)
    assert fields["AGENT_INPUT_MAX_CHARS"]["kind"] == "int"
    assert fields["AGENT_INPUT_MAX_CHARS"]["minimum"] == 0
    assert fields["CONFLUENCE_PUBLISH"]["kind"] == "bool"


def test_secrets_are_declared_not_guessed_by_name():
    """`LLM_MAX_TOKENS` — потолок длины ответа, а не ключ."""
    assert settings_schema.BY_NAME["LLM_API_KEY"].secret
    assert settings_schema.BY_NAME["POSTGRES_URI"].secret
    assert not settings_schema.BY_NAME["LLM_MAX_TOKENS"].secret
    assert not settings_schema.BY_NAME["CONFLUENCE_SPACE_KEY"].secret


def test_an_unknown_variable_is_still_shown_as_text_and_not_editable():
    assert settings_schema.kind_of("СОВСЕМ_ЧУЖАЯ") == "text"
    assert settings_io.can_edit("СОВСЕМ_ЧУЖАЯ") is False
    assert settings_io.is_secret("СОВСЕМ_ЧУЖАЯ") is True
