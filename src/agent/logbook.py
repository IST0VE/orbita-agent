"""
Журнал сервера для интерфейса: последние записи `logging` в памяти процесса.

До него всё, что сервер знал об ошибке, уезжало в stdout контейнера:
трассировка упавшего узла, повтор запроса к шлюзу модели, отказ Jira. Оператор
видел в интерфейсе одну строку «прогон завершился ошибкой», а причину мог
достать только тот, у кого есть `docker compose logs`, — то есть не он.
Здесь эти записи собираются, чтобы показать их на странице «Журнал» и
переслать разработчику одним отчётом.

Буфер живёт в памяти одного процесса, и этого достаточно: `langgraph dev`
выполняет граф в том же процессе, что и HTTP. Перезапуск его очищает —
долговременный журнал остаётся у Docker, здесь только оперативный.

Колец два, а не одно. Предупреждения и ошибки редки и ценны, информационные
записи часты и дёшевы; в общем кольце сотня рядовых записей вытеснила бы
единственную трассировку, ради которой журнал и открыли. По той же причине
не хранится журнал HTTP-запросов ниже предупреждения: каждый опрос `/info`
и самого журнала — это строка, и о случившемся она не говорит ничего.

Секреты вычищаются при записи, а не при выдаче: то, чего нет в буфере, не
утечёт ни через ручку, ни через отчёт, который оператор перешлёт дальше.
Вычищаются значения переменных, которые названы как секрет, и известные
форматы ключей — те же, что маскирует проверка исходящего текста
(`outgoing.MASKED`).
"""

from __future__ import annotations

import logging
import os
import platform
import re
import threading
import traceback
from collections import deque
from datetime import UTC, datetime
from importlib import metadata
from typing import Any

from agent import config as cfg
from agent import outgoing, settings_io

MASK = "********"

#: Сколько записей держит каждое кольцо.
PROBLEMS = 500
ROUTINE = 1500

#: Нижние границы важности, которые понимает ручка.
LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

_MESSAGE_LIMIT = 4000
_EXCEPTION_LIMIT = 20000
_FIELD_LIMIT = 300
_FIELDS_MAX = 24
#: Короче этого значение секрета не вычищается: `1` или `on` в переменной
#: с TOKEN в имени стёрли бы все единицы журнала, ничего не спрятав.
_SECRET_MIN = 8

# Служебные поля structlog: о случившемся они ничего не говорят.
_NOISE = frozenset(
    {
        "event",
        "exception",
        "exc_info",
        "logger",
        "level",
        "timestamp",
        "thread_name",
        "langgraph_api_version",
        "api_variant",
        "stack",
        "stack_info",
    }
)
# Записи не о работе агента, а о том, как запущен сам сервер. Оператор ничего
# не может с ними сделать, а в журнале ошибок они читаются как поломка.
# - structlog жалуется на свою цепочку обработчиков при каждой трассировке и
#   без фильтра стоял бы рядом с каждой настоящей ошибкой;
# - сервер при каждом старте напоминает о `--allow-blocking`, с которым его
#   запускают Dockerfile и инструкции (почему — комментарий у CMD в Dockerfile).
_IGNORED = (
    ("py.warnings", "Remove `format_exc_info` from your processor chain"),
    ("langgraph_runtime_inmem", "You've set --allow-blocking"),
)
# Атрибуты обычной LogRecord: всё сверх них приехало через `extra=`.
_STANDARD = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {
    "message",
    "asctime",
    "taskName",
}
# Поля, по которым запись привязывается к прогону. Поднимаются из полей записи
# на верхний уровень: по треду журнал фильтруется.
_CONTEXT = ("thread_id", "run_id", "graph_id")

_URL_CREDENTIALS = re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@")
_AUTH_FIELD = re.compile(
    r"(?i)(\b(?:authorization|x-api-key|api[_-]?key|password|passwd)['\"]?\s*[:=]\s*['\"]?"
    r"(?:bearer\s+|basic\s+)?)[^\s'\",}]+"
)
_LATENCY = re.compile(r"\d+(?:\.\d+)?\s?ms\b")


def _secret_values() -> list[str]:
    """
    Значения переменных окружения, названных как секрет.

    Читаются при каждой записи, а не один раз: ключ, заданный после старта
    (тест, горячая правка окружения), иначе прошёл бы в журнал открытым.
    Длинные идут первыми — иначе от ключа, внутри которого лежит другой ключ,
    остался бы хвост.
    """
    found = {
        value.strip()
        for name, value in os.environ.items()
        if len(value.strip()) >= _SECRET_MIN and settings_io.named_secret(name)
    }
    return sorted(found, key=len, reverse=True)


def scrub(text: str, secrets: list[str] | None = None) -> str:
    """
    Текст записи без секретов: значений из окружения и известных форматов ключей.

    `secrets` — уже собранный `_secret_values()`: у записи десяток полей, и
    обходить окружение ради каждого незачем.
    """
    if not text:
        return ""
    for value in _secret_values() if secrets is None else secrets:
        if value in text:
            text = text.replace(value, MASK)
    for _, pattern in outgoing.MASKED:
        text = pattern.sub(MASK, text)
    text = _URL_CREDENTIALS.sub(rf"\g<1>{MASK}@", text)
    return _AUTH_FIELD.sub(rf"\g<1>{MASK}", text)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    # У трассировки ценен конец: там исключение, которое её и оборвало.
    return f"… (обрезано {len(text) - limit} симв.)\n" + text[-limit:]


def _run_context() -> dict[str, str]:
    """
    Тред, прогон и узел, если запись сделана внутри узла графа.

    Записи сервера LangGraph несут `run_id` и `thread_id` сами, а наши модули
    — повтор запроса к модели, очередь шлюза, отказ интеграции — нет. Без
    этой привязки фильтр «только этот тред» отбрасывал бы как раз их. Контекст
    прогона лежит в той же переменной, из которой его берёт сам LangGraph.
    """
    try:
        from langchain_core.runnables.config import var_child_runnable_config
    except ImportError:  # pragma: no cover - langchain_core есть всегда
        return {}
    config = var_child_runnable_config.get() or {}
    meta = config.get("metadata") or {}
    configurable = config.get("configurable") or {}
    found = {key: str(meta.get(key) or configurable.get(key) or "") for key in _CONTEXT}
    found["node"] = str(meta.get("langgraph_node") or "")
    return {key: value for key, value in found.items() if value}


def _entry(record: logging.LogRecord) -> dict[str, Any]:
    """
    Запись журнала в виде, который можно показать и переслать.

    Записей два вида. Сервер LangGraph пишет через structlog, и тогда в
    `record.msg` лежит словарь события: текст в `event`, трассировка уже
    отформатирована в `exception`, поля — рядом. Наши модули пишут обычным
    `logging`: текст собирается из `msg` и `args`, поля приезжают через `extra`.
    """
    payload = record.msg if isinstance(record.msg, dict) else None
    if payload is not None:
        message = str(payload.get("event", ""))
        exception = str(payload.get("exception") or "")
        extra = {key: value for key, value in payload.items() if key not in _NOISE}
    else:
        try:
            message = record.getMessage()
        except Exception:  # несовпадение msg и args не должно терять запись
            message = f"{record.msg!r} {record.args!r}"
        exception = ""
        extra = {
            key: value
            for key, value in vars(record).items()
            if key not in _STANDARD and not key.startswith("_")
        }
    if not exception and record.exc_info:
        exception = "".join(traceback.format_exception(*record.exc_info))
    elif not exception and record.exc_text:
        exception = record.exc_text

    secrets = _secret_values()
    fields: dict[str, str] = {}
    for key, value in extra.items():
        if len(fields) >= _FIELDS_MAX:
            break
        if isinstance(value, (str, int, float, bool)) and value != "":
            fields[str(key)] = scrub(_clip(str(value), _FIELD_LIMIT), secrets)

    context = _run_context()
    entry: dict[str, Any] = {
        "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
        "level": record.levelname,
        "levelno": record.levelno,
        "logger": record.name,
        "message": scrub(_clip(message, _MESSAGE_LIMIT), secrets),
        "exception": scrub(_clip(exception.rstrip(), _EXCEPTION_LIMIT), secrets),
    }
    for key in _CONTEXT:
        entry[key] = fields.pop(key, "") or context.get(key, "")
    entry["node"] = fields.pop("langgraph_node", "") or context.get("node", "")
    entry["fields"] = fields
    return entry


def _same(last: dict[str, Any], entry: dict[str, Any]) -> bool:
    """Повтор той же записи подряд: складывается в счётчик, а не в новую строку."""
    return (
        last["levelno"] == entry["levelno"]
        and last["logger"] == entry["logger"]
        and last["thread_id"] == entry["thread_id"]
        and last["exception"] == entry["exception"]
        and _LATENCY.sub("", last["message"]) == _LATENCY.sub("", entry["message"])
    )


class Logbook(logging.Handler):
    """
    Кольцевой буфер записей: предупреждения и ошибки отдельно от остального.

    У записи два номера. `id` — её место в журнале, он не меняется. `seq` —
    номер последнего изменения: повтор той же записи подряд не добавляет
    строку, а увеличивает счётчик `repeats`, и опрос с `after` должен увидеть
    это изменение так же, как новую запись. Клиент сводит записи по `id`.
    """

    def __init__(self, problems: int = PROBLEMS, routine: int = ROUTINE) -> None:
        super().__init__(level=logging.INFO)
        self._problems: deque[dict[str, Any]] = deque(maxlen=problems)
        self._routine: deque[dict[str, Any]] = deque(maxlen=routine)
        self._sequence = 0
        self._evicted = 0
        self.started_at = datetime.now(UTC).isoformat(timespec="seconds")

    def emit(self, record: logging.LogRecord) -> None:
        # Журнал HTTP-запросов сервера: успешный опрос — не событие.
        if record.name == "asgi" and record.levelno < logging.WARNING:
            return
        try:
            entry = _entry(record)
        except Exception:
            self.handleError(record)
            return
        if any(
            record.name.startswith(name) and text in entry["message"] for name, text in _IGNORED
        ):
            return
        ring = self._problems if record.levelno >= logging.WARNING else self._routine
        # `Handler.handle` уже держит `self.lock` на время `emit`.
        self._sequence += 1
        last = ring[-1] if ring else None
        if last is not None and _same(last, entry):
            last["repeats"] += 1
            last["last_time"] = entry["time"]
            last["seq"] = self._sequence
            return
        if len(ring) == ring.maxlen:
            self._evicted += 1
        entry.update(id=self._sequence, seq=self._sequence, repeats=1, last_time=entry["time"])
        ring.append(entry)

    def snapshot(
        self,
        *,
        after: int = 0,
        level: int = logging.INFO,
        thread_id: str = "",
        limit: int = 500,
    ) -> dict[str, Any]:
        """Записи новее `after`, не ниже `level`, по желанию — одного треда."""
        with self.lock:
            chosen = [
                {key: value for key, value in entry.items() if key != "levelno"}
                for entry in (*self._problems, *self._routine)
                if entry["seq"] > after
                and entry["levelno"] >= level
                and (not thread_id or entry["thread_id"] == thread_id)
            ]
            sequence, evicted = self._sequence, self._evicted
        chosen.sort(key=lambda entry: entry["id"])
        return {
            "records": chosen[-limit:],
            "next": sequence,
            # Новых записей больше, чем вошло в ответ: старшие из них пропущены.
            "truncated": len(chosen) > limit,
            # Сколько записей вытеснено из колец с начала работы процесса.
            "evicted": evicted,
            "capacity": {"problems": self._problems.maxlen, "routine": self._routine.maxlen},
            "started_at": self.started_at,
        }


_INSTALL_LOCK = threading.Lock()


def install() -> Logbook:
    """
    Подключить журнал к корневому логгеру — один раз на процесс.

    Uvicorn настраивает logging до того, как загружает приложение, и заменяет
    обработчики корня. Поэтому подключение происходит при импорте роутов, а
    не раньше: подключённый раньше обработчик был бы снят этой настройкой.
    """
    with _INSTALL_LOCK:
        root = logging.getLogger()
        found = next((handler for handler in root.handlers if isinstance(handler, Logbook)), None)
        if found is None:
            found = Logbook()
            root.addHandler(found)
        return found


def _version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return ""


def server_info() -> dict[str, str]:
    """
    Что нужно знать о сервере тому, кто разбирает отчёт.

    Адреса шлюза модели и интеграций здесь нет намеренно: отчёт уходит
    за пределы стенда, а внутренние имена хостов — не то, что нужно для
    разбора ошибки.
    """
    try:
        provider, model = cfg.llm_provider(), cfg.model_name()
    except Exception as exc:  # неверная конфигурация — тоже сведение для отчёта
        provider, model = "", f"не прочитана: {exc}"
    return {
        "app": _version("orbita-agent"),
        "langgraph": _version("langgraph"),
        "langgraph_api": _version("langgraph-api"),
        "python": platform.python_version(),
        "platform": platform.platform(terse=True),
        "provider": provider,
        "model": scrub(model),
    }
