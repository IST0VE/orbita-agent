"""
Своя модель у каждого пользователя: подключение и модель из настроек.

Подключения задаёт администратор в `.env` (основное и слоты LLM_ALT1..3_*,
см. `config/llm.py`), а человек на странице «Моя модель» выбирает одно из них
и модель в нём. Ключ и адрес остаются на сервере: в браузер уходят название
подключения и список моделей, а назад приходят только их имена. Вписать свой
адрес нельзя — с ним уехал бы ключ сервера.

Выбор действует со следующего обращения к модели, без перезапуска: `config`
спрашивает его здесь при каждом вызове (`cfg.set_chooser`), а сохранение
сбрасывает кеш этого пользователя сразу. Другие процессы сервера увидят его
не позже чем через `_CACHE_S`.

Чей выбор брать, решает `credentials.current_subject()`: внутри прогона —
владелец прогона, в HTTP-запросе — вошедший, в скрипте и тесте — никто, и
тогда действует модель сервера (LLM_MODEL), как до появления выбора.

Хранение — таблица `user_preferences` в Postgres (`db.py`). Без базы выбирать
нечем: страница так и говорит, а прогоны идут на модели сервера. База
настроена, но не отвечает, — прогон отказывает, а не уходит молча на чужую
модель: человек выбрал модель сам, и другая отвечала бы ему по чужому тарифу.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from urllib.parse import urlsplit

import httpx

from agent import config as cfg
from agent import credentials, db

_LOG = logging.getLogger(__name__)

#: Под этим именем выбор лежит в `user_preferences`.
_NAME = "llm"
#: Сколько секунд выбор живёт в памяти процесса. Свой процесс сбрасывает его
#: при сохранении сразу; соседний — по истечении.
_CACHE_S = 15.0
#: Список моделей шлюза меняется редко, а спрашивать его на каждом открытии
#: страницы — лишний запрос с ключом сервера.
_MODELS_CACHE_S = 300.0
_HTTP_TIMEOUT_S = 10.0
#: Проверка модели — один короткий ответ. Шлюз, который не ответил «ок» за
#: минуту, не ответит и на документ.
_CHECK_TIMEOUT_S = 60.0
_MAX_ERROR = 300

#: Модели шлюза, с которыми не разговаривают: эмбеддинги, реранкеры, речь, OCR.
#: Шлюз отдаёт их тем же списком, а прогон на них упадёт на первом же вызове.
_NOT_CHAT = ("embed", "bge-", "rerank", "whisper", "tts", "ocr", "moderation", "clip")

#: Где провайдер держит API, если у подключения адреса нет.
_DEFAULT_BASES = {
    "openai": ("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    "deepseek": ("DEEPSEEK_API_BASE", "https://api.deepseek.com"),
    "anthropic": ("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
}
_VENDOR_KEYS = {
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


class ChoiceError(ValueError):
    """Выбор не принять: такого подключения или модели нет."""


class ChoiceUnavailable(RuntimeError):
    """Выбирать нечем: базы нет или она не отвечает. Текст — для человека."""


# --------------------------------------------------------------------------
# Хранение
# --------------------------------------------------------------------------
class PostgresRows:
    """Строки `user_preferences`. Тесты подменяют `rows`."""

    def load(self, subject: str) -> str | None:
        with db.connection() as conn:
            found = conn.execute(
                "SELECT value FROM user_preferences WHERE subject = %s AND name = %s",
                (subject, _NAME),
            ).fetchone()
        return found[0] if found else None

    def write(self, subject: str, value: str | None) -> None:
        with db.connection() as conn:
            if value is None:
                conn.execute(
                    "DELETE FROM user_preferences WHERE subject = %s AND name = %s",
                    (subject, _NAME),
                )
                return
            conn.execute(
                "INSERT INTO user_preferences (subject, name, value) VALUES (%s, %s, %s)"
                " ON CONFLICT (subject, name) DO UPDATE SET value = EXCLUDED.value,"
                " updated_at = now()",
                (subject, _NAME, value),
            )


rows = PostgresRows()
_cache: dict[str, tuple[float, tuple[str, str] | None]] = {}
_cache_lock = threading.Lock()


def unavailable() -> str | None:
    """Почему выбирать модель нельзя; None — можно."""
    if not cfg.postgres_configured():
        return "база Orbita не настроена (POSTGRES_URI): все работают на модели сервера"
    return None


def _parse(raw: str | None) -> tuple[str, str] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    endpoint, model = value.get("endpoint"), value.get("model")
    if not isinstance(endpoint, str) or not endpoint or not isinstance(model, str):
        return None
    return endpoint, model


def chosen(subject: str) -> tuple[str, str] | None:
    """Выбор пользователя `(подключение, модель)`; не выбирал — None."""
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(subject)
        if hit and hit[0] > now:
            return hit[1]
    try:
        value = _parse(rows.load(subject))
    except db.DatabaseUnavailable as exc:
        raise ChoiceUnavailable(f"выбранная модель недоступна: {exc}") from exc
    with _cache_lock:
        _cache[subject] = (now + _CACHE_S, value)
    return value


def forget_cache(subject: str | None = None) -> None:
    with _cache_lock:
        if subject is None:
            _cache.clear()
        else:
            _cache.pop(subject, None)


def _current() -> tuple[str, str] | None:
    """Выбор того, кто сейчас работает — для `cfg.llm_choice()`."""
    if unavailable():
        return None
    subject = credentials.current_subject()
    if not subject:
        return None
    try:
        return chosen(subject)
    except ChoiceUnavailable as exc:
        raise cfg.ConfigError(str(exc)) from exc


cfg.set_chooser(_current)


# --------------------------------------------------------------------------
# Список моделей подключения
# --------------------------------------------------------------------------
_models_cache: dict[tuple, tuple[float, tuple[str, ...], str | None]] = {}
_models_lock = threading.Lock()


def _api_base(endpoint: cfg.Endpoint) -> str:
    if endpoint.base_url:
        return endpoint.base_url.rstrip("/")
    variable, fallback = _DEFAULT_BASES[endpoint.provider]
    return (cfg.env_opt(variable) or fallback).rstrip("/")


def _api_key(endpoint: cfg.Endpoint) -> str:
    return endpoint.api_key or cfg.env_opt(_VENDOR_KEYS[endpoint.provider]) or ""


def _chat_models(names: list[str]) -> tuple[str, ...]:
    kept = [name for name in names if not any(mark in name.lower() for mark in _NOT_CHAT)]
    return tuple(sorted(dict.fromkeys(kept), key=str.lower))


def _fetch_models(endpoint: cfg.Endpoint) -> tuple[str, ...]:
    """`GET …/models` подключения — тот же адрес и тот же ключ, что у прогона."""
    base = _api_base(endpoint)
    key = _api_key(endpoint)
    if endpoint.provider == "anthropic":
        url = base + ("/models" if base.endswith("/v1") else "/v1/models")
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    else:
        url = base + "/models"
        headers = {"authorization": f"Bearer {key}"} if key else {}
    response = httpx.get(url, headers=headers, timeout=_HTTP_TIMEOUT_S, follow_redirects=False)
    response.raise_for_status()
    payload = response.json()
    items = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ValueError("ответ без списка моделей")
    names = [str(item.get("id")) for item in items if isinstance(item, dict) and item.get("id")]
    return _chat_models(names)


def _describe_error(exc: Exception) -> str:
    """Отказ шлюза словами — без адреса и ключа: страницу видит не только администратор."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"шлюз ответил HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return f"шлюз не ответил за {_HTTP_TIMEOUT_S:.0f} с"
    if isinstance(exc, httpx.HTTPError):
        return "шлюз недоступен"
    return str(exc)[:_MAX_ERROR] or type(exc).__name__


def models(endpoint: cfg.Endpoint, *, refresh: bool = False) -> tuple[tuple[str, ...], str, str | None]:
    """
    Модели подключения: `(список, откуда, ошибка)`.

    Объявленный администратором список (LLM_MODELS, LLM_ALTn_MODELS) главнее:
    его и показываем. Не объявлен — спрашиваем у шлюза. Шлюз не ответил —
    остаётся модель по умолчанию, а ошибка показывается рядом.
    """
    if endpoint.models:
        return endpoint.models, "list", None
    # Ключ кеша — всё, от чего зависит ответ: сменили адрес или ключ — список новый.
    key = (endpoint.id, endpoint.provider, _api_base(endpoint), hash(_api_key(endpoint)))
    now = time.monotonic()
    with _models_lock:
        hit = _models_cache.get(key)
        if hit and hit[0] > now and not refresh:
            found, error = hit[1], hit[2]
            return _with_default(endpoint, found), "gateway" if found else "default", error
    try:
        found, error = _fetch_models(endpoint), None
    except (httpx.HTTPError, ValueError) as exc:
        found, error = (), _describe_error(exc)
        _LOG.warning("список моделей подключения %s не получен: %s", endpoint.id, error)
    with _models_lock:
        _models_cache[key] = (now + _MODELS_CACHE_S, found, error)
    return _with_default(endpoint, found), "gateway" if found else "default", error


def _with_default(endpoint: cfg.Endpoint, found: tuple[str, ...]) -> tuple[str, ...]:
    if endpoint.default_model and endpoint.default_model not in found:
        return (endpoint.default_model, *found)
    return found


# --------------------------------------------------------------------------
# Для страницы «Моя модель»
# --------------------------------------------------------------------------
def _endpoint_row(endpoint: cfg.Endpoint, *, refresh: bool = False) -> dict:
    listed, source, error = models(endpoint, refresh=refresh)
    return {
        "id": endpoint.id,
        "title": endpoint.title,
        "provider": endpoint.provider,
        "default_model": endpoint.default_model,
        "models": list(listed),
        # list — объявил администратор, gateway — ответил шлюз, default — только
        # модель по умолчанию: шлюз списка не дал.
        "source": source,
        "error": error,
    }


def describe(subject: str, *, refresh: bool = False) -> dict:
    """Подключения, их модели, свой выбор и то, что действует сейчас."""
    reason = unavailable()
    endpoints: list[dict] = []
    problems: list[str] = []
    try:
        configured = cfg.llm_endpoints()
    except cfg.ConfigError as exc:
        configured = []
        problems.append(str(exc))
    for endpoint in configured:
        endpoints.append(_endpoint_row(endpoint, refresh=refresh))
    own = None
    if not reason:
        try:
            own = chosen(subject)
        except ChoiceUnavailable as exc:
            reason = str(exc)
    default = {"endpoint": cfg.MAIN_ENDPOINT, "model": cfg.default_model_name()}
    current = default
    if own:
        endpoint_id, model = own
        if any(row["id"] == endpoint_id for row in endpoints):
            current = {"endpoint": endpoint_id, "model": model}
        else:
            problems.append(
                f"выбранное подключение {endpoint_id!r} больше не настроено на сервере — "
                "выберите модель заново"
            )
    return {
        "unavailable": reason,
        "endpoints": endpoints,
        "choice": {"endpoint": own[0], "model": own[1]} if own else None,
        "default": default,
        "current": current,
        "problems": problems,
    }


def validate(endpoint_id: str, model: str) -> cfg.Endpoint:
    """Подключение есть, модель названа и, если список известен, есть в нём."""
    if not isinstance(endpoint_id, str) or not endpoint_id:
        raise ChoiceError("не выбрано подключение")
    if not isinstance(model, str) or not model.strip():
        raise ChoiceError("не выбрана модель")
    model = model.strip()
    if len(model) > 200 or any(char.isspace() or char == "," or ord(char) < 32 for char in model):
        raise ChoiceError("имя модели — одно слово до 200 знаков, без пробелов и запятых")
    try:
        endpoint = cfg.llm_endpoint(endpoint_id)
    except cfg.ConfigError as exc:
        raise ChoiceError(str(exc)) from exc
    listed, source, _ = models(endpoint)
    # Список объявлен или получен — модель обязана в нём быть: опечатка в имени
    # иначе всплыла бы отказом шлюза посреди прогона. Шлюз списка не дал —
    # принимаем имя как есть: проверить его можно кнопкой «Проверить».
    if source != "default" and model not in listed:
        raise ChoiceError(f"модели {model!r} нет в списке подключения «{endpoint.title}»")
    return endpoint


def save(subject: str, endpoint_id: str | None, model: str | None) -> None:
    """Запомнить выбор; `endpoint_id=None` — вернуться к модели сервера."""
    if reason := unavailable():
        raise ChoiceUnavailable(reason)
    value = None
    if endpoint_id is not None:
        validate(endpoint_id, model or "")
        value = json.dumps({"endpoint": endpoint_id, "model": (model or "").strip()})
    try:
        rows.write(subject, value)
    except db.DatabaseUnavailable as exc:
        raise ChoiceUnavailable(f"выбор не сохранён: {exc}") from exc
    finally:
        forget_cache(subject)


def check(endpoint_id: str, model: str) -> dict:
    """
    Спросить модель «ок» коротким запросом: отвечает ли она вообще.

    Тот же клиент, что у прогона (адрес, ключ, `extra_body`), но с коротким
    таймаутом и без повторов: проверка, которая ждёт семь минут, ничего не
    проверяет. Ответ модели не важен — важно, что он пришёл.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from agent import providers

    endpoint = validate(endpoint_id, model)
    started = time.monotonic()
    try:
        client = providers.build_probe(endpoint, model.strip(), timeout=_CHECK_TIMEOUT_S)
        client.invoke([
            SystemMessage(content="Отвечай одним словом."),
            HumanMessage(content="Ответь словом «ок»."),
        ])
    except Exception as exc:  # noqa: BLE001 — любой отказ и есть ответ проверки
        detail = _probe_error(exc)
        _LOG.warning("проверка модели %s/%s не прошла: %s", endpoint.id, model, detail)
        return {"ok": False, "detail": detail, "seconds": round(time.monotonic() - started, 1)}
    seconds = round(time.monotonic() - started, 1)
    return {"ok": True, "detail": f"модель ответила за {seconds} с", "seconds": seconds}


def _probe_error(exc: Exception) -> str:
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    name = type(exc).__name__
    # Клиент OpenAI кладёт в текст исключения весь ответ шлюза словарём Python;
    # человеку нужно само сообщение — оно в `body`.
    body = getattr(exc, "body", None)
    message = body.get("message") if isinstance(body, dict) else None
    raw = str(message) if message else str(exc)
    text = raw.strip().splitlines()[0] if raw.strip() else ""
    # Адрес шлюза мог попасть в текст исключения — страницу видит не только
    # администратор, поэтому хост вырезается.
    for word in text.split():
        if "://" in word:
            host = urlsplit(word.strip("'\"()<>,")).hostname
            if host:
                text = text.replace(host, "…")
    prefix = f"{name}" + (f" (HTTP {status})" if status else "")
    return f"{prefix}: {text}"[:_MAX_ERROR] if text else prefix
