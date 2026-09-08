"""
Чтение и запись `.env` из интерфейса.

Настройки живут в файле, а не в базе: `config.py` читает `os.environ`, и
второе место хранения означало бы два разных ответа на вопрос «какая сейчас
модель». Поэтому фронт правит ровно тот файл, который читает агент.

Описания полей не дублируются в вебе, а разбираются из `.env.example`:
комментарий над переменной там уже написан и уже поддерживается. Тип поля
тоже не перечисляется руками — он снимается с исходника `config.py` по тому,
какой функцией переменная читается (`env_bool` — галка, `env_int` — целое).
Добавили переменную в `.env.example` и в `config.py` — она сама появилась в
интерфейсе с правильным виджетом.

Секреты наружу не отдаются вообще: в браузер уходит фиксированная маска `********`
и признак «значение есть». Поле, которого оператор не трогал, приходит назад
пустым и в файле не меняется.

Интерфейс сохраняет только документированные настройки приложения.
Произвольные переменные окружения и управление административным токеном
доступны только оператору на сервере. Комментарии сохраняются над строками.

Имена из `_RESERVED_*` не запишутся никогда: `.env` уезжает в окружение
процесса, поэтому `PYTHONPATH` или `LD_PRELOAD` — это подмена кода, который
выполнится, а не настройка агента.
"""

from __future__ import annotations

import inspect
import os
import re
import tempfile
import threading
from pathlib import Path

from dotenv import dotenv_values, find_dotenv

from agent import config as cfg
from costmeter import prices

# Переменные, значение которых никогда не покидает сервер.
#
# Сравнение по частям имени, а не подстрокой: `LLM_MAX_TOKENS` — это потолок
# длины ответа, а не ключ, и прятать его под звёздочки значит мешать работать.
_SECRET_WORDS = frozenset({"KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL"})
_SECRET_NAMES = frozenset({"POSTGRES_URI"})
# Имя выглядит как секрет, но им не является: ключ пространства Confluence —
# это `DOCS`, его видно в любой ссылке на wiki.
_PUBLIC_NAMES = frozenset({"CONFLUENCE_SPACE_KEY"})

# Значения из фиксированных наборов: списки лежат в config.py, здесь только
# привязка к имени переменной.
_CHOICES: dict[str, tuple[str, ...]] = {
    "LLM_PROVIDER": cfg.LLM_PROVIDERS,
    "LLM_TOOL_MODE": cfg.LLM_TOOL_MODES,
    "PUBLISH_TARGET": cfg.PUBLISH_TARGETS,
    "CHECKPOINT_BACKEND": cfg.CHECKPOINT_BACKENDS,
    "CONFLUENCE_PUBLISH_MODE": cfg.PUBLISH_MODES,
    "CONFLUENCE_API_VERSION": cfg.API_VERSIONS,
}

# Имена, которые интерфейс не запишет никогда.
#
# `.env` целиком уезжает в окружение процесса при следующем запуске. Поэтому
# `PYTHONPATH`, `LD_PRELOAD` или подменённый `SSL_CERT_FILE` — это не настройка
# агента, а выбор кода, который выполнится, и доверия, с которым он пойдёт
# наружу. Такие переменные правятся руками на сервере, а не по HTTP.
_RESERVED_PREFIXES = ("PYTHON", "LD_", "DYLD_", "NODE_")
_RESERVED_NAMES = frozenset(
    {
        "PATH",
        "PATHEXT",
        "HOME",
        "USERPROFILE",
        "SHELL",
        "COMSPEC",
        "TEMP",
        "TMP",
        "TMPDIR",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "API_ADMIN_TOKEN",
        "POSTGRES_PASSWORD",
    }
)
_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_COMMENT_MAX = 2000
#: Раздел для переменных, которых нет в `.env.example`.
_MANUAL_SECTION = "дописано вручную"

_SAVE_LOCK = threading.Lock()


def is_reserved(name: str) -> bool:
    """Имя влияет на то, какой код запустится и кому процесс поверит."""
    return name in _RESERVED_NAMES or name.startswith(_RESERVED_PREFIXES)


def can_edit(name: str) -> bool:
    """Only documented application settings may be persisted over HTTP."""
    allowed = described_names() | _kinds_from_config_source().keys()
    return bool(_NAME.fullmatch(name)) and name in allowed and not is_reserved(name)


def is_secret(name: str) -> bool:
    if name in _PUBLIC_NAMES:
        return False
    return (
        name in _SECRET_NAMES
        or bool(_SECRET_WORDS & set(name.split("_")))
        or name not in (described_names() | _kinds_from_config_source().keys())
    )


def mask(value: str) -> str:
    """Hide the whole secret without leaking its prefix or length."""
    return "********" if value else ""


def _kinds_from_config_source() -> dict[str, str]:
    """
    Тип переменной — по функции, которой её читает `config.py`.

    Разбор исходника, а не таблица в этом файле: таблица разъезжается с
    кодом молча, а здесь достаточно завести переменную в `config.py`.
    """
    source = inspect.getsource(cfg)
    kinds: dict[str, str] = {}
    pattern = r"env_(bool|int|float|str|opt)\(\s*\"([A-Z0-9_]+)\""
    for reader, name in re.findall(pattern, source):
        kinds[name] = {"bool": "bool", "int": "int", "float": "float"}.get(reader, "text")

    # Тарифы читает не config.py, а costmeter: `price_per_mtok()` только
    # спрашивает у него готовый ответ. Имена берём у него же, чтобы список
    # не разъехался при переименовании статьи расхода.
    for name in prices.ENV_BY_ARTICLE.values():
        kinds[name] = "float"
    return kinds


# --------------------------------------------------------------------------
# Разбор .env.example: разделы, описания, значения по умолчанию
# --------------------------------------------------------------------------
_FENCE = re.compile(r"^#\s*=+\s*$")
_SECTION = re.compile(r"^#\s*(.+?)\s*$")
_ASSIGN = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


def _root() -> Path:
    """Корень проекта: там, где лежит `.env` (а если его нет — по структуре)."""
    found = find_dotenv(usecwd=True)
    if found:
        return Path(found).parent
    return Path(__file__).resolve().parents[2]


def env_path() -> Path:
    return _root() / ".env"


def _example_path() -> Path:
    return _root() / ".env.example"


def _parse_example() -> list[dict]:
    """
    Плоский список переменных в порядке файла: раздел, описание, дефолт.

    Файла нет — интерфейс всё равно должен работать: тогда список пустой,
    и наверх уедут только те переменные, что есть в самом `.env`.
    """
    path = _example_path()
    if not path.exists():
        return []

    fields: list[dict] = []
    section = "прочее"
    comments: list[str] = []
    lines = path.read_text(encoding="utf-8").splitlines()

    i = 0
    while i < len(lines):
        line = lines[i]

        # Заголовок раздела — три строки: рамка, текст, рамка.
        if _FENCE.match(line) and i + 2 < len(lines) and _FENCE.match(lines[i + 2]):
            title = _SECTION.match(lines[i + 1])
            if title:
                section = title.group(1)
                comments = []
                i += 3
                continue

        assign = _ASSIGN.match(line)
        if assign:
            name, default = assign.group(1), assign.group(2).strip()
            fields.append(
                {
                    "name": name,
                    "section": section,
                    "default": default,
                    "description": "\n".join(comments).strip(),
                }
            )
            comments = []
            i += 1
            continue

        if line.startswith("#"):
            comments.append(line.lstrip("#").strip())
        elif not line.strip():
            # Пустая строка отделяет комментарий от переменной, к которой он
            # не относится: иначе описание раздела приклеилось бы к первой же.
            comments = []
        i += 1

    return fields


def _manual_section_lines() -> list[str]:
    """
    Заголовок раздела для переменных, заведённых из интерфейса.

    Именно рамкой, а не одиночным `#`: комментарий переменной — это сплошной
    блок строк над ней, и одиночный заголовок прилип бы к первой же из них,
    а потом уехал бы в интерфейс как часть её комментария.
    """
    rule = "# " + "=" * 74
    return [rule, f"# {_MANUAL_SECTION}", rule]


def _has_manual_section(lines: list[str]) -> bool:
    return any(
        _FENCE.match(lines[index - 1].strip())
        and line.strip().lstrip("#").strip() == _MANUAL_SECTION
        for index, line in enumerate(lines)
        if index > 0
    )


def _comment_start(lines: list[str], index: int) -> int:
    """
    Где начинается комментарий, относящийся к переменной на строке `index`.

    Комментарий переменной — это сплошной блок строк с `#` прямо над ней.
    Пустая строка его обрывает: она отделяет чужой текст. Рамка раздела
    (`# =====`) обрывает тоже, иначе правка комментария у первой переменной
    раздела съела бы его заголовок.
    """
    start = index
    while start > 0:
        previous = lines[start - 1].strip()
        if not previous.startswith("#") or _FENCE.match(previous):
            break
        start -= 1
    return start


def _parse_env_comments() -> dict[str, str]:
    """Комментарии над переменными в самом `.env`: их пишет оператор."""
    path = env_path()
    if not path.exists():
        return {}
    lines = path.read_text(encoding="utf-8").splitlines()
    comments: dict[str, str] = {}
    for index, line in enumerate(lines):
        assign = _ASSIGN.match(line.strip())
        if not assign:
            continue
        start = _comment_start(lines, index)
        if start == index:
            continue
        block = [lines[position].strip().lstrip("#").strip() for position in range(start, index)]
        comments[assign.group(1)] = "\n".join(block).strip()
    return comments


def _encode_comment(text: str) -> list[str]:
    """Текст оператора в строки `.env`: каждая под своим `#`."""
    body = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not body:
        return []
    if len(body) > _COMMENT_MAX:
        raise ValueError(f"комментарий длиннее {_COMMENT_MAX} символов")
    return [f"# {line.strip()}".rstrip() for line in body.split("\n")]


def _read_env_file() -> dict[str, str]:
    """Текущие значения из `.env` с корректным разбором кавычек и `#`."""
    path = env_path()
    if not path.exists():
        return {}
    return {
        name: value
        for name, value in dotenv_values(path, interpolate=False).items()
        if value is not None
    }


def _encode_env_value(value: str) -> str:
    """Serialize a value so python-dotenv reads it back without truncation."""
    if "\n" in value or "\r" in value:
        raise ValueError("значение переменной окружения не может содержать перенос строки")
    if "${" in value or "\x00" in value:
        raise ValueError("подстановка переменных и NUL в значении запрещены")
    if re.fullmatch(r"[A-Za-z0-9_./:@%+?,=-]*", value):
        return value
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def described_names() -> frozenset[str]:
    """Имена, у которых есть описание в `.env.example`."""
    return frozenset(field["name"] for field in _parse_example())


def describe() -> dict:
    """
    Что показать в интерфейсе: разделы, поля, текущие значения.

    Секреты уходят наружу маской. Переменные, которых нет в `.env.example`,
    но которые дописали в `.env` руками или через интерфейс, теряться не
    должны — они собираются в отдельный раздел в конце.

    Описание и комментарий — разные вещи и приезжают порознь. Первое написано
    в `.env.example` и принадлежит проекту; второй лежит в `.env` рядом со
    значением, принадлежит этой машине и правится из интерфейса.
    """
    kinds = _kinds_from_config_source()
    current = _read_env_file()
    described = _parse_example()
    known = {f["name"] for f in described}
    written = _parse_env_comments()

    extra = [
        {"name": name, "section": _MANUAL_SECTION, "default": "", "description": ""}
        for name in current
        if name not in known
    ]

    sections: dict[str, list[dict]] = {}
    for field in [*described, *extra]:
        name = field["name"]
        value = current.get(name, "")
        secret = is_secret(name)
        item = {
            "name": name,
            "description": field["description"],
            "default": field["default"],
            "kind": "enum" if name in _CHOICES else kinds.get(name, "text"),
            "secret": secret,
            "editable": can_edit(name),
            "comment": written.get(name, ""),
            "filled": bool(value),
            # Секрет наружу не отдаётся никогда: только маска.
            "value": mask(value) if secret else value,
        }
        if name in _CHOICES:
            item["choices"] = list(_CHOICES[name])
        sections.setdefault(field["section"], []).append(item)

    return {
        # Абсолютный путь — лишняя информация о машине для HTTP-ответа.
        "path": env_path().name,
        "sections": [{"title": title, "fields": fields} for title, fields in sections.items()],
        # Куда попадёт переменная, заведённая из интерфейса.
        "new_section": _MANUAL_SECTION,
    }


def save(updates: dict[str, str], comments: dict[str, str] | None = None) -> dict:
    """
    Записать значения и комментарии в `.env`, сохранив файл как есть.

    Правится правая часть известных строк и блок комментария над ними; порядок,
    разделы и пустые строки остаются на местах — файл человек читает глазами, и
    перегенерация его целиком была бы худшим, что можно сделать.

    Имени, которого в файле ещё нет, здесь заводится новая строка: интерфейс
    умеет добавлять переменные, а не только менять заготовленные. Запрещённые
    имена отбиваются тут же, а не только на границе HTTP: `save()` зовут и из
    кода, и граница обязана стоять в одном месте с записью.

    Маска секрета до этого места не доезжает: её отбивает вызывающая сторона.
    """
    texts = dict(comments or {})
    forbidden = sorted(name for name in {*updates, *texts} if not can_edit(name))
    if forbidden:
        raise ValueError("запрещённые переменные: " + ", ".join(forbidden))
    # Комментарии кодируются до захвата блокировки: слишком длинный текст
    # обязан отказать, а не оставить файл наполовину переписанным.
    encoded = {name: _encode_comment(text) for name, text in texts.items()}

    path = env_path()
    with _SAVE_LOCK:
        existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []

        pending = dict(updates)
        waiting = dict(encoded)
        out: list[str] = []
        for index, line in enumerate(existing):
            assign = _ASSIGN.match(line.strip())
            name = assign.group(1) if assign else None
            if name is not None and name in waiting:
                # Прежний блок комментария этой переменной уже уехал в `out` —
                # снимаем его оттуда и кладём на его место новый.
                del out[len(out) - (index - _comment_start(existing, index)) :]
                out.extend(waiting.pop(name))
            if name is not None and name in pending:
                out.append(f"{name}={_encode_env_value(pending.pop(name))}")
            else:
                out.append(line)

        # Переменной ещё не было в файле — дописываем в конец, под заголовком,
        # чтобы было видно, что её добавил интерфейс, а не человек.
        if pending:
            if out and out[-1].strip():
                out.append("")
            if not _has_manual_section(out):
                out.extend(_manual_section_lines())
            for name, value in pending.items():
                out.extend(waiting.pop(name, []))
                out.append(f"{name}={_encode_env_value(value)}")

        # Замена готового файла атомарна: параллельный GET увидит старую или
        # новую версию целиком, но не половину оборванной записи.
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write("\n".join(out) + "\n")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return {"saved": sorted({*updates, *texts}), "path": path.name}
