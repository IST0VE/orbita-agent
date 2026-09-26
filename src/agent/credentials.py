"""
Личные подключения Jira и Confluence: у каждого пользователя свои токен и e-mail,
свой проект Jira и своё пространство Confluence.

Адрес сервера, пути API и лимиты — общие, они в `.env`. Токен — нет: с ним
агент читает и пишет от имени человека и видит ровно то, что видит он.
Общий токен из `.env` остаётся админ-токену (скрипты, CI, интерфейс без OIDC).

Проект и пространство — не пропуск, а место: куда заводить задачи и куда
публиковать. Своё значение сильнее общего из `.env`, а общее остаётся
значением по умолчанию — его видно на странице рядом с пустым полем.

Чей токен брать, решает `current_subject()`:

* внутри прогона — `langgraph_auth_user_id` из конфига прогона: его кладёт
  сервер LangGraph по пользователю из `auth.py`. Контекст HTTP-запроса здесь
  не годится: воркер прогонов мог родиться в чужом запросе и унести его с собой;
* вне прогона — пользователь текущего HTTP-запроса (`security.current()`);
* никого — это скрипт или тест, и работает `.env`, как раньше.

Нет личного токена — нет и запуска: общий из `.env` не подставляется, иначе
человек снова действовал бы под чужим именем и с чужими правами.

Хранение — таблица `user_secrets` в Postgres (`db.py`). Каждое значение
зашифровано AES-256-GCM ключом из USER_SECRETS_KEY; ключ живёт в `.env` хоста,
данные — в базе, и копия базы без ключа бесполезна. Шифр привязан к паре
«пользователь, поле» (associated data): переставленная в чужую строку или в
другое поле запись не расшифруется. Сменить ключ: новый — в USER_SECRETS_KEY,
прежний — в USER_SECRETS_OLD_KEYS; записи перешифровываются при следующем
сохранении. Потерянный ключ — не катастрофа: люди выпустят токены заново.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from agent import config as cfg
from agent import db, security


@dataclass(frozen=True)
class Field:
    """Одно личное поле: чем оно является и как о нём говорить."""

    name: str
    system: str
    #: `token` — пропуск, наружу не уходит; `email` — часть пропуска; `place` —
    #: куда писать: своё значение сильнее общего, общее — значение по умолчанию.
    kind: str
    label: str
    #: Как поле называется в «не задано: …».
    missing: str
    hint: str
    #: Каким должно быть значение; пусто — любым без переноса строки.
    pattern: str = ""
    #: Место внутри другого места: родительская страница живёт в пространстве.
    #: Задал своё пространство — чужой родитель из `.env` к нему не подходит.
    within: tuple[str, ...] = ()
    #: То же место под другим именем: ключ и числовой id пространства. Задал
    #: своё под одним именем — общее под другим называет уже чужое место.
    twin: str = ""

    @property
    def secret(self) -> bool:
        return self.kind == "token"


FIELDS: tuple[Field, ...] = (
    Field(
        "JIRA_TOKEN", "jira", "token", "Личный токен", "токен Jira",
        "В Jira: аватар → «Профиль» → «Персональные токены доступа» → «Создать токен». "
        "Агент получит ваши права: увидит и заведёт только то, что можете вы.",
    ),
    Field(
        "JIRA_EMAIL", "jira", "email", "E-mail", "e-mail Jira",
        "Только для входа e-mail + токен (Atlassian Cloud). Для Jira на своём сервере "
        "оставьте пустым: токен уйдёт как Bearer.",
    ),
    Field(
        "JIRA_PROJECT_KEY", "jira", "place", "Проект по умолчанию", "проект Jira",
        "Ключ проекта, куда заводить задачи, если в прогоне проект не выбран: ORB "
        "в номере задачи ORB-123.",
        pattern=r"[A-Za-z][A-Za-z0-9_]{0,63}",
    ),
    Field(
        "CONFLUENCE_TOKEN", "confluence", "token", "Личный токен", "токен Confluence",
        "В Confluence, а не в Jira: аватар → «Настройки» → «Персональные токены доступа». "
        "Токен Jira здесь не подойдёт — это другой сервер.",
    ),
    Field(
        "CONFLUENCE_EMAIL", "confluence", "email", "E-mail", "e-mail Confluence",
        "Только для входа e-mail + токен (Atlassian Cloud). Для Confluence на своём "
        "сервере оставьте пустым.",
    ),
    Field(
        "CONFLUENCE_SPACE_KEY", "confluence", "place", "Пространство", "пространство Confluence",
        "Ключ пространства, куда публиковать документы и где искать страницы: DOCS "
        "в адресе …/display/DOCS/…; личное пространство — ~логин.",
        pattern=r"~?[A-Za-z0-9_.\-]{1,255}",
        twin="CONFLUENCE_SPACE_ID",
    ),
    Field(
        "CONFLUENCE_SPACE_ID", "confluence", "place", "Пространство (id)", "id пространства Confluence",
        "Числовой id пространства: REST v2 создаёт страницы по нему, а не по ключу.",
        pattern=r"[0-9]{1,20}",
        twin="CONFLUENCE_SPACE_KEY",
    ),
    Field(
        "CONFLUENCE_PARENT_PAGE_ID", "confluence", "place", "Родительская страница",
        "родительская страница Confluence",
        "Число из адреса страницы (…pageId=123456): новые документы появятся под ней. "
        "Пусто — в корне пространства.",
        pattern=r"[0-9]{1,20}",
        within=("CONFLUENCE_SPACE_KEY", "CONFLUENCE_SPACE_ID"),
    ),
)
BY_NAME = {field.name: field for field in FIELDS}
SYSTEMS: dict[str, tuple[str, ...]] = {}
for _field in FIELDS:
    SYSTEMS.setdefault(_field.system, ())
    SYSTEMS[_field.system] += (_field.name,)
TITLES = {"jira": "Jira", "confluence": "Confluence"}
NAMES = frozenset(BY_NAME)
SECRETS = frozenset(field.name for field in FIELDS if field.secret)
#: Пропуск: токен и e-mail. Их смена стирает прошлую проверку, «Отключить» стирает их.
ACCESS = frozenset(field.name for field in FIELDS if field.kind != "place")
_MIN_KEY_CHARS = 32
_MAX_VALUE_CHARS = 4096
#: Сколько секунд расшифрованные значения живут в памяти процесса. Прогон
#: берёт токен на каждый запрос к Jira; сходить за ним в базу раз в полминуты
#: достаточно, а своё сохранение процесс сбрасывает сразу.
_CACHE_S = 30.0
_SETTINGS_HINT = "«Настройки» → «Мои подключения»"


class CredentialsError(RuntimeError):
    """Личное подключение недоступно: нет ключа, базы или запись не читается."""


# --------------------------------------------------------------------------
# Шифрование
# --------------------------------------------------------------------------
def _derive(secret: str) -> bytes:
    # Ключ — строка из .env (up.cmd / up.sh пишут 43 случайных символа, это
    # ~256 бит). SHA-256 с меткой назначения даёт из неё ровно 32 байта.
    return hashlib.sha256(b"orbita/user-secrets/v1\x00" + secret.encode()).digest()


def _key_id(key: bytes) -> str:
    return hashlib.sha256(b"orbita/key-id\x00" + key).hexdigest()[:16]


def _keys() -> dict[str, bytes]:
    """Ключи по id; первый — текущий, им шифруется всё новое."""
    current = cfg.user_secrets_key()
    if not current:
        raise CredentialsError(
            "на сервере не задан USER_SECRETS_KEY — личные подключения недоступны "
            "(up.cmd / up.sh создают его сами, см. docs/DEPLOYMENT.md)"
        )
    if len(current) < _MIN_KEY_CHARS:
        raise CredentialsError(f"USER_SECRETS_KEY короче {_MIN_KEY_CHARS} символов")
    keys: dict[str, bytes] = {}
    for secret in (current, *cfg.user_secrets_old_keys()):
        key = _derive(secret)
        keys.setdefault(_key_id(key), key)
    return keys


def _label(name: str) -> str:
    field = BY_NAME.get(name)
    return field.missing if field else name


def _aad(subject: str, name: str) -> bytes:
    return f"{subject}\x00{name}".encode()


def seal(subject: str, name: str, value: str) -> tuple[str, bytes, bytes]:
    key_id, key = next(iter(_keys().items()))
    nonce = os.urandom(12)
    return key_id, nonce, AESGCM(key).encrypt(nonce, value.encode(), _aad(subject, name))


def unseal(subject: str, name: str, key_id: str, nonce: bytes, ciphertext: bytes) -> str:
    key = _keys().get(key_id)
    if key is None:
        raise CredentialsError(
            f"{_label(name)}: записан ключом, которого нет ни в USER_SECRETS_KEY, "
            f"ни в USER_SECRETS_OLD_KEYS — задайте значение заново ({_SETTINGS_HINT})"
        )
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, _aad(subject, name)).decode()
    except InvalidTag as exc:
        raise CredentialsError(
            f"{_label(name)}: запись не расшифровалась — задайте значение заново"
        ) from exc


# --------------------------------------------------------------------------
# Хранение
# --------------------------------------------------------------------------
class PostgresRows:
    """Строки `user_secrets` и `user_connection_checks`. Тесты подменяют `rows`."""

    def load(self, subject: str) -> dict[str, tuple[str, bytes, bytes, datetime]]:
        with db.connection() as conn:
            found = conn.execute(
                "SELECT name, key_id, nonce, ciphertext, updated_at"
                " FROM user_secrets WHERE subject = %s",
                (subject,),
            ).fetchall()
        return {name: (key_id, bytes(nonce), bytes(ct), at) for name, key_id, nonce, ct, at in found}

    def write(self, subject: str, sealed: dict[str, tuple[str, bytes, bytes] | None]) -> None:
        # Прошлая проверка говорила о прежнем пропуске; смена проекта её не трогает.
        touched = {BY_NAME[name].system for name in sealed if name in ACCESS}
        with db.connection() as conn, conn.transaction():
            for name, row in sealed.items():
                if row is None:
                    conn.execute(
                        "DELETE FROM user_secrets WHERE subject = %s AND name = %s", (subject, name)
                    )
                    continue
                conn.execute(
                    "INSERT INTO user_secrets (subject, name, key_id, nonce, ciphertext)"
                    " VALUES (%s, %s, %s, %s, %s)"
                    " ON CONFLICT (subject, name) DO UPDATE SET key_id = EXCLUDED.key_id,"
                    " nonce = EXCLUDED.nonce, ciphertext = EXCLUDED.ciphertext, updated_at = now()",
                    (subject, name, *row),
                )
            for system in touched:
                conn.execute(
                    "DELETE FROM user_connection_checks WHERE subject = %s AND system = %s",
                    (subject, system),
                )

    def checks(self, subject: str) -> dict[str, dict]:
        with db.connection() as conn:
            found = conn.execute(
                "SELECT system, ok, detail, checked_at FROM user_connection_checks"
                " WHERE subject = %s",
                (subject,),
            ).fetchall()
        return {
            system: {"ok": ok, "detail": detail, "checked_at": at.isoformat()}
            for system, ok, detail, at in found
        }

    def record_check(self, subject: str, system: str, ok: bool, detail: str) -> None:
        with db.connection() as conn:
            conn.execute(
                "INSERT INTO user_connection_checks (subject, system, ok, detail)"
                " VALUES (%s, %s, %s, %s)"
                " ON CONFLICT (subject, system) DO UPDATE SET ok = EXCLUDED.ok,"
                " detail = EXCLUDED.detail, checked_at = now()",
                (subject, system, ok, detail),
            )


rows = PostgresRows()
_cache: dict[str, tuple[float, dict[str, str | CredentialsError]]] = {}
_cache_lock = threading.Lock()


def _load(subject: str) -> dict[str, tuple[str, bytes, bytes, datetime]]:
    try:
        return rows.load(subject)
    except db.DatabaseUnavailable as exc:
        raise CredentialsError(f"личные подключения недоступны: {exc}") from exc


def _values(subject: str) -> dict[str, str | CredentialsError]:
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(subject)
        if hit and hit[0] > now:
            return hit[1]
    values: dict[str, str | CredentialsError] = {}
    for name, (key_id, nonce, ciphertext, _) in _load(subject).items():
        if name not in NAMES:
            continue
        try:
            values[name] = unseal(subject, name, key_id, nonce, ciphertext)
        except CredentialsError as exc:
            # Нечитаемая запись не прячет соседние: e-mail без токена тоже ответ.
            values[name] = exc
    with _cache_lock:
        _cache[subject] = (now + _CACHE_S, values)
    return values


def _forget_cache(subject: str) -> None:
    with _cache_lock:
        _cache.pop(subject, None)


# --------------------------------------------------------------------------
# Чей токен
# --------------------------------------------------------------------------
def current_subject() -> str | None:
    """Кто сейчас работает: пользователь прогона, запроса или никто."""
    try:
        from langgraph.config import get_config

        config = get_config()
    except (ImportError, RuntimeError):
        config = None
    if config is not None:
        return (config.get("configurable") or {}).get("langgraph_auth_user_id") or None
    principal = security.current()
    return principal.subject if principal else None


def personal(subject: str | None) -> bool:
    """С этим пользователем токены берутся личные, а не из `.env`."""
    return bool(subject) and subject != security.SERVICE_SUBJECT


def _own(values: dict[str, str | CredentialsError], name: str) -> str:
    found = values.get(name, "")
    if isinstance(found, CredentialsError):
        raise found
    return found.strip()


def _resolve(values: dict[str, str | CredentialsError], name: str) -> str:
    """Личное значение пользователя, а для места — и общее по умолчанию."""
    own = _own(values, name)
    field = BY_NAME[name]
    if own or field.kind != "place":
        return own
    # Своё пространство без своего родителя — корень своего пространства, а не
    # страница из общего: она лежит в другом пространстве, и публикация упала бы.
    if any(_own(values, outer) for outer in field.within):
        return ""
    # Своё пространство по id и общий ключ из `.env` — два разных пространства:
    # страница ушла бы в одно, а поиск искал бы в другом. Интерфейс при этом
    # показывает только одно из двух полей (`_shown`), и второе человеку не
    # поправить. Ключ по своему id узнаёт сам клиент Confluence.
    if field.twin and _own(values, field.twin):
        return ""
    return cfg.env_str(name)


def value(name: str) -> str:
    """
    Значение настройки для того, кто сейчас работает.

    Токен и e-mail пользователя — только его личные: общий пропуск за человека не
    подставляется. Проект и пространство — его личные, а без них общие из `.env`.
    Для остального и для админ-токена — `.env`, как было.
    """
    subject = current_subject()
    if name not in NAMES or not personal(subject):
        return cfg.env_str(name)
    return _resolve(_values(subject), name)


def missing_message(absent: list[str]) -> str:
    """«Не задано» по-человечески: что правит администратор, а что — сам пользователь."""
    own = [name for name in absent if name in NAMES and personal(current_subject())]
    server = [name for name in absent if name not in own]
    parts = []
    if server:
        parts.append("не заданы переменные окружения: " + ", ".join(server))
    if own:
        parts.append(
            f"в {_SETTINGS_HINT} не задано: " + ", ".join(_label(name) for name in own)
        )
    return "; ".join(parts)


# --------------------------------------------------------------------------
# Для страницы «Мои подключения»
# --------------------------------------------------------------------------
def available() -> str | None:
    """Почему личные подключения выключены; None — включены."""
    try:
        _keys()
    except CredentialsError as exc:
        return str(exc)
    return None


def _shown(field: Field) -> bool:
    """Ключ пространства нужен REST v1, числовой id — v2; второе поле только путало бы."""
    if field.name == "CONFLUENCE_SPACE_ID":
        return cfg.confluence_api_version() == "v2"
    if field.name == "CONFLUENCE_SPACE_KEY":
        return cfg.confluence_api_version() == "v1"
    return True


def describe(subject: str) -> dict:
    """Что показать пользователю. Токен наружу не уходит: только «задан» и когда."""
    stored = _load(subject)
    try:
        checks = rows.checks(subject)
    except db.DatabaseUnavailable as exc:
        raise CredentialsError(f"личные подключения недоступны: {exc}") from exc
    values = _values(subject)
    systems = []
    for system, names in SYSTEMS.items():
        fields = []
        for name in names:
            field = BY_NAME[name]
            if not _shown(field):
                continue
            found = values.get(name, "")
            fields.append(
                {
                    "name": name,
                    "kind": field.kind,
                    "label": field.label,
                    "hint": field.hint,
                    "secret": field.secret,
                    "filled": name in stored,
                    # Токен не уходит никогда — ни маской, ни началом.
                    "value": "" if field.secret or not isinstance(found, str) else found,
                    # Общее значение для места: его и возьмёт прогон, если своё пусто.
                    "default": cfg.env_str(name) if field.kind == "place" else "",
                    "updated_at": stored[name][3].isoformat() if name in stored else None,
                    "error": str(found) if isinstance(found, CredentialsError) else None,
                }
            )
        token = next(name for name in names if BY_NAME[name].secret)
        systems.append(
            {
                "id": system,
                "title": TITLES[system],
                # Куда уйдёт токен: человек должен видеть это до того, как его вставит.
                "base_url": cfg.env_str(f"{system.upper()}_BASE_URL"),
                "connected": token in stored,
                "fields": fields,
                "check": checks.get(system),
            }
        )
    return {"unavailable": available(), "systems": systems}


def save(subject: str, values: dict[str, object]) -> list[str]:
    """
    Записать присланное. Пустая строка стирает значение.

    Проверки те же, что у `.env`: имя из списка, без переноса строки, не маска.
    """
    sealed: dict[str, tuple[str, bytes, bytes] | None] = {}
    for name, raw in values.items():
        if name not in NAMES:
            raise ValueError(f"{name!r}: это не личная настройка")
        text = "" if raw is None else str(raw).strip()
        if "\n" in text or "\r" in text:
            raise ValueError(f"{name}: перенос строки в значении")
        if len(text) > _MAX_VALUE_CHARS:
            raise ValueError(f"{name}: значение длиннее {_MAX_VALUE_CHARS} символов")
        if name in SECRETS and text and set(text) == {"*"}:
            raise ValueError(f"{name}: пришла маска вместо значения — поле не сохранено")
        field = BY_NAME[name]
        if text and field.pattern and not re.fullmatch(field.pattern, text):
            raise ValueError(f"{field.label}: {text!r} так не выглядит — {field.hint}")
        sealed[name] = seal(subject, name, text) if text else None
    if not sealed:
        return []
    try:
        rows.write(subject, sealed)
    except db.DatabaseUnavailable as exc:
        raise CredentialsError(f"личные подключения недоступны: {exc}") from exc
    finally:
        _forget_cache(subject)
    return sorted(sealed)


def forget(subject: str, system: str) -> None:
    """
    Отключить систему: стереть токен, e-mail и прошлую проверку.

    Проект и пространство остаются: это выбор места, а не пропуск, и после
    нового токена он снова понадобится таким же.
    """
    save(subject, {name: "" for name in SYSTEMS[system] if name in ACCESS})


def check(subject: str, system: str) -> dict:
    """
    Проверить подключение запросом «кто я» с личным токеном.

    Результат запоминается: страница показывает его и после перезагрузки, а
    сохранение нового токена его стирает.
    """
    from agent import confluence, jira

    try:
        if system == "jira":
            s = jira.load_settings()
            me = jira.call("GET", f"{s.api_path}/myself", s)
        else:
            s = confluence.load_settings(require_space=False)
            me = confluence._call("GET", "/rest/api/user/current", s)
    except (jira.JiraError, confluence.ConfluenceError) as exc:
        ok, detail = False, str(exc)
    else:
        who = me.get("displayName") or me.get("name") or me.get("username") or me.get("emailAddress")
        ok, detail = True, f"вход выполнен: {who or 'учётная запись без имени'}"
    try:
        rows.record_check(subject, system, ok, detail)
    except db.DatabaseUnavailable as exc:
        raise CredentialsError(f"личные подключения недоступны: {exc}") from exc
    return {"ok": ok, "detail": detail}
