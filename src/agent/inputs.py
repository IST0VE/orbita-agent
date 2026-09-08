"""
Папки задач: файлы, с которыми агент работает на прогоне.

Одна папка внутри `input/` — одна задача. Оператор кладёт туда файлы,
выбирает папку в интерфейсе и запускает прогон; выбранная папка уезжает в
`configurable.input_dir`, и инструменты агента видят только её.

Ограничение папкой здесь не декоративное. Инструмент, который принимает путь
от модели, — это чтение произвольного файла на машине по тексту из чата;
`resolve()` ниже — единственное место, где путь превращается в файл, и оно
проверяет, что результат остался внутри корня.

Читаются только текстовые файлы и только начало: агент платит за каждый
символ, который увидел, а бинарник в промпте — это деньги за мусор.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from agent import config as cfg
from agent import drawio, publishers

# Что считаем текстом. Список закрытый, а не «всё, кроме картинок»: неизвестное
# расширение лучше не открывать, чем прислать модели гигабайт бинарника.
TEXT_SUFFIXES = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".rst",
        ".log",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".csv",
        ".tsv",
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".sql",
        ".sh",
        ".html",
        ".css",
        ".xml",
    }
)

#: Имя виртуальной папки «готовые документы».
#:
#: Граф работает не только по сырью оператора: Jira-декомпозиция и ревью
#: раскладывают уже написанную аналитику, а она лежит не в `input/`, а в папке
#: публикации. Второго корня для этого не заводится — папка публикации
#: подставляется под тем же выбором задачи, под именем, которое `create_task()`
#: завести не может: символ `@` его чистка выбрасывает, поэтому настоящая
#: папка с таким именем через API не появится.
OUTPUT_TASK = "@published"
OUTPUT_TITLE = "готовые документы"


class InputError(ValueError):
    """Папка или файл задачи недоступны."""


def is_output(task: str) -> bool:
    """Выбрана виртуальная папка публикации, а не задача в `input/`."""
    return task == OUTPUT_TASK or task.startswith(OUTPUT_TASK + "/")


def title_for(task: str) -> str:
    """Как папка называется для человека и для промпта."""
    if not is_output(task):
        return task
    rest = task[len(OUTPUT_TASK) :].lstrip("/")
    return OUTPUT_TITLE if not rest else f"{OUTPUT_TITLE} / {rest}"


def picked_names(chosen: str | Sequence[str] | None) -> list[str]:
    """
    Выбор оператора списком имён — один документ, несколько или ни одного.

    В `configurable.input_file` приезжает либо строка, либо список: комплект
    документации это несколько файлов, и раскладывать его надо целиком.
    Разбирать оба вида в каждом графе значит написать одно и то же четыре раза
    и один раз ошибиться, а ошибка здесь тихая: конвейер, не понявший список,
    прочитает не то, что показал оператор, и backlog будет выглядеть как
    настоящий.

    Пустые имена и повторы выбрасываются: до границы папки они не доживут,
    а в списке выбранного повторятся дважды.
    """
    if isinstance(chosen, str):
        raw: Sequence[object] = [chosen]
    elif isinstance(chosen, (list, tuple)):
        raw = chosen
    else:
        return []
    names: list[str] = []
    for item in raw:
        name = str(item).strip()
        if name and name not in names:
            names.append(name)
    return names


def root() -> Path:
    """Корень с папками задач. Нет — будет создан при первом обращении."""
    path = Path(cfg.input_root())
    return path if path.is_absolute() else Path.cwd() / path


def ensure_root() -> Path:
    path = root()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _is_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES


def _describe_file(path: Path, base: Path) -> dict:
    stat = path.stat()
    return {
        "name": str(path.relative_to(base)).replace("\\", "/"),
        "size": stat.st_size,
        "text": _is_text(path),
        # Схема .drawio роли не текст — сырой XML ей не подставляют, — но и не
        # бинарник: её разбирает граф схем, и в списке файлов она обязана
        # читаться как материал, а не как мусор, лежащий рядом с ним.
        "diagram": drawio.is_diagram(path),
        "suffix": path.suffix.lower().lstrip("."),
    }


def list_tasks() -> list[dict]:
    """
    Папки задач с содержимым. Файлы в корне `input/` показываются как
    безымянная задача — оператор бросил файл мимо папки, и это не повод
    прятать его от интерфейса.
    """
    base = ensure_root()
    tasks: list[dict] = []

    # Symlinks are excluded from listings too: reads already reject a link that resolves
    # outside the task, but even its target size would disclose host metadata.
    loose = sorted(p for p in base.iterdir() if p.is_file() and not p.is_symlink())
    if loose:
        tasks.append(
            {
                "name": ".",
                "title": "без папки",
                "files": [_describe_file(p, base) for p in loose],
            }
        )

    for folder in sorted(p for p in base.iterdir() if p.is_dir() and not p.is_symlink()):
        # Папку, случайно названную как виртуальная, показывать нельзя: под
        # этим именем открывается папка публикации, и её файлы всё равно
        # не открылись бы.
        if folder.name == OUTPUT_TASK:
            continue
        files = sorted(p for p in folder.rglob("*") if p.is_file() and not p.is_symlink())
        tasks.append(
            {
                "name": folder.name,
                "title": folder.name,
                "kind": "input",
                "files": [_describe_file(p, folder) for p in files],
            }
        )

    tasks.extend(_output_tasks())
    return tasks


def output_root() -> Path:
    """Папка публикации — второй источник материалов для прогона."""
    return publishers.directory()


def _output_tasks() -> list[dict]:
    """
    Готовые документы как набор выбираемых папок.

    Файлы непосредственно в корне публикации остаются в пункте «готовые
    документы». Каждый непустой каталог первого уровня становится отдельной
    задачей: так граф можно запустить на уже собранном комплекте документации,
    не подмешивая соседние комплекты. Граница чтения при этом начинается от
    выбранного каталога, а не от всего корня публикации.
    """
    base = output_root()
    if not base.is_dir():
        return []

    tasks: list[dict] = []
    loose = sorted(p for p in base.iterdir() if p.is_file() and not p.is_symlink())
    if loose:
        tasks.append(
            {
                "name": OUTPUT_TASK,
                "title": OUTPUT_TITLE,
                "kind": "output",
                "files": [_describe_file(p, base) for p in loose],
            }
        )

    for folder in sorted(p for p in base.iterdir() if p.is_dir() and not p.is_symlink()):
        files = sorted(p for p in folder.rglob("*") if p.is_file() and not p.is_symlink())
        if not files:
            continue
        task_name = f"{OUTPUT_TASK}/{folder.name}"
        tasks.append(
            {
                "name": task_name,
                "title": title_for(task_name),
                "kind": "output",
                "files": [_describe_file(p, folder) for p in files],
            }
        )
    return tasks


def create_task(name: str) -> dict:
    """Завести пустую папку задачи. Имя чистится до одного безопасного сегмента."""
    safe = "".join(ch for ch in name.strip() if ch.isalnum() or ch in "-_. ").strip()
    if not safe or safe in {".", ".."}:
        raise InputError("имя папки пустое или состоит из служебных символов")
    folder = ensure_root() / safe
    folder.mkdir(parents=True, exist_ok=True)
    return {"name": folder.name, "title": folder.name, "files": []}


def folder(task: str) -> Path:
    """
    Папка задачи внутри корня — единственное место, где имя папки становится
    путём. Проверка на выход за корень стоит после `resolve()`, то есть после
    раскрытия `..` и симлинков: сравнивать строки до нормализации бессмысленно.

    Корней два: `input/` для задач оператора и папка публикации для виртуальной
    задачи «готовые документы». Граница считается от того корня, которому имя
    принадлежит, поэтому `@published/../..` отбивается ровно так же, как `..`
    в имени обычной задачи.
    """
    base = (output_root() if is_output(task) else root()).resolve()
    # Имя виртуальной папки — это её корень, а не сегмент пути внутри него.
    rest = task[len(OUTPUT_TASK) :].lstrip("/") if is_output(task) else task
    path = (base if rest in {"", "."} else base / rest).resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise InputError(f"{task!r}: папка задачи вне корня") from exc
    return path


def resolve(task: str, name: str) -> Path:
    """
    Путь к файлу внутри папки задачи — единственный способ его получить.

    Граница — папка задачи, а не корень `input/`. Соседняя задача это материалы
    чужого треда, и `../другая-задача/файл` обязан отбиваться ровно так же, как
    выход за пределы корня: тред видит свою задачу и только её.

    Проверки `relative_to` стоят после `resolve()`, то есть после раскрытия
    `..` и симлинков: сравнивать строки до нормализации бессмысленно.
    Их две, потому что подставить `..` можно с обеих сторон — и в имя файла,
    которое пришло от модели, и в имя папки, которое пришло из интерфейса.
    """
    task_folder = folder(task)

    target = (task_folder / name).resolve()
    if any(part.lower().startswith(".env") for part in target.relative_to(target.anchor).parts) or target.suffix.lower() in {".env", ".pem", ".key"}:
        raise InputError("чтение файлов с секретами запрещено")
    try:
        target.relative_to(task_folder)
    except ValueError as exc:
        raise InputError(f"{name!r}: путь выходит за пределы папки задачи") from exc

    # Безымянная задача — это файлы, брошенные мимо папки, и `list_tasks()`
    # показывает только их. Читаться должно ровно то, что показано: иначе
    # через неё открылись бы все настоящие задачи разом.
    if task in {"", "."} and target.parent != task_folder:
        raise InputError(f"{name!r}: файл лежит в папке задачи, а она не выбрана")

    if not target.is_file():
        raise InputError(f"{name!r}: файла нет в папке задачи")
    return target


def read(task: str, name: str, max_chars: int | None = None) -> str:
    """
    Содержимое файла, обрезанное по потолку из настроек.

    Обрезка честно помечается в тексте: модель, которая не знает, что файл
    кончился раньше времени, сделает вывод по половине данных и не скажет
    об этом оператору.
    """
    path = resolve(task, name)
    if not _is_text(path):
        raise InputError(f"{name!r}: не текстовый файл, читать нечего")
    return _head(path, max_chars)


def preview(task: str, name: str, max_chars: int | None = None) -> str:
    """
    То же содержимое, но для человека в интерфейсе.

    Отличие от `read()` одно: схема .drawio показывается. Роли она приезжает
    разобранной (`drawio.py`), а не сырым XML, и в `TEXT_SUFFIXES` ей поэтому
    не место — но оператору, который открыл файл в списке, показывать нечего
    только у настоящего бинарника.
    """
    path = resolve(task, name)
    if not _is_text(path) and not drawio.is_diagram(path):
        raise InputError(f"{name!r}: не текстовый файл, показывать нечего")
    return _head(path, max_chars)


def _head(path: Path, max_chars: int | None) -> str:
    """Начало файла по потолку из настроек — общее тело обоих чтений."""
    limit = cfg.input_max_chars() if max_chars is None else max_chars
    if limit < 0:
        raise InputError("потолок чтения файла не может быть отрицательным")

    # `read_text()` сначала загружал файл целиком и только потом обрезал его.
    # Это ограничивало цену промпта, но не память процесса. Храним только
    # разрешённую голову; остаток считаем потоково ради точной отметки.
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        if not limit:
            return handle.read()
        head = handle.read(limit + 1)
        if len(head) <= limit:
            return head
        total = len(head)
        while chunk := handle.read(64 * 1024):
            total += len(chunk)
    return head[:limit] + f"\n\n[…файл обрезан на {limit} символах из {total}]"


# --------------------------------------------------------------------------
# Блок для промпта
#
# Список файлов подставляется в КОНЕЦ вопроса оператора — там же, где справка
# из базы знаний и долгая память, и ровно по той же причине: префикс промпта
# обязан остаться неподвижным, иначе кеш обнуляется на каждом ходе.
#
# В блок уходят только имена и размеры, а не содержимое: файл читается
# инструментом и только тот, который модели действительно понадобился.
# Иначе каждая мегабайтная выгрузка оплачивалась бы на каждом ходе треда.
# --------------------------------------------------------------------------
BLOCK_TITLE = "Файлы задачи"


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} Б"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} КБ"
    return f"{size / 1024 / 1024:.1f} МБ"


def _files(task: str) -> list[Path]:
    """
    Файлы задачи одним списком. Пусто — папка не выбрана, недоступна или пуста.

    Недоступная папка не ошибка на этом уровне: список файлов подставляется
    в каждый ход, и падать из-за того, что оператор ещё не выбрал задачу,
    здесь нечему. Границу корня проверяет `folder()`, симлинки отсеиваются
    здесь же — их целевой размер выдал бы содержимое хоста.
    """
    if not task:
        return []
    try:
        # В отличие от прежней прямой склейки пути, `folder()` раскрывает `..`
        # и симлинки и проверяет границу AGENT_INPUT_DIR.
        base = folder(task)
        if not base.is_dir():
            return []
        return sorted(p for p in base.rglob("*") if p.is_file() and not p.is_symlink())
    except (InputError, OSError):
        return []


def readable_files(task: str) -> list[str]:
    """
    Имена файлов, которые роль действительно сможет прочитать.

    Нужен там, где по содержимому папки принимается решение до вызова модели:
    картинка или архив в папке задачи — не материал, и считать их за него
    значило бы оплатить прогон по пустому входу.
    """
    files = [p for p in _files(task) if _is_text(p)]
    if not files:
        return []
    base = folder(task)
    return [_describe_file(p, base)["name"] for p in files]


def block_for(task: str, chosen: str | Sequence[str] = ()) -> str:
    """
    Список приложенных файлов. Пустая строка — папка не выбрана или пуста.

    `chosen` — файл или файлы, выбранные оператором в интерфейсе. Выбор не
    сокращает список и ничего не запрещает: материал в папке задачи
    разнородный, и роль, которой не дали заглянуть в соседний протокол встречи,
    напишет требования без него и не скажет об этом. Выбор поэтому уезжает
    пометкой — «начни отсюда», — а не фильтром. Там, где документ и есть весь
    вход, выбор трактуется строже: см. `sources.from_files`.
    """
    files = _files(task)
    if not files:
        return ""
    base = folder(task)

    named = {_describe_file(p, base)["name"]: p for p in files}
    wanted = picked_names(chosen)
    picked = [name for name in wanted if name in named]
    lost = [name for name in wanted if name not in named]
    lines = [
        f"- {name} ({_human_size(path.stat().st_size)})"
        + ("" if _is_text(path) else ", не текстовый — не читается")
        + (" ← ВЫБРАН ОПЕРАТОРОМ" if name in picked else "")
        for name, path in named.items()
    ]
    header = (
        f"\n\n---\n{BLOCK_TITLE} «{title_for(task)}» (подставлен автоматически). "
        "Содержимое доступно инструментом read_task_file по имени из списка:\n\n"
    )
    tail = ""
    if picked:
        many = len(picked) > 1
        tail = (
            f"\n\nОператор выбрал источником {'файлы' if many else 'файл'} "
            + ", ".join(picked)
            + f". Начни с {'них' if many else 'него'} и опирайся на "
            f"{'них' if many else 'него'} в первую очередь; остальные файлы папки "
            "читай только тогда, когда выбранного не хватает, и скажи в документе, "
            "что взял из них."
        )
    if lost:
        # Имя пришло, а файла нет: папку сменили, файл удалили, имя набрали
        # руками. Промолчать нельзя — роль решит, что выбора не было, а
        # оператор будет уверен, что он был. Пропасть мог один файл из
        # выбранного комплекта, поэтому говорится про него, а не про весь выбор.
        many = len(lost) > 1
        tail += (
            f"\n\nВНИМАНИЕ: оператор выбрал источником {'файлы' if many else 'файл'} "
            + ", ".join(lost)
            + f", но в папке задачи {'их' if many else 'его'} нет. Работай по "
            "остальным файлам и начни документ с того, что "
            + ("выбранные файлы не найдены." if many else "выбранный файл не найден.")
        )
    return header + "\n".join(lines) + tail
