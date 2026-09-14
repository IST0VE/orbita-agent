"""
D2: проверка секретов сама должна быть проверяемой.

«Ничего не найдено» доказывает отсутствие находок ровно до тех пор, пока
работают правила распознавания. Сломанные они продолжают радостно ничего
не находить, и проверка превращается в зелёную галочку без содержания.
Поэтому в репозитории лежит контрольная закладка, а у сканера — самопроверка.

Второе требование к сообщению: значения находок не печатаются. Сообщение об
утечке, в котором лежит утёкший ключ, — это второй экземпляр того же секрета,
теперь в логах CI, доступных шире, чем исходный файл.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("scan_secrets", ROOT / "scripts" / "scan_secrets.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["scan_secrets"] = module
    spec.loader.exec_module(module)
    return module


scan = _load()

PLANTED = "tests/fixtures/planted_secret.txt"


def test_the_planted_control_is_detected():
    """Закладка на месте и находится: иначе проверять нечем."""
    text = (ROOT / PLANTED).read_text(encoding="utf-8")

    kinds = {item.kind for item in scan.scan_text(text, PLANTED)}

    assert {"openai-key", "aws-access-key", "github-token", "url-credentials"} <= kinds


def test_the_self_test_passes():
    assert scan.self_test() == 0


def test_a_finding_never_carries_its_value():
    text = "ключ sk-abcdefghijklmnopqrstuvwxyz0123 в конфиге"

    found = scan.scan_text(text, "где-то.py")

    assert [item.kind for item in found] == ["openai-key"]
    printed = str(found[0])
    assert "sk-abcdefghijklmnopqrstuvwxyz0123" not in printed
    assert printed == "где-то.py:1: openai-key"


def test_the_repository_tree_is_clean():
    """Основная проверка: в рабочем дереве секретов нет."""
    found = scan.scan_paths(scan.tracked_files())

    assert [str(item) for item in found] == []


@pytest.mark.parametrize(
    "line",
    [
        "POSTGRES_URI=postgresql://orbita:orbita@localhost:5432/orbita",
        "POSTGRES_URI: postgresql://orbita:${POSTGRES_PASSWORD}@postgres:5432/orbita",
        'url = "https://user:secret@example.org"',
        "смотри https://user:pass@wiki.example.com/rest",
    ],
)
def test_documentation_and_local_defaults_are_not_findings(line: str):
    """Адрес к петле, к сервису compose и к домену для примеров — не утечка."""
    assert scan.scan_text(line, "где-то") == []


@pytest.mark.parametrize(
    "line",
    [
        "https://deploy:hunter2@vault.internal/rest",
        "https://svc:p4ssw0rd@wiki.corp.local/api",
    ],
)
def test_credentials_to_a_real_host_are_findings(line: str):
    assert {item.kind for item in scan.scan_text(line, "где-то")} == {"url-credentials"}


def test_a_fixture_is_allowed_only_in_its_named_file():
    line = 'headers={"authorization": "Bearer test-only-correct-horse-battery-staple"}'

    assert scan.scan_text(line, "tests/test_api_security.py") == []
    assert scan.scan_text(line, "tests/где-то.py")


def test_a_real_looking_bearer_token_is_a_finding():
    line = 'headers={"authorization": "Bearer 9f2b7c1de4a8069135bbaf27cd3e05"}'

    assert {item.kind for item in scan.scan_text(line, "где-то.py")} == {"bearer-token"}


def test_exclusions_are_named_files_with_reasons():
    """Исключение каталога означало бы, что в нём секрет можно спрятать."""
    for name, reason in scan.ALLOWED.items():
        assert (ROOT / name).is_file(), name
        assert not name.endswith("/"), name
        assert reason.strip(), name


def test_the_rules_are_the_ones_used_for_outgoing_documents():
    """Второй набор шаблонов разошёлся бы с первым в первую же правку."""
    from agent import outgoing

    kinds = {kind for kind, _ in scan._rules()}

    assert {kind for kind, _ in outgoing.MASKED} <= kinds
    assert {kind for kind, _ in outgoing.BLOCKED} <= kinds
