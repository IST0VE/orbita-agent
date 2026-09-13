"""
Q2: хранение и восстановление — штатные, а не «допишите свой файл».

Пути публикации в Compose были неоднозначны, и неоднозначность стоила данных:
`env_file` перекрывает `ENV` образа, поэтому строка `PUBLISH_DIR=published` из
личного `.env` уводила документы в файловую систему контейнера — туда, где их
стирает пересоздание. Лечилось это дополнительным файлом, который надо было
не забыть, а забывался он молча.

Здесь проверяется сам штатный сценарий: пути заданы там, где их не перебить,
каждое хранилище лежит на томе или на хосте, а треды сервера разработки имеют
своё место — Postgres рядом их не сохраняет.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
DEPLOYMENT = (ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")

#: Что обязано пережить пересоздание контейнера и куда оно должно лечь.
PATHS = {
    "PUBLISH_DIR": "/data/published",
    "AGENT_INPUT_DIR": "/data/input",
    "JIRA_JOURNAL_PATH": "/data/jira-operations.sqlite3",
}


def mounts(service: str) -> dict[str, str]:
    """Точка монтирования → источник."""
    found = {}
    for row in COMPOSE["services"][service].get("volumes", []):
        source, target = row.split(":")[:2]
        found[target] = source
    return found


@pytest.mark.parametrize("name", sorted(PATHS))
def test_storage_paths_are_set_where_a_personal_env_cannot_override_them(name: str):
    """`environment` сильнее `env_file` — поэтому пути стоят именно там."""
    environment = COMPOSE["services"]["agent"]["environment"]

    assert environment[name] == PATHS[name]


@pytest.mark.parametrize("service", ["agent", "demo"])
def test_both_services_write_to_the_same_places(service: str):
    """Демо и сервер не должны расходиться в том, где лежат документы."""
    assert COMPOSE["services"][service]["environment"]["PUBLISH_DIR"] == PATHS["PUBLISH_DIR"]


def test_every_stored_path_lands_on_a_volume_or_the_host():
    where = mounts("agent")

    assert where["/data"] == "data"
    assert where["/data/input"] == "./input"
    # Треды сервера разработки: своим томом, а не внутри контейнера.
    assert where["/app/.langgraph_api"] == "threads"


def test_the_dev_server_threads_have_their_own_volume():
    """Postgres рядом не делает треды `langgraph dev` постоянными."""
    assert "threads" in COMPOSE["volumes"]
    assert COMPOSE["services"]["agent"]["environment"]["CHECKPOINT_BACKEND"] == "postgres"
    assert "PostgreSQL не делает треды `langgraph dev` постоянными" in DEPLOYMENT


def test_the_image_creates_the_directories_it_declares():
    """Том монтируется на готовое место с нужным владельцем."""
    assert "mkdir -p /data/published /data/input /app/.langgraph_api" in DOCKERFILE
    assert "chown -R orbita:orbita /app /data" in DOCKERFILE


def test_the_supported_flow_needs_no_extra_compose_file():
    """Дополнительный файл был обходом неоднозначности, а не сценарием."""
    assert "compose.local.yaml" not in DEPLOYMENT
    assert not (ROOT / "compose.local.yaml").exists()


def test_backup_and_restore_are_documented_with_permissions():
    assert "## Резервная копия и восстановление" in DEPLOYMENT
    assert "pg_dump" in DEPLOYMENT
    assert "threads.tar.gz" in DEPLOYMENT
    # Восстановление без проверки владельца ломает публикацию молча.
    assert "Права доступа" in DEPLOYMENT
    assert "ls -ln /data" in DEPLOYMENT


def test_the_journal_and_documents_are_not_the_same_backup():
    """Три независимых набора данных, и копия одного не заменяет другой."""
    assert "дамп базы не содержит документов" in DEPLOYMENT
