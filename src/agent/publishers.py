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

import difflib
import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

from agent import config as cfg
from agent import confluence, outgoing, render


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

    def location_key(self, title: str) -> str:
        """
        Куда именно ляжет этот заголовок — в сравнимом виде.

        По нему план проверяется на коллизии до первой записи: цель, которая
        не различает два заголовка, перезапишет один документ другим, и узнать
        об этом по четырём файлам вместо пяти уже поздно.
        """

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

    def publish(self, title: str, document: str, *, expected: dict | None = None) -> dict:
        title, document = checked(title, document)
        try:
            kwargs = {"expected": expected} if expected is not None else {}
            return confluence.publish_page(title, document, **kwargs)
        except confluence.ConfluenceError as exc:
            raise PublishError(str(exc)) from exc

    def location_key(self, title: str) -> str:
        """Confluence различает страницы заголовком — он же и ключ upsert."""
        return title

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
            # Идентификатор нужен, чтобы показать diff: тело страницы отдаёт
            # только чтение по id, а поиск по заголовку его не возвращает.
            "page_id": str(existing.get("id") or ""),
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
        """
        Файл этого заголовка. Имя с отпечатком — кроме случая, когда на диске
        уже лежит документ со старым именем и тем же заголовком в первой
        строке: такой обновляется на своём месте. Чужой файл, случайно занявший
        старое имя, не трогается — он остаётся со своим заголовком.
        """
        base = self.directory()
        fresh = base / (slug(title) + self.renderer.extension)
        if fresh.exists():
            return fresh
        old = base / (legacy_slug(title) + self.renderer.extension)
        if old != fresh and old.is_file() and not old.is_symlink():
            if _document_title(old) == title:
                return old
        return fresh

    def location_key(self, title: str) -> str:
        """
        Чем цель различает документы. Регистр сложен намеренно: NTFS и APFS
        считают `Тема.md` и `тема.md` одним файлом, и коллизию надо увидеть
        до записи, а не по одному оставшемуся документу из двух.
        """
        return str(self.path_for(title)).casefold()

    def publish(self, title: str, document: str) -> dict:
        title, document = checked(title, document)
        path = self.path_for(title)
        existed = path.exists()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Заголовок в теле файла: на wiki он часть страницы, а в файле
            # без него остался бы только текст без имени.
            _write_atomic(path, f"# {title}\n\n{document}\n")
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

    def location_key(self, title: str) -> str:
        return title

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

#: Длина отпечатка заголовка в имени файла. Он не для красоты: имя усекается,
#: а различать документы одного треда обязано именно усечённое.
DIGEST_LEN = 10

#: Сколько знаков хвоста сохраняется при усечении. Заголовок этапа выглядит как
#: «Orbita: тема [thread] — 3 Данные», и человеку в папке нужен как раз хвост.
TAIL_ROOM = 28


def digest_of(title: str) -> str:
    """Отпечаток заголовка: им имя файла остаётся уникальным после усечения."""
    return hashlib.sha256((title or "").encode("utf-8")).hexdigest()[:DIGEST_LEN]


def _shorten(flat: str, limit: int) -> str:
    """
    Усечение, сохраняющее хвост.

    Простое `flat[:limit]` отрезало именно то, чем документы одного треда
    различаются: тема в восемьдесят знаков плюс UUID треда съедают имя целиком,
    и суффикс этапа до него не доезжает. Поэтому голова и хвост остаются оба.
    """
    if len(flat) <= limit:
        return flat
    tail = min(TAIL_ROOM, max(0, limit // 3))
    head = limit - tail - 1
    if head <= 0:
        return flat[:limit]
    return flat[:head] + "_" + flat[len(flat) - tail:]


def slug(title: str, limit: int = 120) -> str:
    """
    Заголовок страницы — в имя файла. Кириллица остаётся как есть: имя должно
    читаться человеком, который откроет папку.

    К читаемой части всегда добавляется отпечаток полного заголовка. Без него
    имя файла — это усечённый и очищенный от знаков заголовок, то есть
    отображение с потерями: «Orbita: тема [t] — 1 Требования» и «… — 2 API»
    после усечения совпадают, и пять документов конвейера превращаются в один.
    Отпечаток считается по заголовку целиком, поэтому различает и то, что
    в читаемую часть не поместилось, и то, что стёрла очистка знаков.
    """
    flat = _UNSAFE.sub("_", " ".join((title or "").split())).strip("_.") or "document"
    return f"{_shorten(flat, limit - DIGEST_LEN - 1)}_{digest_of(title)}"


def legacy_slug(title: str, limit: int = 120) -> str:
    """
    Как имя файла считалось до отпечатка.

    Нужно ровно для одного: не бросить уже опубликованные документы. Файл со
    старым именем обновляется на своём месте, если его заголовок совпадает
    с нашим, — и не трогается, если заголовок чужой.
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


def destination(publisher: Publisher) -> dict:
    """
    Куда именно пишет цель прямо сейчас — в сравнимом виде.

    Не «в файл» и не «в Confluence», а папка, адрес, пространство и версия API.
    Одобрение относится к назначению, а не к имени цели: `PUBLISH_DIR`
    и адрес wiki меняются между предпросмотром и записью одинаково легко,
    и с прежним `approved` документ уезжает в другое место.
    """
    if publisher.name == "file":
        return {"directory": str(directory().resolve())}
    if publisher.name == "confluence":
        return {
            "base_url": cfg.confluence_base_url(),
            "space_key": cfg.confluence_space_key(),
            "space_id": cfg.confluence_space_id(),
            "parent_id": cfg.confluence_parent_id(),
            "api_path": cfg.confluence_api_path(),
            "version": cfg.confluence_api_version(),
        }
    return {}


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


def checked(title: str, document: str) -> tuple[str, str]:
    """
    Заголовок и тело, подготовленные к отправке.

    Последняя граница перед сетью и диском: план собирается проверенным
    (`nodes.publish_plan`), но `publish()` вызывают и мимо плана — из резервного
    сохранения, из графа обновления, из чужого кода. Проверка идемпотентна,
    поэтому второй раз она ничего не меняет и ничего не стоит.
    """
    try:
        return outgoing.guard(title, "title"), outgoing.guard(document, "document")
    except outgoing.OutgoingBlocked as exc:
        raise PublishError(str(exc)) from exc


def _write_atomic(path: Path, text: str) -> None:
    """
    Запись через временный файл рядом и `os.replace`.

    Прямой `write_text` открывает документ на усечение и только потом пишет:
    отказ диска посередине оставляет на месте готового документа половину,
    и следующий прогон в режиме `changed` считает эту половину опубликованной.
    `os.replace` в пределах одной папки атомарен и на POSIX, и на Windows.
    """
    handle, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.stem}."[:40], suffix=".part"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


#: Сколько строк различий показывать. Полный diff страницы на сорок килобайт
#: читать никто не будет, а решение принимают по первым расхождениям.
DIFF_LINES = 200


def current_text(publisher: Publisher, title: str, preview: dict) -> str | None:
    """
    Что лежит на месте документа сейчас. None — прочитать не удалось.

    Нужно одному: показать оператору, чем новая версия отличается от той,
    которую она затрёт. «Перезапишет существующую» без этого — предупреждение
    без содержания: перезапись бывает уточнением абзаца и бывает потерей
    чужой работы, и решают их по-разному.
    """
    if preview.get("action") != "update":
        return None
    if publisher.name == "file" and preview.get("path"):
        try:
            body = Path(preview["path"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        # Первая строка файла — заголовок, приписанный публикацией, а последний
        # перевод строки — её же. Снимаем оба: сравнивать надо документы, а не
        # обёртку, в которой они лежат.
        head, _, rest = body.partition("\n\n")
        if not head.startswith("# "):
            return body
        return rest[:-1] if rest.endswith("\n") else rest
    if publisher.name == "confluence" and preview.get("page_id"):
        try:
            page = confluence.read_page_body(str(preview["page_id"]))
        except confluence.ConfluenceError:
            return None
        return page
    return None


def diff_of(before: str | None, after: str) -> dict:
    """
    Различия между тем, что лежит, и тем, что уедет.

    Считается по строкам и обрезается: решение принимают по первым
    расхождениям, а не по сорока килобайтам контекста.
    """
    if before is None:
        return {"available": False, "reason": "прежняя версия не прочитана"}
    lines = list(
        difflib.unified_diff(
            before.splitlines(), after.splitlines(),
            fromfile="сейчас", tofile="после публикации", lineterm="", n=2,
        )
    )
    added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
    shown = lines[:DIFF_LINES]
    if len(lines) > DIFF_LINES:
        shown.append(f"[…показаны первые {DIFF_LINES} строк различий из {len(lines)}]")
    return {
        "available": True,
        "added": added,
        "removed": removed,
        "unchanged": not lines,
        "text": "\n".join(shown),
    }


def collisions(publisher: Publisher, titles: list[str]) -> list[str]:
    """
    Заголовки плана, которые цель не различит между собой.

    Проверяется до первой записи и по всему плану сразу. Пять документов
    конвейера пишутся по одному, и коллизия обнаруживается иначе только постфактум
    — по четырём файлам вместо пяти, причём с содержимым последнего.
    """
    # Цель без `location_key` различает документы заголовком: так ведут себя
    # и Confluence, и подделки в тестах, и чужая цель, написанная до этой
    # проверки. Отсутствие метода — не повод пропустить проверку целиком.
    where = getattr(publisher, "location_key", None) or (lambda title: title)
    seen: dict[str, str] = {}
    clashes: list[str] = []
    for title in titles:
        key = where(title)
        first = seen.get(key)
        if first is None:
            seen[key] = title
            continue
        clashes.append(f"{first!r} и {title!r}")
    return clashes


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
