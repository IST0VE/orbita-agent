"""
Файлы чата: что пользователь загрузил в свой тред.

Раньше материалы жили в общих папках задач `input/`: папку и её файлы видел
каждый вошедший, и любой мог запустить прогон по чужим документам, вписав имя
папки в запрос. Теперь файл загружается в чат и принадлежит его треду — папка
`CHAT_FILES_DIR/<thread_id>`, — а владельца треда уже проверяет сервер
LangGraph (`auth.py`). Отдельных прав на файлы нет и не нужно: чей тред, того
и файлы. Роуты в `api.py` сверяют владельца через `auth.owns_thread` до того,
как дойти сюда, а граф получает папку по треду прогона (`runtime.options`).

Папки задач остались общей библиотекой примеров: оттуда, как и из своих
опубликованных документов, файл копируется в чат (`attach`). Копия, а не
ссылка: чат читает только свою папку, и граница у него одна.

Что принимается. Список расширений закрытый — тот же, что роль умеет
прочитать (`inputs.TEXT_SUFFIXES`), плюс схемы draw.io. Текст хранится в
UTF-8: файл из Блокнота или выгрузка CSV в cp1251 перекодируется при загрузке,
а не превращается при чтении в «кракозябры», за которые модель возьмёт деньги.
Бинарник с текстовым расширением отбивается по нулевым байтам.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unicodedata
from pathlib import Path

from agent import config as cfg
from agent import drawio, inputs, publishers

#: Сколько файлов держит один чат. Не настройка: это защита диска, а не тариф.
MAX_FILES = 100
#: Длина имени файла без расширения.
MAX_NAME = 120
#: Что можно загрузить: то, что роль умеет прочитать, и схемы.
SUFFIXES = frozenset(inputs.TEXT_SUFFIXES | {".drawio"})
#: Откуда копируется файл в чат.
SOURCES = ("examples", "published")
# Символы, которых не бывает в имени файла ни на одной из систем сервера.
_FORBIDDEN = set('<>:"/\\|?*')


class ChatFileError(ValueError):
    """Файл не принят или не найден. Текст уходит пользователю как есть."""


def task(thread_id: str) -> str:
    """Имя папки треда для `inputs`: проверяет id и ничего не создаёт."""
    inputs.chat_folder(thread_id)
    return inputs.chat_task(thread_id)


def folder(thread_id: str) -> Path:
    return inputs.chat_folder(thread_id)


def clean_name(name: str) -> str:
    """
    Имя файла, под которым он ляжет в чат, — или отказ.

    Берётся только последний сегмент: браузер на Windows присылал бы полный
    путь, а `..` в имени — это попытка выйти из папки чата. Имена с точкой в
    начале не принимаются: так называются `.env` и прочие файлы, которые
    чтение отбивает всё равно (`inputs.resolve`).
    """
    raw = unicodedata.normalize("NFC", str(name or ""))
    base = raw.replace("\\", "/").rsplit("/", 1)[-1].strip()
    cleaned = "".join(ch for ch in base if ch not in _FORBIDDEN and ch.isprintable()).strip()
    cleaned = " ".join(cleaned.split())
    if not cleaned or cleaned.startswith("."):
        raise ChatFileError("у файла нет имени или оно начинается с точки")
    path = Path(cleaned)
    suffix = path.suffix.lower()
    if suffix not in SUFFIXES:
        allowed = ", ".join(sorted(SUFFIXES))
        raise ChatFileError(f"{cleaned}: такие файлы не читаются. Можно: {allowed}")
    stem = path.name[: -len(path.suffix)].rstrip(" .")
    if not stem:
        raise ChatFileError(f"{cleaned}: у файла нет имени")
    return stem[:MAX_NAME].rstrip(" .") + path.suffix


def _as_text(name: str, data: bytes) -> bytes:
    """
    Содержимое в UTF-8 — или отказ.

    Сначала UTF-8 (с BOM и без), затем cp1251: другого русского текста из
    Windows не бывает, а Latin-1 «расшифровал» бы что угодно и тем самым
    ничего бы не проверил.
    """
    if b"\x00" in data:
        raise ChatFileError(f"{name}: это не текстовый файл")
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        return text.encode("utf-8")
    raise ChatFileError(f"{name}: текст не в UTF-8 и не в Windows-1251")


def listing(thread_id: str) -> dict:
    """Файлы чата и ограничения — всё, что нужно интерфейсу для списка."""
    return {
        "thread_id": str(thread_id),
        "files": inputs.files_of(task(thread_id)),
        "limits": {
            "max_bytes": cfg.chat_file_max_bytes(),
            "max_files": MAX_FILES,
            "suffixes": sorted(SUFFIXES),
        },
    }


def save(thread_id: str, name: str, data: bytes) -> dict:
    """
    Положить файл в чат. Файл с тем же именем заменяется.

    Замена, а не второй экземпляр рядом: пользователь, загрузивший исправленный
    `ticket.md`, хочет работать по исправленному, а два файла с почти
    одинаковыми именами роль прочитает оба. Что файл заменён, видно по ответу.
    """
    target_name = clean_name(name)
    limit = cfg.chat_file_max_bytes()
    if len(data) > limit:
        raise ChatFileError(f"{target_name}: файл больше {limit} байт (CHAT_FILE_MAX_BYTES)")
    if not data.strip():
        raise ChatFileError(f"{target_name}: файл пустой")
    body = _as_text(target_name, data)

    base = folder(thread_id)
    target = base / target_name
    replaced = target.is_file()
    if not replaced:
        present = sum(1 for p in base.iterdir() if p.is_file()) if base.is_dir() else 0
        if present >= MAX_FILES:
            raise ChatFileError(f"в чате уже {MAX_FILES} файлов: удалите лишние")
    base.mkdir(parents=True, exist_ok=True)
    _write_atomic(target, body)
    described = next(
        (item for item in inputs.files_of(task(thread_id)) if item["name"] == target_name),
        {"name": target_name, "size": len(body)},
    )
    return {**described, "replaced": replaced}


def _write_atomic(path: Path, body: bytes) -> None:
    """Запись через временный файл: оборванная загрузка не оставит половину."""
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=".upload-", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(body)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def preview(thread_id: str, name: str) -> str:
    """Содержимое для просмотра в интерфейсе, по тому же потолку, что у роли."""
    try:
        return inputs.preview(task(thread_id), name)
    except inputs.InputError as exc:
        raise ChatFileError(str(exc)) from exc


def remove(thread_id: str, name: str) -> None:
    try:
        path = inputs.resolve(task(thread_id), name)
    except inputs.InputError as exc:
        raise ChatFileError(str(exc)) from exc
    path.unlink()


def drop(thread_id: str) -> bool:
    """Удалить все файлы чата вместе с папкой. False — файлов не было."""
    base = folder(thread_id)
    if not base.is_dir():
        return False
    shutil.rmtree(base)
    return True


# --------------------------------------------------------------------------
# Откуда ещё берутся файлы
# --------------------------------------------------------------------------
def library() -> dict:
    """
    Что можно скопировать в чат: общие примеры и свои опубликованные документы.

    Примеры — папки задач `input/`, их заводит администратор на сервере, и
    видит их каждый. Опубликованное — только своё: `publishers.directory()`
    уже знает, кто спрашивает.
    """
    examples = [
        {
            "name": item["name"],
            "title": item.get("title") or item["name"],
            "files": [f for f in item.get("files", []) if _acceptable(f["name"])],
        }
        for item in inputs.list_tasks()
        if item.get("kind") != "output"
    ]
    return {
        "examples": [item for item in examples if item["files"]],
        "published": publishers.documents(),
    }


def _acceptable(name: str) -> bool:
    return Path(name).suffix.lower() in SUFFIXES or drawio.is_diagram(Path(name))


def attach(thread_id: str, source: str, name: str, example: str = "") -> dict:
    """
    Скопировать в чат пример или свой опубликованный документ.

    Имя папки примера проверяется по списку примеров, а не только по границе
    корня: `@published` и `@chat` — тоже имена папок для `inputs`, и открыть
    через пример чужой чат или общий корень публикации было бы можно.
    """
    if source == "examples":
        known = {item["name"] for item in library()["examples"]}
        if example not in known:
            raise ChatFileError(f"{example!r}: такого примера нет")
        try:
            path = inputs.resolve(example, name)
        except inputs.InputError as exc:
            raise ChatFileError(str(exc)) from exc
    elif source == "published":
        try:
            path = publishers.resolve_document(name)
        except publishers.DocumentError as exc:
            raise ChatFileError(str(exc)) from exc
    else:
        raise ChatFileError(f"{source!r}: неизвестный источник, есть {', '.join(SOURCES)}")
    if path.stat().st_size > cfg.chat_file_max_bytes():
        raise ChatFileError(f"{path.name}: файл больше CHAT_FILE_MAX_BYTES")
    return save(thread_id, path.name, path.read_bytes())


# --------------------------------------------------------------------------
# Уборка
# --------------------------------------------------------------------------
def folders(older_than_s: float = 0.0) -> list[str]:
    """
    Id тредов, у которых есть папка файлов и которую давно не трогали.

    Нужен уборке: тред, удалённый мимо `/api/chats/{id}` (через SDK, сбросом
    хранилища `langgraph dev`), оставляет папку, которую больше никто не
    откроет. Свежие папки не отдаются: тред мог появиться только что.
    """
    root = inputs.chat_root()
    if not root.is_dir():
        return []
    border = time.time() - older_than_s
    found = []
    for path in root.iterdir():
        if not path.is_dir() or path.is_symlink():
            continue
        try:
            canonical = inputs.chat_folder(path.name).name
        except inputs.InputError:
            continue
        # Порог 0 — без фильтра по возрасту: отметка времени NTFS бывает на
        # доли секунды впереди `time.time()`, и свежая папка иначе пропадала бы.
        if canonical == path.name and (older_than_s <= 0 or path.stat().st_mtime <= border):
            found.append(path.name)
    return sorted(found)
