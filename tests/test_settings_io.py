"""Tests for safe .env inspection and updates."""

from pathlib import Path

import pytest

from agent import settings_io


@pytest.fixture
def settings_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".env.example").write_text(
        "AGENT_NAME=Orbita\nAPI_ADMIN_TOKEN=\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "API_ADMIN_TOKEN=top-secret\nMANUAL_SETTING=kept\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_io, "_root", lambda: tmp_path)
    return tmp_path


def test_describe_masks_secrets_and_hides_absolute_path(settings_root: Path) -> None:
    described = settings_io.describe()
    fields = {
        field["name"]: field for section in described["sections"] for field in section["fields"]
    }

    assert described["path"] == ".env"
    assert fields["API_ADMIN_TOKEN"]["value"] == "********"
    assert fields["API_ADMIN_TOKEN"]["filled"] is True


def test_unknown_and_process_settings_are_not_editable(settings_root: Path) -> None:
    """
    Произвольные переменные и подмена окружения процесса запрещены.

    `.env` уезжает в окружение при следующем запуске, поэтому `PYTHONPATH` и
    `LD_PRELOAD` — это выбор кода, который выполнится, а не настройка агента.
    """
    assert not settings_io.can_edit("MANUAL_SETTING")
    assert not settings_io.can_edit("BRAND_NEW_SETTING")
    assert not settings_io.can_edit("PYTHONPATH")
    assert not settings_io.can_edit("LD_PRELOAD")
    assert not settings_io.can_edit("PATH")
    assert not settings_io.can_edit("HTTPS_PROXY")
    assert not settings_io.can_edit("не имя")


def test_save_rejects_process_environment_names(settings_root: Path) -> None:
    with pytest.raises(ValueError, match="PYTHONPATH"):
        settings_io.save({"PYTHONPATH": "/tmp/attack"})
    # И через комментарий тоже: граница одна на оба поля.
    with pytest.raises(ValueError, match="LD_PRELOAD"):
        settings_io.save({}, {"LD_PRELOAD": "подпись"})


def test_save_is_atomic_and_preserves_unrelated_values(settings_root: Path) -> None:
    result = settings_io.save({"AGENT_NAME": "Updated"})

    assert result["path"] == ".env"
    contents = (settings_root / ".env").read_text(encoding="utf-8")
    assert "AGENT_NAME=Updated" in contents
    assert "MANUAL_SETTING=kept" in contents
    assert not list(settings_root.glob(".*.tmp"))


def test_save_round_trips_spaces_hashes_and_quotes(settings_root: Path) -> None:
    settings_io.save({"AGENT_NAME": "O'Brien # 1"})

    described = settings_io.describe()
    fields = {
        field["name"]: field for section in described["sections"] for field in section["fields"]
    }
    assert fields["AGENT_NAME"]["value"] == "O'Brien # 1"


def test_interface_can_add_a_documented_variable_with_a_comment(settings_root: Path) -> None:
    settings_io.save({"LLM_MAX_RETRIES": "42"}, {"LLM_MAX_RETRIES": "зачем это здесь\nвторая строка"})

    contents = (settings_root / ".env").read_text(encoding="utf-8")
    fields = {
        field["name"]: field for section in settings_io.describe()["sections"] for field in section["fields"]
    }

    assert "LLM_MAX_RETRIES=42" in contents
    assert "# зачем это здесь" in contents
    assert fields["LLM_MAX_RETRIES"]["value"] == "42"
    assert fields["LLM_MAX_RETRIES"]["comment"] == "зачем это здесь\nвторая строка"
    assert fields["LLM_MAX_RETRIES"]["editable"] is True


def test_added_variables_share_one_section_header_and_keep_their_own_comments(
    settings_root: Path,
) -> None:
    """
    Заголовок раздела — рамкой, и он не должен прилипнуть к комментарию.

    Одиночный `# Добавлено из интерфейса` попал бы в сплошной блок строк над
    первой переменной и уехал бы обратно как часть её комментария.
    """
    settings_io.save({"LLM_MAX_RETRIES": "1"}, {"LLM_MAX_RETRIES": "первая"})
    settings_io.save({"LLM_MAX_TOKENS": "2"}, {"LLM_MAX_TOKENS": "вторая"})

    contents = (settings_root / ".env").read_text(encoding="utf-8")
    fields = {
        field["name"]: field for section in settings_io.describe()["sections"] for field in section["fields"]
    }

    assert contents.count("# дописано вручную") == 1
    assert fields["LLM_MAX_RETRIES"]["comment"] == "первая"
    assert fields["LLM_MAX_TOKENS"]["comment"] == "вторая"


def test_comment_replaces_the_previous_one_without_eating_the_section_title(
    settings_root: Path,
) -> None:
    (settings_root / ".env").write_text(
        "# ====\n# сеть\n# ====\n# старый текст\nAGENT_NAME=Orbita\n",
        encoding="utf-8",
    )

    settings_io.save({}, {"AGENT_NAME": "новый текст"})

    contents = (settings_root / ".env").read_text(encoding="utf-8")
    assert "# сеть" in contents
    assert "# старый текст" not in contents
    assert "# новый текст" in contents
    assert "AGENT_NAME=Orbita" in contents


def test_empty_comment_removes_the_block(settings_root: Path) -> None:
    settings_io.save({"LLM_MAX_RETRIES": "1"}, {"LLM_MAX_RETRIES": "временная заметка"})

    settings_io.save({}, {"LLM_MAX_RETRIES": ""})

    contents = (settings_root / ".env").read_text(encoding="utf-8")
    assert "временная заметка" not in contents
    assert "LLM_MAX_RETRIES=1" in contents


def test_overlong_comment_is_refused_before_the_file_is_touched(settings_root: Path) -> None:
    before = (settings_root / ".env").read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="длиннее"):
        settings_io.save({"AGENT_NAME": "Updated"}, {"AGENT_NAME": "x" * 5000})

    assert (settings_root / ".env").read_text(encoding="utf-8") == before
