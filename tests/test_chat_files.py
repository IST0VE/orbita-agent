"""Файлы чата: принадлежат треду, и граф получает папку по треду, а не из запроса.

Раньше материалы жили в общих папках `input/`: любой вошедший видел их и мог
запустить прогон по чужой папке, вписав её имя в `configurable.input_dir`.
Здесь проверяется то, на чём держится новое разделение: имя файла не выводит
из папки чата, папку пользователя сервер подставляет сам, публикации и память
раскладываются по владельцу.
"""

from __future__ import annotations

import uuid

import pytest

from agent import chat_files, inputs, memory, publishers, runtime, security
from agent.security import Principal

THREAD = str(uuid.uuid4())
OTHER = str(uuid.uuid4())


@pytest.fixture(autouse=True)
def chat_root(monkeypatch, tmp_path):
    monkeypatch.setenv("CHAT_FILES_DIR", str(tmp_path / "chats"))
    return tmp_path / "chats"


@pytest.fixture
def as_user():
    """Выполнить код от имени пользователя HTTP-запроса."""
    tokens = []

    def enter(principal: Principal):
        tokens.append(security._CURRENT.set(principal))

    yield enter
    while tokens:
        security._CURRENT.reset(tokens.pop())


# --------------------------------------------------------------------------
# Имя файла
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ticket.md", "ticket.md"),
        ("C:\\Users\\me\\Desktop\\встреча.txt", "встреча.txt"),
        ("../../etc/passwd.txt", "passwd.txt"),
        ("a<b>:c?.md", "abc.md"),
        ("схема.DRAWIO", "схема.DRAWIO"),
    ],
)
def test_only_the_last_segment_of_a_name_survives(raw, expected):
    assert chat_files.clean_name(raw) == expected


@pytest.mark.parametrize("raw", ["", ".env", ".hidden.md", "../", "архив.zip", "photo.png", ".md"])
def test_names_that_are_not_readable_files_are_refused(raw):
    with pytest.raises(chat_files.ChatFileError):
        chat_files.clean_name(raw)


# --------------------------------------------------------------------------
# Загрузка и чтение
# --------------------------------------------------------------------------
def test_upload_list_preview_and_remove(chat_root):
    saved = chat_files.save(THREAD, "ticket.md", "# Задача\n\nВернуть позицию.".encode())

    assert saved["name"] == "ticket.md" and saved["replaced"] is False
    assert (chat_root / THREAD / "ticket.md").is_file()
    assert [f["name"] for f in chat_files.listing(THREAD)["files"]] == ["ticket.md"]
    assert "Вернуть позицию" in chat_files.preview(THREAD, "ticket.md")

    again = chat_files.save(THREAD, "ticket.md", b"new")
    assert again["replaced"] is True

    chat_files.remove(THREAD, "ticket.md")
    assert chat_files.listing(THREAD)["files"] == []


def test_windows_1251_text_is_stored_as_utf8(chat_root):
    chat_files.save(THREAD, "встреча.txt", "Решили: возврат частями".encode("cp1251"))

    assert (chat_root / THREAD / "встреча.txt").read_text(encoding="utf-8") == "Решили: возврат частями"


def test_binary_content_and_oversized_files_are_refused(monkeypatch):
    with pytest.raises(chat_files.ChatFileError, match="не текстовый"):
        chat_files.save(THREAD, "data.csv", b"a,b\x00\x01")
    monkeypatch.setenv("CHAT_FILE_MAX_BYTES", "1024")
    with pytest.raises(chat_files.ChatFileError, match="CHAT_FILE_MAX_BYTES"):
        chat_files.save(THREAD, "big.md", b"x" * 2048)
    with pytest.raises(chat_files.ChatFileError, match="пустой"):
        chat_files.save(THREAD, "empty.md", b"  \n")


def test_a_chat_holds_a_bounded_number_of_files(monkeypatch):
    monkeypatch.setattr(chat_files, "MAX_FILES", 2)
    chat_files.save(THREAD, "a.md", b"a")
    chat_files.save(THREAD, "b.md", b"b")
    # Замена существующего — не новый файл.
    chat_files.save(THREAD, "a.md", b"a2")
    with pytest.raises(chat_files.ChatFileError, match="уже 2 файлов"):
        chat_files.save(THREAD, "c.md", b"c")


def test_a_name_cannot_reach_a_neighbouring_chat():
    chat_files.save(OTHER, "secret.md", b"secret")

    with pytest.raises(chat_files.ChatFileError):
        chat_files.preview(THREAD, f"../{OTHER}/secret.md")
    with pytest.raises(chat_files.ChatFileError):
        chat_files.remove(THREAD, f"../{OTHER}/secret.md")


@pytest.mark.parametrize("thread", ["", "..", "not-a-uuid", "../x"])
def test_only_a_thread_id_names_a_chat_folder(thread):
    with pytest.raises(inputs.InputError):
        inputs.folder(f"@chat/{thread}" if thread else "@chat")


def test_drop_removes_the_whole_chat(chat_root):
    chat_files.save(THREAD, "a.md", b"a")

    assert chat_files.drop(THREAD) is True
    assert not (chat_root / THREAD).exists()
    assert chat_files.drop(THREAD) is False


def test_sweep_candidates_are_only_old_thread_folders(chat_root):
    chat_files.save(THREAD, "a.md", b"a")
    (chat_root / "not-a-thread").mkdir()

    assert chat_files.folders() == [THREAD]
    # Свежую папку уборка не трогает: тред мог появиться только что.
    assert chat_files.folders(older_than_s=3600) == []


# --------------------------------------------------------------------------
# Папку прогона задаёт сервер
# --------------------------------------------------------------------------
def run_config(user: str | None, **configurable) -> dict:
    identity = {"thread_id": THREAD}
    if user is not None:
        identity["langgraph_auth_user_id"] = user
    return {"configurable": {**configurable, **identity}}


def test_a_user_always_gets_the_files_of_this_chat():
    chosen = runtime.options(run_config("sub-alice", input_dir="partial-refund", base_dir="@published"))

    assert chosen["input_dir"] == f"@chat/{THREAD}"
    assert chosen["base_dir"] == f"@chat/{THREAD}"


def test_a_user_cannot_name_somebody_elses_chat():
    chosen = runtime.options(run_config("sub-alice", input_dir=f"@chat/{OTHER}"))

    assert chosen["input_dir"] == f"@chat/{THREAD}"


def test_a_user_without_chosen_folder_still_reads_this_chat():
    assert runtime.options(run_config("sub-alice"))["input_dir"] == f"@chat/{THREAD}"


def test_the_admin_token_may_still_name_a_task_folder():
    chosen = runtime.options(run_config(security.SERVICE_SUBJECT, input_dir="partial-refund"))

    assert chosen["input_dir"] == "partial-refund"
    assert runtime.options(run_config(security.SERVICE_SUBJECT, input_dir="@chat"))["input_dir"] == (
        f"@chat/{THREAD}"
    )


def test_a_run_without_a_thread_has_no_chat_files():
    chosen = runtime.options({"configurable": {"input_dir": "@chat"}})

    assert chosen["input_dir"] == ""


def test_the_role_tool_lists_chat_files():
    from agent.tools import list_task_files, read_task_file

    chat_files.save(THREAD, "ticket.md", b"# Ticket")
    config = run_config("sub-alice")

    assert "ticket.md" in list_task_files.invoke({}, config=config)
    assert read_task_file.invoke({"name": "ticket.md"}, config=config) == "# Ticket"
    assert "файлы чата" in inputs.block_for(f"@chat/{THREAD}")


# --------------------------------------------------------------------------
# Библиотека и копирование в чат
# --------------------------------------------------------------------------
def test_examples_are_copied_into_the_chat(chat_root):
    example = inputs.ensure_root() / "partial-refund"
    example.mkdir()
    (example / "ticket.md").write_text("пример", encoding="utf-8")
    (example / "photo.png").write_bytes(b"\x89PNG")

    library = chat_files.library()
    assert library["examples"] == [
        {"name": "partial-refund", "title": "partial-refund", "files": [
            {"name": "ticket.md", "size": 12, "text": True, "diagram": False, "suffix": "md"},
        ]},
    ]
    chat_files.attach(THREAD, "examples", "ticket.md", "partial-refund")

    assert (chat_root / THREAD / "ticket.md").read_text(encoding="utf-8") == "пример"


@pytest.mark.parametrize("example", ["@published", f"@chat/{OTHER}", "..", "missing"])
def test_only_listed_examples_can_be_copied(example):
    chat_files.save(OTHER, "secret.md", b"secret")

    with pytest.raises(chat_files.ChatFileError):
        chat_files.attach(THREAD, "examples", "secret.md", example)


def test_published_documents_are_copied_from_the_users_own_folder(as_user):
    as_user(Principal("sub-alice", "alice"))
    publishers.FilePublisher().publish("Требования", "текст Алисы")
    name = publishers.documents()[0]["name"]

    chat_files.attach(THREAD, "published", name)
    assert "текст Алисы" in chat_files.preview(THREAD, name)

    as_user(Principal("sub-bob", "bob"))
    assert chat_files.library()["published"] == []
    with pytest.raises(chat_files.ChatFileError):
        chat_files.attach(OTHER, "published", name)


# --------------------------------------------------------------------------
# Публикации и память по владельцу
# --------------------------------------------------------------------------
def test_each_user_publishes_into_an_own_folder(as_user):
    root = publishers.root_directory()
    as_user(Principal("sub-alice", "alice"))
    publishers.FilePublisher().publish("Требования", "Алиса")
    as_user(Principal("sub-bob", "bob"))
    publishers.FilePublisher().publish("Требования", "Боб")

    # Одинаковый заголовок у двух пользователей больше не перезаписывает чужой файл.
    alice = next((root / "users" / "sub-alice").glob("*.md")).read_text(encoding="utf-8")
    bob = next((root / "users" / "sub-bob").glob("*.md")).read_text(encoding="utf-8")
    assert "Алиса" in alice and "Боб" in bob
    assert [doc["title"] for doc in publishers.documents()] == ["Требования"]


def test_the_admin_token_keeps_the_root_and_does_not_list_user_folders(as_user):
    as_user(Principal("sub-alice", "alice"))
    publishers.FilePublisher().publish("Личное", "Алиса")
    as_user(security.SERVICE)

    assert publishers.directory() == publishers.root_directory()
    assert publishers.documents() == []
    assert [task["name"] for task in inputs.list_tasks() if task.get("kind") == "output"] == []


def test_an_unusual_subject_becomes_a_digest_folder():
    assert publishers.owner_folder("0b5c-uuid") == "0b5c-uuid"
    assert publishers.owner_folder("../../etc").startswith("u-")
    assert publishers.owner_folder("user@example.com").startswith("u-")


def test_memory_is_private_to_each_user(as_user):
    shared = memory.namespace()
    as_user(Principal("sub-alice", "alice"))
    alice = memory.namespace()
    as_user(Principal("sub-bob", "bob"))

    assert alice != memory.namespace()
    assert shared not in (alice, memory.namespace())


# --------------------------------------------------------------------------
# Отказ диска
# --------------------------------------------------------------------------
def test_a_storage_permission_problem_is_explained_instead_of_a_bare_500(monkeypatch):
    """
    Том `data`, заведённый, когда контейнер работал от root, не даёт процессу
    `orbita` создать `/data/chats`. Раньше это был голый 500 без текста.
    """
    from starlette.testclient import TestClient

    from agent import api

    token = "test-only-auth-token-with-32-characters"
    monkeypatch.setenv("API_ADMIN_TOKEN", token)

    async def owns(*_args, **_kwargs):
        return True

    def forbidden(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied", "/data/chats")

    monkeypatch.setattr(api, "owns_thread", owns)
    monkeypatch.setattr(chat_files, "save", forbidden)
    monkeypatch.setattr(chat_files, "attach", forbidden)
    client = TestClient(api.app, headers={"Authorization": f"Bearer {token}"})

    for response in (
        client.put(f"/api/chats/{THREAD}/files", params={"name": "a.md"}, content=b"a"),
        client.post(f"/api/chats/{THREAD}/attach", json={"source": "published", "name": "a.md"}),
    ):
        assert response.status_code == 503, response.text
        assert response.json()["error_code"] == "chat_storage_forbidden"
        assert "/data/chats" in response.json()["error"]
