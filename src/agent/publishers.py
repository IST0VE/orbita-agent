"""
Куда уезжает документ треда: Confluence, файл на диске или никуда.

Граница здесь была чистой с самого начала — нода публикации делает ровно один
вызов, — поэтому цель публикации оказалась протоколом из двух методов, а не
рефакторингом графа. Новая цель (Notion, GitHub Wiki, S3, почта) — это класс
с `missing()` и `publish()` плюс строка в `_PUBLISHERS`.

Выбор — переменная PUBLISH_TARGET:

    auto        Confluence, если он настроен, иначе файл (по умолчанию)
    confluence  только Confluence; не настроен — этап пропускается
    file        всегда на диск, в PUBLISH_DIR
    none        никуда: документ собирается, но остаётся в состоянии треда

`auto` появился ради того, чтобы проект запускался без корпоративной wiki.
Человек без Confluence раньше видел строку «этап пропущен» и не понимал, что
вообще должно было получиться; теперь он получает готовый документ файлом.

Рубильник CONFLUENCE_PUBLISH=0 и режим CONFLUENCE_PUBLISH_MODE остались со
своими историческими именами, но действуют на любую цель: переименовывать
переменные в чужих `.env` дороже, чем объяснить это здесь и в `.env.example`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from agent import config as cfg
from agent import confluence, render


class PublishError(RuntimeError):
    """Публикация не удалась: сеть, права, диск. Тред из-за этого не падает."""


class Publisher(Protocol):
    """Цель публикации: как называется, чем рисует документ и чего ей не хватает."""

    name: str
    renderer: render.Renderer

    def missing(self) -> list[str]:
        """Каких переменных окружения не хватает. Пусто — можно публиковать."""

    def publish(self, title: str, document: str) -> dict:
        """Upsert по заголовку. Ошибку отдаёт как PublishError."""

    def preview(self, title: str) -> dict:
        """
        Что сделает `publish` с этим заголовком, без записи.

        Нужно окну черновиков: «создать» и «перезаписать существующую» — это
        два разных решения, и принимать их по одному и тому же тексту нельзя.
        Ответ всегда есть: недоступная цель отдаёт `unknown` с причиной, а не
        роняет остановку — узнать судьбу страницы приятно, но не обязательно.
        """


class ConfluencePublisher:
    """Корпоративная wiki: та самая цель, ради которой всё писалось."""

    name = "confluence"
    renderer = render.STORAGE

    def missing(self) -> list[str]:
        return confluence.missing_vars()

    def publish(self, title: str, document: str) -> dict:
        try:
            return confluence.publish_page(title, document)
        except confluence.ConfluenceError as exc:
            raise PublishError(str(exc)) from exc

    def preview(self, title: str) -> dict:
        try:
            s = confluence.load_settings()
            existing = confluence.find_page(title, s)
        except confluence.ConfluenceError as exc:
            return {"action": "unknown", "reason": str(exc)}
        if not existing:
            return {"action": "create"}
        return {
            "action": "update",
            "url": confluence.page_url(existing, s),
            "version": (existing.get("version") or {}).get("number"),
        }


class FilePublisher:
    """
    Документ на диск, одна страница — один файл.

    Реализация важнее прочих альтернатив: она снимает главный барьер к запуску
    проекта. Заголовок страницы превращается в имя файла, поэтому upsert по
    заголовку сохраняется — повторный ход треда перезаписывает свой же файл,
    а не плодит новые.
    """

    name = "file"
    renderer = render.MARKDOWN

    def missing(self) -> list[str]:
        return []

    def directory(self) -> Path:
        return directory()

    def path_for(self, title: str) -> Path:
        return self.directory() / (slug(title) + self.renderer.extension)

    def publish(self, title: str, document: str) -> dict:
        path = self.path_for(title)
        existed = path.exists()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Заголовок в теле файла: на wiki он часть страницы, а в файле
            # без него остался бы только текст без имени.
            path.write_text(f"# {title}\n\n{document}\n", encoding="utf-8")
        except OSError as exc:
            raise PublishError(f"не записать {path}: {exc}") from exc

        return {
            "status": "updated" if existed else "created",
            "title": title,
            "path": str(path),
            "url": path.resolve().as_uri(),
        }

    def preview(self, title: str) -> dict:
        path = self.path_for(title)
        if not path.exists():
            return {"action": "create", "path": str(path)}
        return {"action": "update", "path": str(path), "url": path.resolve().as_uri()}


class NullPublisher:
    """
    PUBLISH_TARGET=none: документ собирается и остаётся в состоянии треда.

    Нужен там, где документация треда нужна интерфейсу, а складывать её никуда
    не надо. Нода публикации до `publish()` не доходит — цель отсеивается
    раньше; исключение здесь на случай прямого вызова.
    """

    name = "none"
    renderer = render.STORAGE

    def missing(self) -> list[str]:
        return []

    def publish(self, title: str, document: str) -> dict:
        raise PublishError("PUBLISH_TARGET=none: публиковать некуда")

    def preview(self, title: str) -> dict:
        return {"action": "none", "reason": "PUBLISH_TARGET=none: публиковать некуда"}


_PUBLISHERS: dict[str, Publisher] = {
    "confluence": ConfluencePublisher(),
    "file": FilePublisher(),
    "none": NullPublisher(),
}

_UNSAFE = re.compile(r"[^\w.-]+")


def slug(title: str, limit: int = 120) -> str:
    """
    Заголовок страницы — в имя файла. Кириллица остаётся как есть: имя должно
    читаться человеком, который откроет папку.
    """
    flat = _UNSAFE.sub("_", " ".join((title or "").split())).strip("_.")
    return flat[:limit] or "document"


def directory() -> Path:
    """
    Папка файловой цели. Относительный путь считается от рабочей папки —
    как и корень задач в `inputs.py`, и по той же причине: путь из `.env`
    обязан читаться одинаково у ноды публикации и у HTTP-роута.
    """
    path = Path(cfg.publish_dir()).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def is_enabled() -> bool:
    """
    Рубильник всего этапа публикации.

    Имя переменной историческое (CONFLUENCE_PUBLISH), смысл — общий: 0
    выключает публикацию в любую цель.
    """
    return cfg.confluence_publish()


def resolve() -> str:
    """Какая цель выбрана сейчас; `auto` раскрывается по настройкам Confluence."""
    target = cfg.publish_target()
    if target != "auto":
        return target
    return "file" if confluence.missing_vars() else "confluence"


def current() -> Publisher:
    """Цель публикации на этот ход."""
    return _PUBLISHERS[resolve()]


def named(name: str) -> Publisher:
    """Цель по имени из доверенного описания конвейера."""
    try:
        return _PUBLISHERS[name]
    except KeyError as exc:
        raise PublishError(f"неизвестная цель публикации: {name}") from exc


# --------------------------------------------------------------------------
# Чтение опубликованного
#
# Файловая цель заканчивается на диске, и до сих пор увидеть результат можно
# было только в проводнике: `file://` из вкладки браузер не открывает. Те же
# документы отдаются интерфейсу через API — тем же способом, что и файлы
# задачи в `inputs.py`, и с той же границей по папке.
# --------------------------------------------------------------------------

#: Потолок чтения документа в интерфейс. Страница треда — это десятки
#: килобайт, а в папку публикации можно положить что угодно руками.
READ_LIMIT = 1_000_000


class DocumentError(ValueError):
    """Опубликованный документ недоступен."""


def _document_title(path: Path) -> str:
    """
    Заголовок из первой строки: `FilePublisher.publish` кладёт туда `# {title}`.

    Имя файла для этого не годится — это slug, обрезанный до 120 знаков, и
    в списке из пяти страниц одного треда они различаются последними буквами.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            first = handle.readline(500)
    except OSError:
        return path.stem
    return first.strip().lstrip("#").strip() or path.stem


def documents() -> list[dict]:
    """
    Что лежит в папке публикации, свежее сверху.

    Только расширение файловой цели: рядом оператор может держать и свои
    файлы, но документами треда они от этого не становятся.
    """
    base = directory()
    if not base.is_dir():
        return []
    suffix = _PUBLISHERS["file"].renderer.extension
    found: list[dict] = []
    for path in base.iterdir():
        # Симлинки не показываются по той же причине, что и в списке задач:
        # даже размер цели рассказал бы о файлах вне папки.
        if not path.is_file() or path.is_symlink() or path.suffix.lower() != suffix:
            continue
        stat = path.stat()
        found.append(
            {
                "name": path.name,
                "title": _document_title(path),
                "size": stat.st_size,
                "modified": stat.st_mtime,
            }
        )
    # Имя вторым ключом: страницы одного хода пишутся в один тик часов
    # файловой системы, и без него их порядок задавал бы обход каталога.
    found.sort(key=lambda item: (-item["modified"], item["name"]))
    return found


def resolve_document(name: str) -> Path:
    """
    Имя из запроса — в путь. Единственное место, где это происходит.

    Проверка границы стоит после `resolve()`, то есть после раскрытия `..`
    и симлинков: сравнивать строки до нормализации бессмысленно. Папка при
    этом плоская — одна страница один файл, — поэтому вложенный путь
    отбивается отдельно, а не считается «внутри границы».
    """
    base = directory().resolve()
    target = (base / name).resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise DocumentError(f"{name!r}: путь выходит за папку публикации") from exc
    if target.parent != base or not target.is_file():
        raise DocumentError(f"{name!r}: документа нет в папке публикации")
    return target


def read_document(name: str) -> str:
    """Текст документа для предпросмотра, обрезанный по потолку."""
    path = resolve_document(name)
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        head = handle.read(READ_LIMIT + 1)
    if len(head) <= READ_LIMIT:
        return head
    return head[:READ_LIMIT] + f"\n\n[…документ обрезан на {READ_LIMIT} символах]"
