"""
Единый порядок внешних действий: предложение → правила → согласие → выполнение → сверка.

Записей наружу у проекта три вида: страницы в Confluence или файлы, задачи в
Jira, нагрузка на стенд НТ. Каждый вырос своим путём, и у каждого было своё
понимание того, на что согласился оператор. Публикация сверяла одобряемый набор
(`publish_nodes.publish_commitment`), НТ — отпечаток плана (`approved_hash`), а
заведение задач не сверяло ничего: ответ «да» относился к слову «заводить», и
правка в треде заводила заново все карточки, включая неизменённые.

Здесь то общее, что у них должно быть одинаковым:

  предложение  что именно и куда: цель и список операций, у каждой ключ и
               отпечаток содержимого. Отпечаток всего предложения (`digest`) —
               то, к чему привязано согласие (`seal`);
  правила      проверки кодом до вопроса: рубильники, реквизиты, коллизии,
               «отправлять нечего». Живут у вида действия (`jira_graph`,
               `publish_nodes._publish_skip`), здесь — только их итог;
  согласие     `interrupt()` показывает предложение вместе с отпечатком,
               интерфейс возвращает его в ответе (`bind`). Перед записью
               предложение собирается заново и сравнивается (`stale`): согласие
               на другое содержимое — не согласие;
  выполнение   каждая операция проходит через журнал: запись «отправляем» до
               запроса, итог после. Повтор узла узнаёт свои операции и не
               отправляет принятое второй раз;
  сверка       после записи прочитанное с той стороны сравнивается с
               отправленным (`VERIFIED`, `DIFFERS`, `UNVERIFIED`). Сравнение
               идёт по нормализованному тексту: трекер и wiki переписывают
               разметку, и побайтовая сверка расходилась бы на каждой записи.

Хранилище одно на все виды. С `POSTGRES_URI` — таблицы `actions`,
`action_approvals`, `action_operations` (`db.py`, миграция 3): аудит, защита от
повтора и правка только изменённого переживают пересоздание контейнера и видны
по пользователю. Без базы — тот же SQL в файле `JIRA_JOURNAL_PATH`: журнал
обязан переживать перезапуск процесса и там, где Postgres не заводили.

Журнал операций — прежний `jira_journal.Journal` с тем же интерфейсом
(`record`, `begin`, `finish`, `fail`, `unresolved`): его ключ — область
(`scope`, тред и цель), операция и локальный ключ. Области живут дольше
действия: следующий ход треда по ним узнаёт, что уже заведено и с каким
содержимым, и предлагает отправить только изменившееся.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent import config as cfg

# Состояния операции в журнале.
PENDING = "pending"
COMPLETED = "completed"
UNKNOWN = "unknown"
FAILED = "failed"

# Итог сверки после записи.
VERIFIED = "verified"
DIFFERS = "differs"
UNVERIFIED = "unverified"

# Состояния действия. Итог выполнения — статус узла (`created`, `partial`,
# `failed`, ...), он пишется как есть.
PROPOSED = "proposed"
APPROVED = "approved"
REJECTED = "rejected"
STALE = "stale"

#: Префикс метки операции. По нему же её видно в самой Jira: задача, заведённая
#: конвейером, помечена, и найти её потом можно не только по журналу.
LABEL_PREFIX = "orbita-op"


class JournalUnavailable(RuntimeError):
    """Журнал действий не открылся или не принял запись. Текст — для человека."""


# --------------------------------------------------------------------------
# Отпечатки
# --------------------------------------------------------------------------
def digest(value: Any) -> str:
    """Отпечаток значения: канонический JSON, SHA-256."""
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_key(*parts: str) -> str:
    """Ключ области журнала: тред и цель — см. `jira_graph` и `publish_nodes`."""
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:32]


def label_for(run: str, kind: str, local: str) -> str:
    """
    Устойчивая метка операции: одна и та же у прогона и у его повтора.

    Длина ограничена: Jira принимает метку до 255 знаков, но короткая метка
    читается в интерфейсе трекера, а длины отпечатка хватает, чтобы не
    столкнуться с чужой.
    """
    value = hashlib.sha256(f"{run}\n{kind}\n{local}".encode()).hexdigest()[:16]
    return f"{LABEL_PREFIX}-{value}"


def seal(
    kind: str,
    *,
    graph: str,
    target: Mapping[str, Any],
    operations: Iterable[Mapping[str, Any]],
    thread: str = "",
    owner: str = "",
    scope: str = "",
    fingerprint: str = "",
) -> dict:
    """
    Предложение: цель, операции и отпечаток содержимого.

    Отпечаток (`digest`) считается по виду, цели и операциям — по тому, на что
    соглашаются. Тред и владелец в него не входят: одно и то же предложение
    в двух тредах — одно содержимое. Они входят в `id`: запись журнала у
    каждого треда своя.

    `fingerprint` — готовый отпечаток, когда у вида действия он уже есть и
    согласия к нему давно привязаны: у публикации это отпечаток одобряемого
    набора (`publish_nodes.commitment_digest`). Два отпечатка одного и того же
    расходились бы в журнале с тем, что видел оператор.
    """
    content = {
        "kind": kind,
        "target": dict(target),
        "operations": [dict(item) for item in operations],
    }
    fingerprint = fingerprint or digest(content)
    return {
        **content,
        "graph": graph,
        "thread": thread,
        "owner": owner,
        "scope": scope,
        "digest": fingerprint,
        "id": "ACT-" + digest([thread, kind, fingerprint])[:20],
    }


def pending(proposal: Mapping[str, Any]) -> list[dict]:
    """Операции, которые что-то отправят: без `unchanged`."""
    return [dict(op) for op in proposal.get("operations") or [] if op.get("action") != "unchanged"]


# --------------------------------------------------------------------------
# Согласие
# --------------------------------------------------------------------------
def approval_of(answer: Any) -> dict:
    """
    Ответ оператора — в решение.

    Интерфейсы возвращают разное: Studio — введённый JSON, чат — строку, свой
    код — просто True. Понимаем все три, потому что человеку на другом конце
    не должно быть важно, чем он пользуется.
    """
    if isinstance(answer, dict):
        raw = answer.get("decision", answer.get("approved"))
        reason = str(answer.get("reason", "") or "")
    else:
        raw, reason = answer, ""

    if isinstance(raw, str):
        approved = raw.strip().lower() in {"approve", "approved", "yes", "y", "да", "ок"}
    else:
        approved = bool(raw)
    return {"decision": "approved" if approved else "rejected", "reason": reason}


def shown(prompt: Mapping[str, Any], proposal: Mapping[str, Any]) -> dict:
    """
    Остановка с отпечатком предложения.

    Интерфейс возвращает `digest` в ответе (`approval.tsx`), и сервер узнаёт,
    на какую версию предложения дано согласие, — даже если за время чтения
    тред успел показать другую.
    """
    return {**prompt, "digest": proposal["digest"], "action_id": proposal["id"]}


def bind(answer: Any, fingerprint: str) -> dict:
    """
    Решение оператора, привязанное к отпечатку показанного предложения.

    Ответ с чужим отпечатком — не согласие: оператор решал по другой версии.
    Отказ остаётся отказом при любом отпечатке — он ничего не пишет. Ответ
    без отпечатка (Studio, старый интерфейс, `true`) относится к тому, что
    показала эта остановка, и перед записью всё равно сверяется (`stale`).
    """
    decision = approval_of(answer)
    if isinstance(answer, dict) and answer.get("decision") == "drafts":
        decision["decision"] = "drafts"
    echoed = str(answer.get("digest") or "") if isinstance(answer, dict) else ""
    if echoed and echoed != fingerprint and decision["decision"] != "rejected":
        decision = {
            "decision": STALE,
            "reason": "решение принято по другой версии предложения: подтвердите заново",
        }
    decision["digest"] = fingerprint
    return decision


def stale(approval: Mapping[str, Any], fingerprint: str) -> str:
    """Почему согласие к этому содержимому неприменимо. Пусто — применимо."""
    decision = approval.get("decision")
    if decision == STALE:
        return str(approval.get("reason") or "решение относится к другой версии предложения")
    if decision not in {"approved", "drafts"}:
        return ""
    if not approval.get("digest"):
        return "согласие не привязано к содержимому (старый checkpoint): подтвердите заново"
    if approval["digest"] != fingerprint:
        return "содержимое изменилось после согласия: подтвердите заново"
    return ""


def status_of(approval: Mapping[str, Any]) -> str:
    decision = str(approval.get("decision") or "")
    return decision if decision in {"approved", "drafts", STALE} else REJECTED


def actor() -> str:
    """Кто действует: пользователь прогона или запроса, без входа — админ-токен."""
    from agent import credentials, security

    return credentials.current_subject() or security.SERVICE_SUBJECT


# --------------------------------------------------------------------------
# Хранилище
# --------------------------------------------------------------------------
_OPERATION_COLUMNS = (
    "scope", "op", "key", "action_id", "state", "label", "title", "remote", "url",
    "detail", "content_hash", "remote_hash", "remote_version", "verified", "updated_at",
)
_ACTION_COLUMNS = (
    "id", "kind", "graph", "thread_id", "owner", "scope", "digest", "target",
    "operations", "status", "result", "created_at", "updated_at",
)

#: Что из итога выполнения попадает в журнал. Тел документов и описаний задач
#: там нет: журнал — про то, что сделано, а не про то, что написано.
_RESULT_KEYS = (
    "status", "reason", "project", "target", "created", "failed", "unresolved",
    "unchanged", "updated", "conflicts", "pages", "verification", "journal_error",
)
_ITEM_KEYS = (
    "local", "key", "url", "title", "status", "reason", "page_id", "version", "path",
    "role", "verified", "recovered", "unchanged", "updated", "conflict",
)


def brief(result: Mapping[str, Any] | None) -> dict:
    """Итог выполнения без текстов: что сделано, где и чем кончилась сверка."""
    kept: dict[str, Any] = {}
    for key in _RESULT_KEYS:
        value = (result or {}).get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            value = [
                {k: item[k] for k in _ITEM_KEYS if k in item} if isinstance(item, dict) else item
                for item in value
            ]
        kept[key] = value
    return kept


class _Sql:
    """
    Действия и журнал операций одним SQL на двух базах.

    Различаются соединение, подстановки, время и JSON — это методы наследников.
    Всё остальное общее: схема одна, и расходиться двум копиям запросов нельзя,
    иначе журнал без базы и журнал с базой по-разному отвечали бы на вопрос
    «уже отправлено?».
    """

    placeholder = "%s"

    @contextmanager
    def _connect(self) -> Iterator[Any]:  # pragma: no cover - у наследников
        raise NotImplementedError
        yield

    def _sql(self, text: str) -> str:
        return text if self.placeholder == "%s" else text.replace("%s", self.placeholder)

    def _run(self, text: str, params: tuple = ()) -> list[tuple]:
        with self._connect() as conn:
            cursor = conn.execute(self._sql(text), params)
            # У записи строк нет: `fetchall` psycopg на ней поднял бы ошибку.
            return list(cursor.fetchall()) if cursor.description else []

    def _json(self, value: Any) -> Any:
        return json.dumps(value, ensure_ascii=False, default=str)

    def _load(self, value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value

    def _stamp(self) -> Any:
        return datetime.now(UTC)

    def _iso(self, value: Any) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, UTC).isoformat()
        return str(value or "")

    # ---------------------------------------------------------------- действия
    def save_action(self, proposal: Mapping[str, Any], status: str,
                    *, result: Mapping[str, Any] | None = None) -> None:
        """Предложение в журнал. То же предложение повторно — новый статус."""
        now = self._stamp()
        self._run(
            "INSERT INTO actions (id, kind, graph, thread_id, owner, scope, digest, target,"
            " operations, status, result, created_at, updated_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (id) DO UPDATE SET status = excluded.status,"
            " updated_at = excluded.updated_at",
            (
                proposal["id"], proposal["kind"], proposal.get("graph") or "",
                proposal.get("thread") or "", proposal.get("owner") or actor(),
                proposal.get("scope") or "", proposal["digest"],
                self._json(proposal.get("target") or {}),
                self._json(proposal.get("operations") or []), status,
                self._json(brief(result)), now, now,
            ),
        )

    def set_status(self, action_id: str, status: str,
                   result: Mapping[str, Any] | None = None) -> None:
        if result is None:
            self._run("UPDATE actions SET status = %s, updated_at = %s WHERE id = %s",
                      (status, self._stamp(), action_id))
            return
        self._run(
            "UPDATE actions SET status = %s, result = %s, updated_at = %s WHERE id = %s",
            (status, self._json(brief(result)), self._stamp(), action_id),
        )

    def add_approval(self, action_id: str, approval: Mapping[str, Any], who: str) -> None:
        self._run(
            "INSERT INTO action_approvals (action_id, decision, digest, actor, reason, decided_at)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (action_id, status_of(approval), str(approval.get("digest") or ""), who,
             str(approval.get("reason") or ""), self._stamp()),
        )

    # Согласие и его продолжение. Узел, перезапущенный после падения посреди
    # записи, получает тот же ответ оператора, а журнал уже продвинулся его же
    # выполнением, и пересобранное предложение другое. Согласие на предложение
    # покрывает его продолжение — спрашивать заново о том, что человек уже
    # одобрил и что частично уехало, значило бы переложить на него сбой.
    def approved_action(self, action_id: str) -> bool:
        """Было ли согласие на действие с этим id."""
        if not action_id:
            return False
        rows = self._run(
            "SELECT 1 FROM action_approvals WHERE action_id = %s AND decision = 'approved' LIMIT 1",
            (action_id,),
        )
        return bool(rows)

    def approved_operations(self, thread: str, kind: str, fingerprint: str) -> list[dict] | None:
        """Операции предложения, на которое в этом треде согласились. None — не было."""
        rows = self._run(
            "SELECT a.operations FROM action_approvals p JOIN actions a ON a.id = p.action_id"
            " WHERE a.thread_id = %s AND a.kind = %s AND p.digest = %s"
            " AND p.decision = 'approved' LIMIT 1",
            (thread, kind, fingerprint),
        )
        return list(self._load(rows[0][0]) or []) if rows else None

    def _action(self, row: tuple) -> dict:
        found = dict(zip(_ACTION_COLUMNS, row, strict=True))
        for key in ("target", "operations", "result"):
            found[key] = self._load(found[key])
        found["created_at"] = self._iso(found["created_at"])
        found["updated_at"] = self._iso(found["updated_at"])
        return found

    def list(self, owner: str, *, thread: str = "", limit: int = 50) -> list[dict]:
        """Действия человека, свежие первыми. `thread` — только одного треда."""
        where, params = "owner = %s", [owner]
        if thread:
            where += " AND thread_id = %s"
            params.append(thread)
        rows = self._run(
            f"SELECT {', '.join(_ACTION_COLUMNS)} FROM actions WHERE {where}"
            " ORDER BY updated_at DESC LIMIT %s",
            (*params, int(limit)),
        )
        found = []
        for row in rows:
            action = self._action(row)
            action["operations"] = len(action["operations"] or [])
            found.append(action)
        return found

    def get(self, owner: str, action_id: str) -> dict | None:
        """Действие целиком: предложение, согласия, операции журнала. Чужое — None."""
        rows = self._run(
            f"SELECT {', '.join(_ACTION_COLUMNS)} FROM actions WHERE owner = %s AND id = %s",
            (owner, action_id),
        )
        if not rows:
            return None
        action = self._action(rows[0])
        approvals = self._run(
            "SELECT decision, digest, actor, reason, decided_at FROM action_approvals"
            " WHERE action_id = %s ORDER BY decided_at",
            (action_id,),
        )
        action["approvals"] = [
            {"decision": d, "digest": g, "actor": a, "reason": r, "decided_at": self._iso(at)}
            for d, g, a, r, at in approvals
        ]
        action["journal"] = [
            self._operation(row)
            for row in self._run(
                f"SELECT {', '.join(_OPERATION_COLUMNS)} FROM action_operations"
                " WHERE action_id = %s ORDER BY updated_at",
                (action_id,),
            )
        ]
        return action

    # --------------------------------------------------------------- операции
    def _operation(self, row: tuple) -> dict:
        found = dict(zip(_OPERATION_COLUMNS, row, strict=True))
        found["updated_at"] = self._iso(found["updated_at"])
        return found

    def record(self, run: str, kind: str, local: str) -> dict | None:
        rows = self._run(
            f"SELECT {', '.join(_OPERATION_COLUMNS)} FROM action_operations"
            " WHERE scope = %s AND op = %s AND key = %s",
            (run, kind, local),
        )
        return self._operation(rows[0]) if rows else None

    def records(self, run: str) -> list[dict]:
        rows = self._run(
            f"SELECT {', '.join(_OPERATION_COLUMNS)} FROM action_operations"
            " WHERE scope = %s ORDER BY updated_at",
            (run,),
        )
        return [self._operation(row) for row in rows]

    def import_legacy(self, run: str, rows: Iterable[Mapping[str, Any]],
                      hashes: Mapping[str, str]) -> None:
        """Перенести операции прежнего SQLite-журнала в новую область один раз."""
        for row in rows:
            kind, local = str(row["kind"]), str(row["local"])
            self._run(
                "INSERT INTO action_operations (scope, op, key, state, label, remote, url,"
                " detail, content_hash, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (scope, op, key) DO NOTHING",
                (run, kind, local, row["state"], row["label"], row["remote"],
                 row["url"], row["detail"], hashes.get(local, "") if kind == "issue" else "",
                 self._stamp()),
            )

    def begin(
        self,
        run: str,
        kind: str,
        local: str,
        *,
        content_hash: str = "",
        remote_hash: str = "",
        title: str = "",
        action: str = "",
    ) -> str:
        """
        Отметить операцию начатой и вернуть её метку.

        Запись делается ДО запроса: журнал, заполняемый после ответа, не знает
        ровно о том случае, ради которого он заведён, — об оборванном ответе
        на принятый трекером POST. Отпечатки пишутся сюда же: у операции,
        восстановленной потом сверкой по метке, иначе не было бы содержимого,
        с которым сравнивать следующую правку.
        """
        label = label_for(run, kind, local)
        self._run(
            "INSERT INTO action_operations (scope, op, key, action_id, state, label, title,"
            " content_hash, remote_hash, verified, detail, updated_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, '', '', %s)"
            " ON CONFLICT (scope, op, key) DO UPDATE SET state = excluded.state,"
            " action_id = excluded.action_id, label = excluded.label, title = excluded.title,"
            " content_hash = excluded.content_hash, remote_hash = excluded.remote_hash,"
            " verified = '', detail = '', updated_at = excluded.updated_at",
            (run, kind, local, action, PENDING, label, title, content_hash, remote_hash,
             self._stamp()),
        )
        return label

    def finish(
        self,
        run: str,
        kind: str,
        local: str,
        *,
        remote: str = "",
        url: str = "",
        detail: str = "",
        content_hash: str | None = None,
        remote_hash: str | None = None,
    ) -> None:
        sets = ["state = %s", "remote = %s", "url = %s", "detail = %s"]
        params: list[Any] = [COMPLETED, remote, url, detail]
        if content_hash is not None:
            sets.append("content_hash = %s")
            params.append(content_hash)
        if remote_hash is not None:
            sets.append("remote_hash = %s")
            params.append(remote_hash)
        self._update(run, kind, local, sets, params)

    def fail(self, run: str, kind: str, local: str, detail: str) -> None:
        """Трекер отказал явно: запроса, который мог пройти, не было."""
        self._update(run, kind, local, ["state = %s", "detail = %s"], [FAILED, detail])

    def unresolved(self, run: str, kind: str, local: str, detail: str) -> None:
        """Ответа нет и сверка не удалась. Дальше решает человек."""
        self._update(run, kind, local, ["state = %s", "detail = %s"], [UNKNOWN, detail])

    def checked(self, run: str, kind: str, local: str, verified: str, *, detail: str = "",
                remote_hash: str | None = None, remote_version: str | None = None) -> None:
        """
        Итог сверки после записи.

        `remote_hash` — нормализованный отпечаток того, что прочитано с той
        стороны. Он становится точкой отсчёта: следующая правка сравнит с ним
        то, что лежит там к тому моменту, и чужую правку руками не затрёт.
        """
        sets, params = ["verified = %s", "detail = %s"], [verified, detail]
        if remote_hash is not None:
            sets.append("remote_hash = %s")
            params.append(remote_hash)
        if remote_version is not None:
            sets.append("remote_version = %s")
            params.append(remote_version)
        self._update(run, kind, local, sets, params)

    def rebase(self, run: str, kind: str, local: str, *, content_hash: str,
               remote_hash: str, remote_version: str | None = None) -> None:
        """Новое содержимое записанного объекта: после принятой правки."""
        sets, params = ["content_hash = %s", "remote_hash = %s"], [content_hash, remote_hash]
        if remote_version is not None:
            sets.append("remote_version = %s")
            params.append(remote_version)
        self._update(run, kind, local, sets, params)

    def _update(self, run: str, kind: str, local: str, sets: list[str],
                params: list[Any]) -> None:
        self._run(
            f"UPDATE action_operations SET {', '.join(sets)}, updated_at = %s"
            " WHERE scope = %s AND op = %s AND key = %s",
            (*params, self._stamp(), run, kind, local),
        )


class SqliteActions(_Sql):
    """Журнал в файле: без Postgres и в тестах. Переживает перезапуск процесса."""

    placeholder = "?"

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path or cfg.jira_journal_path()).expanduser()
        if not self.path.is_absolute():
            self.path = Path.cwd() / self.path
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.executescript(_SQLITE_SCHEMA)
                columns = {row[1] for row in db.execute("PRAGMA table_info(action_operations)")}
                if "remote_version" not in columns:
                    db.execute("ALTER TABLE action_operations ADD COLUMN remote_version TEXT NOT NULL DEFAULT ''")
        except OSError as exc:
            raise JournalUnavailable(f"журнал действий недоступен ({exc})") from exc

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        try:
            db = sqlite3.connect(self.path, timeout=10)
        except (sqlite3.Error, OSError) as exc:
            raise JournalUnavailable(f"журнал действий недоступен ({exc})") from exc
        try:
            with db:
                yield db
        except sqlite3.Error as exc:
            raise JournalUnavailable(f"журнал действий не принял запись ({exc})") from exc
        finally:
            db.close()

    def _stamp(self) -> float:
        return time.time()


#: Та же схема, что в миграциях 3–4, на типах SQLite. Прежняя таблица Jira
#: (`operations`) остаётся в файле: записи точно того же плана переносятся в
#: новую область при первом обращении (`legacy_rows`, `import_legacy`).
_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    graph TEXT NOT NULL DEFAULT '',
    thread_id TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '',
    digest TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '{}',
    operations TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    result TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS actions_owner ON actions (owner, updated_at);
CREATE TABLE IF NOT EXISTS action_approvals (
    action_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    digest TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    decided_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS action_operations (
    scope TEXT NOT NULL,
    op TEXT NOT NULL,
    key TEXT NOT NULL,
    action_id TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    label TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    remote TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
    remote_hash TEXT NOT NULL DEFAULT '',
    remote_version TEXT NOT NULL DEFAULT '',
    verified TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL,
    PRIMARY KEY (scope, op, key)
);
"""


class PostgresActions(_Sql):
    """Журнал в базе приложения (`db.py`, миграция 3)."""

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        import psycopg

        from agent import db

        try:
            with db.connection() as conn, conn.transaction():
                yield conn
        except db.DatabaseUnavailable as exc:
            raise JournalUnavailable(f"журнал действий недоступен: {exc}") from exc
        except psycopg.Error as exc:
            first = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
            raise JournalUnavailable(f"журнал действий не принял запись: {first}") from exc

    def _json(self, value: Any) -> Any:
        from psycopg.types.json import Jsonb

        return Jsonb(value)


_lock = threading.Lock()
_files: dict[Path, SqliteActions] = {}
_postgres = PostgresActions()


def store() -> _Sql:
    """
    Хранилище действий этого процесса.

    Postgres, если `POSTGRES_URI` задан явно, иначе файл `JIRA_JOURNAL_PATH`.
    Отказ базы не подменяется файлом: два журнала отвечали бы на «уже
    отправлено?» по-разному, и повтор завёл бы задачи второй раз.
    """
    if cfg.postgres_configured():
        return _postgres
    path = Path(cfg.jira_journal_path()).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    with _lock:
        found = _files.get(path)
        if found is None or not path.exists():
            found = _files[path] = SqliteActions(path)
        return found


def legacy_rows(thread: str, project: str, plan_fingerprint: str) -> list[dict]:
    """Операции старого журнала для точно того же плана и треда."""
    path = Path(cfg.jira_journal_path()).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.is_file():
        return []
    old_run = run_key(thread, project, plan_fingerprint)
    try:
        with sqlite3.connect(path, timeout=10) as conn:
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'operations'"
            ).fetchone():
                return []
            rows = conn.execute(
                "SELECT kind, local, state, label, remote, url, detail FROM operations WHERE run = ?",
                (old_run,),
            ).fetchall()
    except (sqlite3.Error, OSError) as exc:
        raise JournalUnavailable(f"старый журнал Jira не прочитан ({exc})") from exc
    return [dict(zip(("kind", "local", "state", "label", "remote", "url", "detail"), row,
                     strict=True)) for row in rows]


class Bound:
    """Журнал с действием: каждая начатая операция помечается его id."""

    def __init__(self, journal: Any, action: str) -> None:
        self._journal = journal
        self.action = action

    def __getattr__(self, name: str) -> Any:
        return getattr(self._journal, name)

    def begin(self, run: str, kind: str, local: str, **kwargs: Any) -> str:
        return self._journal.begin(run, kind, local, action=self.action, **kwargs)


class Recorder:
    """
    Запись одного действия: предложение, согласие, итог.

    `strict=False` — для публикации: страница уезжает и без журнала, и отказ
    базы не должен её останавливать; причина остаётся в `error` и попадает в
    итог. `strict=True` — для заведения задач: без журнала повтор не отличить
    от первого раза, и отказ поднимается `JournalUnavailable`.
    """

    def __init__(self, proposal: Mapping[str, Any], *, strict: bool = False,
                 journal: Any = None) -> None:
        self.proposal = dict(proposal)
        self.strict = strict
        self.error = ""
        self._journal = journal

    @property
    def journal(self) -> Any:
        if self._journal is None:
            self._journal = store()
        return self._journal

    def _call(self, action):
        if self.error and not self.strict:
            return None
        try:
            return action(self.journal)
        except JournalUnavailable as exc:
            if self.strict:
                raise
            self.error = str(exc)
            return None

    def operations(self) -> Bound:
        """Журнал операций с id этого действия."""
        return Bound(self.journal, self.proposal["id"])

    def step(self, action) -> Any:
        """Шаг журнала операций с той же терпимостью к отказу, что у записи действия."""
        return self._call(lambda j: action(Bound(j, self.proposal["id"])))

    def propose(self) -> None:
        self._call(lambda j: j.save_action(self.proposal, PROPOSED))

    def decide(self, approval: Mapping[str, Any]) -> None:
        def write(j):
            j.save_action(self.proposal, status_of(approval))
            j.add_approval(self.proposal["id"], approval, actor())

        self._call(write)

    def done(self, status: str, result: Mapping[str, Any] | None = None) -> None:
        def write(j):
            j.save_action(self.proposal, status)
            j.set_status(self.proposal["id"], status, result or {})

        self._call(write)
