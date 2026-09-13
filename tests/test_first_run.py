"""
U3: запуск из чистого checkout описан одним путём и без расхождений.

Инструкция, `.env.example`, `docker-compose.yml` и значения по умолчанию
в коде рассказывают об одном и том же. Расходятся они по одному шагу за раз
и молча: кто-то поменял модель по умолчанию, кто-то добавил переменную, а
инструкция осталась прежней — и первым это замечает человек, у которого
ничего не запустилось.

Отдельно проверяется то, чего в инструкции быть не должно: материалы,
которых нет в репозитории, и файлы, которые надо «дописать самому».
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUIDE = (ROOT / "docs" / "GETTING_STARTED.md").read_text(encoding="utf-8")
EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")
DEPLOYMENT = (ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")

yaml = pytest.importorskip("yaml")
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def dotenv_block() -> dict[str, str]:
    """Строки `.env`, которые инструкция просит привести к нужному виду."""
    block = re.search(r"```dotenv\n(.*?)```", GUIDE, re.S)
    assert block, "в инструкции нет блока с настройками первого прогона"
    values = {}
    for line in block.group(1).splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, _, value = line.partition("=")
            values[name.strip()] = value.strip()
    return values


def example_values() -> dict[str, str]:
    values = {}
    for line in EXAMPLE.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, _, value = line.partition("=")
            values[name.strip()] = value.strip()
    return values


GUIDE_ENV = dotenv_block()
EXAMPLE_ENV = example_values()


def test_every_variable_of_the_guide_exists_in_the_example():
    """Инструкция не может просить настроить то, чего в шаблоне нет."""
    missing = sorted(set(GUIDE_ENV) - set(EXAMPLE_ENV))

    assert missing == [], "нет в .env.example: " + ", ".join(missing)


def test_every_variable_of_the_guide_is_declared_in_the_schema():
    from agent import settings_schema

    unknown = sorted(set(GUIDE_ENV) - settings_schema.NAMES)

    assert unknown == [], "не объявлено в settings_schema: " + ", ".join(unknown)


def test_the_guide_names_the_lines_it_asks_to_change():
    """Значения, отличные от шаблона, должны быть названы, а не найдены глазами."""
    differing = sorted(
        name for name, value in GUIDE_ENV.items()
        if name in EXAMPLE_ENV and EXAMPLE_ENV[name] != value and not value.startswith("your-")
    )

    for name in differing:
        assert name in GUIDE, f"инструкция меняет {name}, но не говорит об этом"


def test_the_model_of_the_guide_is_the_default_of_the_code():
    from agent import config as cfg

    assert GUIDE_ENV["LLM_PROVIDER"] == cfg.DEFAULT_PROVIDER
    assert GUIDE_ENV["LLM_MODEL"] == cfg.DEFAULT_MODEL


def test_the_prepared_materials_of_the_guide_exist():
    """«Она уже есть в input/» — проверяемое утверждение."""
    folder = ROOT / "input" / "partial-refund"

    assert folder.is_dir()
    assert sorted(item.name for item in folder.glob("*.md")), "папка задачи пуста"
    assert GUIDE_ENV["AGENT_INPUT_DIR"] == "input"


def test_the_port_of_the_guide_matches_compose():
    published = COMPOSE["services"]["agent"]["ports"][0]

    assert published.endswith(":2024")
    assert "2024" in GUIDE


def test_compose_needs_no_extra_file_the_guide_does_not_mention():
    """Дополнительный compose-файл был обходом, а не сценарием."""
    assert "compose.local" not in DEPLOYMENT
    assert "compose.local" not in GUIDE
    assert not (ROOT / "compose.local.yaml").exists()


@pytest.mark.parametrize("name", ["PUBLISH_DIR", "AGENT_INPUT_DIR", "JIRA_JOURNAL_PATH"])
def test_compose_pins_the_paths_the_deployment_doc_promises(name: str):
    value = COMPOSE["services"]["agent"]["environment"][name]

    assert value in DEPLOYMENT, f"{name}={value} не описан в DEPLOYMENT.md"


def test_the_guide_explains_that_the_file_is_read_at_start():
    assert "перезапустите backend" in GUIDE
    assert "Применено сейчас" in GUIDE


# --------------------------------------------------------------------------
# Что применено сейчас
# --------------------------------------------------------------------------
def test_applied_settings_separate_the_file_from_the_process(monkeypatch: pytest.MonkeyPatch, tmp_path):
    from agent import settings_io

    monkeypatch.setattr(settings_io, "_read_env_file", lambda: {"AGENT_NAME": "из файла"})
    monkeypatch.setenv("AGENT_NAME", "из окружения")

    rows = {row["name"]: row for row in settings_io.applied()["applied"]}

    assert rows["AGENT_NAME"]["value"] == "из окружения"
    assert rows["AGENT_NAME"]["source"] == "окружение"
    assert rows["AGENT_NAME"]["restart_required"] is True


def test_a_matching_file_and_process_need_no_restart(monkeypatch: pytest.MonkeyPatch):
    from agent import settings_io

    monkeypatch.setattr(settings_io, "_read_env_file", lambda: {"AGENT_NAME": "Орбита"})
    monkeypatch.setenv("AGENT_NAME", "Орбита")

    rows = {row["name"]: row for row in settings_io.applied()["applied"]}

    assert rows["AGENT_NAME"]["source"] == "файл"
    assert rows["AGENT_NAME"]["restart_required"] is False


def test_applied_secrets_never_leave_the_server(monkeypatch: pytest.MonkeyPatch):
    from agent import settings_io

    monkeypatch.setattr(settings_io, "_read_env_file", lambda: {})
    monkeypatch.setenv("LLM_API_KEY", "sk-очень-секретный-ключ")

    rows = {row["name"]: row for row in settings_io.applied()["applied"]}

    assert rows["LLM_API_KEY"]["value"] == "********"
    assert "очень-секретный" not in str(rows["LLM_API_KEY"])


def test_the_interface_gets_the_applied_block():
    from agent import settings_io

    described = settings_io.describe()

    assert "applied" in described and "restart_required" in described
    assert described["note"]
