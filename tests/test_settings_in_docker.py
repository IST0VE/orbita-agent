"""«Настройки» в образе: `.env` хоста смонтирован отдельно, а не лежит в `/app`.

Страница в контейнере была пустой: `.env` в `/app` нет, пакет стоит в
`site-packages`, и поиск `.env.example` уходил в `/usr/local/lib/python3.12`.
Сохранять тоже было некуда — файл внутри контейнера перекрывало окружение
Compose и стирало пересоздание.
"""

from __future__ import annotations

import errno
import os
import re
from pathlib import Path

import pytest
from dotenv import dotenv_values
from starlette.testclient import TestClient

from agent import api, settings_io

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
AGENT = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]["agent"]
TOKEN = "test-only-security-token-with-32-characters"


@pytest.fixture
def image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Раскладка образа: рабочая папка с `.env.example`, `.env` хоста — в другом месте."""
    workdir = tmp_path / "app"
    workdir.mkdir()
    (workdir / ".env.example").write_text(
        "# " + "=" * 20 + "\n# Агент\n# " + "=" * 20 + "\n# Имя в заголовках\nAGENT_NAME=Orbita\n",
        encoding="utf-8",
    )
    host = tmp_path / "host"
    host.mkdir()
    (host / ".env").write_text("AGENT_NAME=Орбита\n", encoding="utf-8")
    monkeypatch.chdir(workdir)
    monkeypatch.setattr(settings_io, "find_dotenv", lambda usecwd=False: "")
    monkeypatch.setenv("SETTINGS_ENV_FILE", str(host / ".env"))
    return host / ".env"


def test_the_image_shows_sections_and_values_of_the_host_file(image: Path):
    described = settings_io.describe()
    fields = {f["name"]: f for section in described["sections"] for f in section["fields"]}

    assert [section["title"] for section in described["sections"]] == ["Агент"]
    assert fields["AGENT_NAME"]["value"] == "Орбита"
    assert fields["AGENT_NAME"]["editable"] is True


def test_saving_writes_the_host_file_and_says_how_to_apply(image: Path, monkeypatch):
    monkeypatch.setenv("SETTINGS_APPLY_HINT", "выполните docker compose up -d")

    result = settings_io.save({"AGENT_NAME": "Новое имя"})

    assert dotenv_values(image)["AGENT_NAME"] == "Новое имя"
    assert result["apply"] == "выполните docker compose up -d"
    assert settings_io.describe()["apply"] == "выполните docker compose up -d"


def test_without_compose_the_hint_is_a_restart(image: Path, monkeypatch):
    monkeypatch.delenv("SETTINGS_APPLY_HINT", raising=False)

    assert settings_io.apply_hint() == "перезапустите сервер агента"


def test_a_file_mounted_on_its_own_is_rewritten_in_place(image: Path, monkeypatch):
    """Переименование поверх смонтированного файла Linux отбивает EBUSY."""
    inode = os.stat(image).st_ino

    def busy(source, target):
        raise OSError(errno.EBUSY, "Device or resource busy")

    monkeypatch.setattr(settings_io.os, "replace", busy)
    settings_io.save({"AGENT_NAME": "На месте"})

    assert dotenv_values(image)["AGENT_NAME"] == "На месте"
    assert os.stat(image).st_ino == inode
    assert [path.name for path in image.parent.iterdir()] == [".env"]


def test_other_write_failures_are_not_hidden(image: Path, monkeypatch):
    def full(source, target):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(settings_io.os, "replace", full)

    with pytest.raises(OSError):
        settings_io.save({"AGENT_NAME": "x"})
    assert image.read_text(encoding="utf-8") == "AGENT_NAME=Орбита\n"


def test_an_unreadable_host_file_is_reported_not_shown_empty(image: Path, monkeypatch):
    monkeypatch.setenv("API_ADMIN_TOKEN", TOKEN)

    def denied():
        raise PermissionError(errno.EACCES, "Permission denied", "/host/.env")

    monkeypatch.setattr(settings_io, "describe", denied)
    response = TestClient(api.app).get("/api/settings", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 503
    assert "/host/.env" in response.json()["error"]


def test_compose_mounts_the_host_env_outside_the_app_folder():
    """`langgraph dev` грузит ./.env поверх окружения — там ему не место."""
    mount = next(v for v in AGENT["volumes"] if isinstance(v, dict) and v["source"] == "./.env")

    assert mount["type"] == "bind"
    assert not mount["target"].startswith("/app")
    # Без этого Docker создал бы на хосте папку `.env` вместо ошибки.
    assert mount["bind"]["create_host_path"] is False
    assert AGENT["environment"]["SETTINGS_ENV_FILE"] == mount["target"]
    assert "docker compose up -d" in AGENT["environment"]["SETTINGS_APPLY_HINT"]
    # Процесс по-прежнему получает значения из env_file: окружение Compose
    # (PUBLISH_DIR и соседи) остаётся сильнее личного .env.
    assert AGENT["env_file"][0]["path"] == ".env"


def test_a_crlf_file_keeps_its_line_endings(image: Path):
    """`.env` из Блокнота: сохранение одного поля не переписывает концы всех строк."""
    image.write_bytes(b"# \xd0\xb8\xd0\xbc\xd1\x8f\r\nAGENT_NAME=A\r\nOTHER=1\r\n")

    settings_io.save({"AGENT_NAME": "B"})

    assert image.read_bytes() == b"# \xd0\xb8\xd0\xbc\xd1\x8f\r\nAGENT_NAME=B\r\nOTHER=1\r\n"


def test_fields_compose_sets_itself_are_locked_and_never_need_a_restart(image: Path, monkeypatch):
    image.write_text("PUBLISH_DIR=published\nAGENT_NAME=Orbita\n", encoding="utf-8")
    (Path.cwd() / ".env.example").write_text("PUBLISH_DIR=published\nAGENT_NAME=Orbita\n", encoding="utf-8")
    monkeypatch.setenv("SETTINGS_FIXED", "PUBLISH_DIR")
    monkeypatch.setenv("PUBLISH_DIR", "/data/published")
    monkeypatch.setenv("AGENT_NAME", "Orbita")

    described = settings_io.describe()
    fields = {f["name"]: f for section in described["sections"] for f in section["fields"]}
    rows = {row["name"]: row for row in described["applied"]}

    assert fields["PUBLISH_DIR"]["editable"] is False
    assert fields["PUBLISH_DIR"]["locked"] == "задаёт docker-compose.yml"
    assert fields["PUBLISH_DIR"]["value"] == "/data/published"
    assert rows["PUBLISH_DIR"]["source"] == "docker-compose.yml"
    assert described["restart_required"] is False
    with pytest.raises(ValueError, match="docker-compose.yml"):
        settings_io.save({"PUBLISH_DIR": "elsewhere"})


def test_the_fixed_list_matches_what_compose_really_pins():
    """
    Закреплено всё, кроме проброса той же переменной (`X: ${X:-}`): такое
    значение приходит из .env. Собранное Compose из других переменных —
    POSTGRES_URI из пароля — закреплено: строку из .env оно не читает.
    """
    environment = AGENT["environment"]
    pinned = {
        name for name, value in environment.items()
        if not re.fullmatch(r"\$\{" + name + r"(:[-?][^}]*)?\}", str(value))
        and not name.startswith("SETTINGS_")
    }

    assert set(environment["SETTINGS_FIXED"].split(",")) == pinned


def compose_reads(encoded: str) -> str:
    """Как `env_file` Compose читает значение: одинарные кавычки буквальные, в двойных — `\\"`."""
    if encoded[:1] == "'":
        return encoded[1:-1]
    if encoded[:1] == '"':
        return encoded[1:-1].replace('\\"', '"')
    return encoded


@pytest.mark.parametrize(
    "value",
    [
        '{"q":"rate(x{status=~\\"5..\\"}[1m])"}',
        r"C:\Users\orbita",
        "O'Brien # 1",
        'say "hi" and it\'s fine',
        "cost $5 per run",
        "plain-value",
    ],
)
def test_values_read_the_same_by_compose_and_python_dotenv(image: Path, value: str):
    settings_io.save({"AGENT_NAME": value})
    encoded = image.read_text(encoding="utf-8").strip().split("=", 1)[1]

    assert dotenv_values(image)["AGENT_NAME"] == value
    assert compose_reads(encoded) == value


@pytest.mark.parametrize("value", [r"it's C:\path", "it's $HOME"])
def test_a_value_the_two_readers_would_split_is_refused(image: Path, value: str):
    with pytest.raises(ValueError, match="вручную"):
        settings_io.save({"AGENT_NAME": value})
    assert dotenv_values(image)["AGENT_NAME"] == "Орбита"
