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
import subprocess
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


@pytest.mark.parametrize("object_id", [None, "0123456789abcdef0123456789abcdef01234567"])
@pytest.mark.parametrize(
    ("path", "token"),
    [
        ("tests/test_api_security.py", "correct-horse-battery-staple"),
        ("tests/test_api_security.py", "test-only-correct-horse-battery-staple"),
        ("tests/test_api_security.py", "test-only-auth-token-with-32-characters"),
        ("tests/test_ui_engine.py", "test-only-auth-token-with-32-characters"),
        ("tests/test_ui_engine.py", "test-only-ui-secret-with-32-characters"),
        ("tests/test_server_security_integration.py", "test-only-integration-token-32-characters"),
    ],
)
def test_bearer_fixtures_require_an_exact_path_and_value(path, token, object_id):
    line = f"Authorization: Bearer {token}"

    assert scan.scan_text(line, path, object_id=object_id) == []
    assert scan.scan_text(line, "tests/other.py", object_id=object_id)
    assert scan.scan_text(line, f"{path}@archive", object_id=object_id)
    assert scan.scan_text(f"{line}-unexpected", path, object_id=object_id)


@pytest.fixture
def history_repo(tmp_path, monkeypatch):
    """Настоящие Git-объекты: подмена scan_text скрыла бы ошибку передачи пути."""
    monkeypatch.setattr(scan, "ROOT", tmp_path)

    def git(*args):
        return subprocess.run(
            [
                "git", "-c", "user.name=Secret scan tests",
                "-c", "user.email=secret-scan@example.invalid",
                "-c", "commit.gpgsign=false",
                "-c", f"core.hooksPath={tmp_path / 'empty-hooks'}", *args,
            ],
            cwd=tmp_path, check=True, capture_output=True, text=True, encoding="utf-8",
        ).stdout.strip()

    git("init", "--quiet")
    return tmp_path, git


def test_history_accepts_current_and_retired_fixtures(history_repo, monkeypatch, capsys):
    root, git = history_repo
    fixtures = {
        "tests/test_api_security.py": [
            "correct-horse-battery-staple",
            "test-only-correct-horse-battery-staple",
            "test-only-auth-token-with-32-characters",
        ],
        "tests/test_ui_engine.py": [
            "test-only-auth-token-with-32-characters", "test-only-ui-secret-with-32-characters",
        ],
        "tests/test_server_security_integration.py": ["test-only-integration-token-32-characters"],
    }
    for name, tokens in fixtures.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(f"Authorization: Bearer {token}" for token in tokens),
                        encoding="utf-8")
    git("add", ".")
    git("commit", "--quiet", "-m", "Synthetic authentication fixtures")
    for name in fixtures:
        (root / name).unlink()
    git("add", "-u")
    git("commit", "--quiet", "-m", "Remove fixtures from the tree")

    monkeypatch.setattr(sys, "argv", ["scan_secrets.py", "--mode", "history"])
    assert scan.main() == 0
    assert capsys.readouterr().out == "секретов не найдено (history)\n"


def test_history_reports_unlisted_tokens_after_deletion(history_repo, monkeypatch, capsys):
    root, git = history_repo
    files = {
        "tests/test_api_security.py": "test-only-unlisted-token-with-32-characters",
        "tests/test_ui_engine.py": "9f2b7c1de4a8069135bbaf27cd3e05",
        "tests/copied_fixture.py": "test-only-auth-token-with-32-characters",
        "tests/test_api_security.py@archive": "correct-horse-battery-staple",
    }
    expected = []
    for name, token in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# Synthetic control\nAuthorization: Bearer {token}\n", encoding="utf-8")
        object_id = git("hash-object", name)
        expected.append(f"{name}@{object_id[:10]}:2: bearer-token")
    git("add", ".")
    git("commit", "--quiet", "-m", "Plant synthetic controls")
    for name in files:
        (root / name).unlink()
    git("add", "-u")
    git("commit", "--quiet", "-m", "Remove controls from the tree")

    assert scan.scan_paths(scan.tracked_files()) == []
    assert sorted(str(item) for item in scan.scan_history()) == sorted(expected)
    monkeypatch.setattr(sys, "argv", ["scan_secrets.py", "--mode", "history"])
    assert scan.main() == 1
    output = capsys.readouterr().out
    for location in expected:
        assert location in output
    for token in files.values():
        assert token not in output


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
