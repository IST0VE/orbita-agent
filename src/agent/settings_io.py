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

В Compose файл другой: процесс получает `.env` хоста через `env_file`, а сам
файл смонтирован отдельно (SETTINGS_ENV_FILE), и правка применяется
пересозданием контейнера (SETTINGS_APPLY_HINT). Без этого страница в образе
искала файлы от `site-packages` и оставалась пустой.
"""

from __future__ import annotations

import errno
import os
import re
import tempfile
import threading
from pathlib import Path

from dotenv import dotenv_values, find_dotenv

from agent import settings_schema

# Переменные, значение которых никогда не покидает сервер.
#
# Сравнение по частям имени, а не подстрокой: `LLM_MAX_TOKENS` — это потолок
# длины ответа, а не ключ, и прятать его под звёздочки значит мешать работать.
_SECRET_WORDS = frozenset({"KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL"})
_SECRET_NAMES = frozenset({"POSTGRES_URI"})
# Имя выглядит как секрет, но им не является: ключ пространства Confluence —
# это `DOCS`, его видно в любой ссылке на wiki; ключ проекта Jira — `ORB` в
# номере каждой задачи.
_PUBLIC_NAMES = frozenset({"CONFLUENCE_SPACE_KEY", "JIRA_PROJECT_KEY"})

# Имена, которые интерфейс не запишет никогда.
#
# `.env` целиком уезжает в окружение процесса при следующем запуске. Поэтому
# `PYTHONPATH`, `LD_PRELOAD` или подменённый `SSL_CERT_FILE` — это не настройка
# агента, а выбор кода, который выполнится, и доверия, с которым он пойдёт
# наружу. Такие переменные правятся руками на сервере, а не по HTTP.
# OIDC_ — кому API верит: сменив issuer из интерфейса, можно впустить себя
# токенами своего Keycloak. USER_SECRETS_ — ключ личных токенов: сменённый из
# интерфейса, он сделал бы нечитаемыми подключения всех пользователей разом.
# Пароли между сервисами (runner, база, Keycloak, Prometheus и Grafana) — часть
# развёртывания, их меняют вместе с сервисом на другом конце, а не одной
# стороной по HTTP.
_RESERVED_PREFIXES = (
    "PYTHON", "LD_", "DYLD_", "NODE_", "OIDC_", "USER_SECRETS_", "KEYCLOAK_", "GRAFANA_"
)
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
        "METRICS_TOKEN",
        "NT_RUNNER_TOKEN",
        "POSTGRES_PASSWORD",
        "POSTGRES_URI",
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
    return bool(_NAME.fullmatch(name)) and name in known_names() and not is_reserved(name)


def named_secret(name: str) -> bool:
    """
    Секрет по одному имени — без сверки со списком известных настроек.

    `is_secret` считает секретом и всякое незнакомое имя: интерфейсу настроек
    показывать незнакомое значение незачем. Журналу сервера (`logbook`) такое
    правило не годится — он вычищает значения из текста записей, и секретом
    оказался бы весь `os.environ`: `HOME=/app` стёр бы каталог из каждой
    трассировки.
    """
    if name in _PUBLIC_NAMES:
        return False
    spec = settings_schema.BY_NAME.get(name)
    return (
        name in _SECRET_NAMES
        or bool(_SECRET_WORDS & set(name.split("_")))
        or bool(spec and spec.secret)
    )


def is_secret(name: str) -> bool:
    """
    Секрет для страницы настроек: по имени, по схеме — и всякое незнакомое имя.

    Слово в имени решает для всех переменных, а не только для объявленных в
    схеме. Раньше условие читалось как «(имя или схема) если в схеме, иначе
    незнакомо» — и POSTGRES_PASSWORD, NT_RUNNER_TOKEN и токены НТ, которых в
    схеме нет, уезжали в браузер открытым текстом.
    """
    if name in _PUBLIC_NAMES:
        return False
    if named_secret(name):
        return True
    return name not in settings_schema.BY_NAME and name not in known_names()


def mask(value: str) -> str:
    """Hide the whole secret without leaking its prefix or length."""
    return "********" if value else ""


def known_names() -> frozenset[str]:
    """Настройки, о которых проект знает: схема плюс описанные в `.env.example`."""
    return frozenset(settings_schema.NAMES) | described_names()


# --------------------------------------------------------------------------
# Разбор .env.example: разделы, описания, значения по умолчанию
# --------------------------------------------------------------------------
_FENCE = re.compile(r"^#\s*=+\s*$")
#: Сколько строк текста может стоять между рамками заголовка раздела.
_HEADER_LINES = 3
_SECTION = re.compile(r"^#\s*(.+?)\s*$")
_ASSIGN = re.compile(r"^(?P<export>export\s+)?(?P<name>[A-Z][A-Z0-9_]*)\s*=\s*(?P<value>.*)$")


def _root() -> Path:
    """Корень проекта: там, где лежит `.env`, иначе рабочая папка с `.env.example`.

    Рабочая папка нужна образу: `.env` в `/app` нет, а пакет установлен в
    `site-packages`, и путь «по структуре» уводил в `/usr/local/lib/python3.12`.
    """
    found = find_dotenv(usecwd=True)
    if found:
        return Path(found).parent
    if (Path.cwd() / ".env.example").exists():
        return Path.cwd()
    return Path(__file__).resolve().parents[2]


def env_path() -> Path:
    """Файл, который правит интерфейс.

    В Compose это `.env` хоста, смонтированный вне `/app`: `langgraph dev`
    загружает `./.env` поверх окружения процесса, и личные `PUBLISH_DIR` или
    `NT_RUNNER_URL` перебили бы пути, которые задаёт Compose.
    """
    explicit = os.environ.get("SETTINGS_ENV_FILE", "").strip()
    return Path(explicit) if explicit else _root() / ".env"


def fixed_names() -> frozenset[str]:
    """Что задаёт само развёртывание (`environment:` в Compose), а не `.env`.

    Их правка в файле не меняет ничего и никогда: `docker compose up -d`
    снова поставит значение из docker-compose.yml. Поле, которое можно
    поменять без последствий, хуже поля, которое поменять нельзя.
    """
    listed = os.environ.get("SETTINGS_FIXED", "")
    return frozenset(name.strip() for name in listed.split(",") if name.strip())


def apply_hint() -> str:
    """Как применить сохранённое: без Docker — перезапуск, в Compose — пересоздание."""
    return os.environ.get("SETTINGS_APPLY_HINT", "").strip() or "перезапустите сервер агента"


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

        # Заголовок раздела: рамка, строки текста, рамка. Строк бывает и две —
        # у НТ под названием стоит пояснение. Читалась только одна, и тогда
        # весь заголовок вместе с рамками прилипал описанием к NT_PROMETHEUS_URL.
        if _FENCE.match(line):
            end = next(
                (j for j in range(i + 2, min(i + 2 + _HEADER_LINES, len(lines)))
                 if _FENCE.match(lines[j])),
                None,
            )
            text = [_SECTION.match(lines[j]) for j in range(i + 1, end)] if end else []
            if text and all(text) and not any(_FENCE.match(lines[j]) for j in range(i + 1, end)):
                section = " ".join(match.group(1) for match in text)
                comments = []
                i = end + 1
                continue

        assign = _ASSIGN.match(line)
        if assign:
            name, default = assign.group("name"), assign.group("value").strip()
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
        comments[assign.group("name")] = "\n".join(block).strip()
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
    """Записать значение так, чтобы python-dotenv и Docker Compose прочли одно и то же.

    `.env` читают двое: агент без Docker (python-dotenv) и Compose через
    `env_file`. Одинарные кавычки у Compose буквальные, а python-dotenv
    раскрывает в них `\\\\` и `\\'`; в двойных Compose подставляет `$`. Прежняя
    запись удваивала `\\` для python-dotenv, и в контейнер JSON с `\\"` приезжал
    сломанным. Значение, которое не записать одинаково, отклоняется — молча
    испортить его хуже.
    """
    if "\n" in value or "\r" in value:
        raise ValueError("значение переменной окружения не может содержать перенос строки")
    if "${" in value or "\x00" in value:
        raise ValueError("подстановка переменных и NUL в значении запрещены")
    if re.fullmatch(r"[A-Za-z0-9_./:@%+?,=-]*", value):
        return value
    if "'" not in value and "\\\\" not in value and not value.endswith("\\"):
        return f"'{value}'"
    if "\\" not in value and "$" not in value:
        return '"' + value.replace('"', '\\"') + '"'
    raise ValueError(
        "значение с апострофом и обратной косой чертой или `$` Docker Compose и "
        "python-dotenv прочтут по-разному — задайте его в .env вручную"
    )


def described_names() -> frozenset[str]:
    """Имена, у которых есть описание в `.env.example`."""
    return frozenset(field["name"] for field in _parse_example())


def applied() -> dict:
    """
    Что применено прямо сейчас — в отличие от того, что лежит в файле.

    Это разные вещи, и разница дорого стоит. `.env` читается один раз при
    старте процесса, поэтому сохранённая через интерфейс настройка начинает
    действовать только после перезапуска. Переменная, заданная в окружении
    процесса (docker compose, systemd, терминал), вообще сильнее файла и не
    меняется правкой файла никогда.

    Пока интерфейс показывал только файл, оба случая выглядели одинаково:
    «я же поменял, а оно работает по-старому».

    Значения секретов наружу не отдаются: только маска и признак «задано».
    """
    file_values = _read_env_file()
    pinned = fixed_names()
    rows = []
    for name in sorted(known_names()):
        process = (os.environ.get(name) or "").strip()
        stored = (file_values.get(name) or "").strip()
        if not process and not stored:
            continue
        secret = is_secret(name)
        rows.append({
            "name": name,
            "value": mask(process) if secret else process,
            "secret": secret,
            # Откуда взялось применённое значение: окружение процесса сильнее
            # файла, и правка файла его не отменит.
            "source": "docker-compose.yml" if name in pinned
                      else "окружение" if process and process != stored
                      else "файл" if stored else "умолчание",
            # Файл разошёлся с процессом: нужен перезапуск, а для значения
            # из окружения — правка там, где оно задано. Закреплённое
            # развёртыванием перезапуском не догнать — и не нужно.
            "restart_required": process != stored and bool(stored) and name not in pinned,
        })
    return {
        "applied": rows,
        "restart_required": any(row["restart_required"] for row in rows),
        "note": (
            "Файл читается при старте процесса. Значения из окружения сильнее файла "
            "и правкой файла не меняются."
        ),
    }


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
    pinned = fixed_names()
    for field in [*described, *extra]:
        name = field["name"]
        # Закреплённое развёртыванием показывается таким, каким применено.
        value = os.environ.get(name, "") if name in pinned else current.get(name, "")
        secret = is_secret(name)
        item = {
            "name": name,
            "description": field["description"],
            "default": field["default"],
            # Тип и ограничения объявлены, а не выведены разбором исходника:
            # см. `settings_schema`. «enum» — историческое имя виджета, схема
            # называет тот же тип «choice».
            "kind": "enum" if settings_schema.kind_of(name) == "choice"
                    else settings_schema.kind_of(name),
            "secret": secret,
            "editable": can_edit(name) and name not in pinned,
            # Почему поле не правится, если причина не «нет в .env.example».
            **({"locked": "задаёт docker-compose.yml"} if name in pinned else {}),
            "comment": written.get(name, ""),
            "filled": bool(value),
            # Секрет наружу не отдаётся никогда: только маска.
            "value": mask(value) if secret else value,
            **settings_schema.limits_of(name),
        }
        sections.setdefault(field["section"], []).append(item)

    return {
        # Абсолютный путь — лишняя информация о машине для HTTP-ответа.
        "path": env_path().name,
        "sections": [{"title": title, "fields": fields} for title, fields in sections.items()],
        # Куда попадёт переменная, заведённая из интерфейса.
        "new_section": _MANUAL_SECTION,
        # Как применить сохранённое: в Compose `restart` не перечитывает env_file.
        "apply": apply_hint(),
        # Что применено прямо сейчас: файл и процесс — разные вещи, и разница
        # между ними и есть ответ на «я же поменял, а оно работает по-старому».
        **applied(),
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
    pinned = sorted(name for name in updates if name in fixed_names())
    if pinned:
        raise ValueError("задаёт docker-compose.yml, правка .env не подействует: " + ", ".join(pinned))
    # Комментарии кодируются до захвата блокировки: слишком длинный текст
    # обязан отказать, а не оставить файл наполовину переписанным.
    encoded = {name: _encode_comment(text) for name, text in texts.items()}

    path = env_path()
    with _SAVE_LOCK:
        raw = path.read_bytes() if path.exists() else b""
        existing = raw.decode("utf-8").splitlines()
        # `.env`, заведённый в Блокноте, — с CRLF. Первое же сохранение из
        # интерфейса не должно переписывать концы всех его строк.
        eol = "\r\n" if b"\r\n" in raw else "\n"

        pending = dict(updates)
        waiting = dict(encoded)
        out: list[str] = []
        # Закрывающая кавычка значения, которое продолжается на следующих
        # строках. Пока она ждёт, строки файла — это части чужого значения,
        # а не присваивания, и трогать их нельзя.
        tail = ""
        for index, line in enumerate(existing):
            if tail:
                out.append(line)
                if tail in line:
                    tail = ""
                continue
            assign = _ASSIGN.match(line.strip())
            name = assign.group("name") if assign else None
            value = assign.group("value") if assign else ""
            quote = value[:1] if value[:1] in ('"', "'") else ""
            if quote and quote not in value[1:]:
                # Многострочное значение переписать построчно нельзя: хвост
                # остался бы мусором. Оставляем как есть — новое значение
                # допишется в конец, а dotenv берёт последнее присваивание.
                tail = quote
                out.append(line)
                continue
            if name is not None and name in encoded:
                # Прежний блок комментария этой переменной уже уехал в `out` —
                # снимаем его оттуда и кладём на его место новый. У повторных
                # присваиваний того же имени блок только снимается: описание
                # переменной живёт над первым из них.
                del out[len(out) - (index - _comment_start(existing, index)) :]
                out.extend(waiting.pop(name, []))
            if name is not None and name in updates:
                # dotenv uses the last assignment. Update every occurrence so
                # an older duplicate cannot override the value just saved.
                prefix = assign.group("export") or "" if assign else ""
                out.append(f"{prefix}{name}={_encode_env_value(updates[name])}")
                pending.pop(name, None)
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

        _write(path, eol.join(out) + eol)
    return {"saved": sorted({*updates, *texts}), "path": path.name, "apply": apply_hint()}


def _write(path: Path, text: str) -> None:
    """Заменить файл атомарно, а смонтированный по отдельности — на месте.

    Атомарная замена: параллельный GET увидит старую или новую версию целиком,
    но не половину оборванной записи. Файл, смонтированный в контейнер сам по
    себе (`.env` хоста в Compose), переименованием не заменить: Linux отвечает
    EBUSY, а папка точки монтирования принадлежит root. Тогда пишем в тот же
    файл, под той же блокировкой, что и чтение перед сохранением.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=path.parent)
    except PermissionError:
        descriptor = None
    if descriptor is not None:
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                stream.write(text)
            try:
                os.replace(temporary, path)
                return
            except OSError as exc:
                if exc.errno not in (errno.EBUSY, errno.EXDEV):
                    raise
        finally:
            temporary.unlink(missing_ok=True)
    with path.open("r+" if path.exists() else "w", encoding="utf-8", newline="") as stream:
        stream.write(text)
        stream.truncate()
