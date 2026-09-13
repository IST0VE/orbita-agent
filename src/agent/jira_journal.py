"""
Журнал операций записи в Jira: что уже уехало и чем это кончилось.

Заведение задачи — единственное место конвейера, где повтор узла стоит денег
чужой команде. Узел LangGraph может выполниться дважды: упал процесс между
принятым POST и сохранением результата, оборвалась сеть на середине пачки,
оператор возобновил тред из checkpoint. До этого журнала повтор просто заводил
задачи заново — тихо, по второму разу, с уведомлениями всей доске.

Отсюда три состояния и ни одного лишнего:

    pending     запрос отправлен, ответа ещё нет. Повторять вслепую нельзя:
                трекер мог его принять;
    completed   ответ получен, ключ известен. Повторять нечего;
    unknown     ответа нет и сверка не удалась. Решение — за человеком.

Сверка идёт по устойчивой метке: она считается из ключа прогона и локального
ключа карточки, уезжает в `labels` вместе с задачей и потому находится в
трекере даже тогда, когда ответ на POST потерялся. Метка детерминирована —
повтор считает ту же самую, — и этим отличается от идемпотентного ключа,
который Jira не поддерживает.

Хранилище — SQLite рядом с данными проекта: журнал обязан переживать перезапуск
процесса, иначе он отвечает только на вопросы того же прогона, который и так
всё помнит.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from agent import config as cfg

PENDING = "pending"
COMPLETED = "completed"
UNKNOWN = "unknown"
FAILED = "failed"

#: Префикс метки операции. По нему же её видно в самой Jira: задача, заведённая
#: конвейером, помечена, и найти её потом можно не только по журналу.
LABEL_PREFIX = "orbita-op"


def label_for(run: str, kind: str, local: str) -> str:
    """
    Устойчивая метка операции: одна и та же у прогона и у его повтора.

    Длина ограничена: Jira принимает метку до 255 знаков, но короткая метка
    читается в интерфейсе трекера, а длины отпечатка хватает, чтобы не
    столкнуться с чужой.
    """
    digest = hashlib.sha256(f"{run}\n{kind}\n{local}".encode()).hexdigest()[:16]
    return f"{LABEL_PREFIX}-{digest}"


def run_key(*parts: str) -> str:
    """Ключ прогона: из треда, проекта и отпечатка плана — см. `jira_graph`."""
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:32]


class Journal:
    """Операции записи в Jira, переживающие перезапуск процесса."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path or cfg.jira_journal_path()).expanduser()
        if not self.path.is_absolute():
            self.path = Path.cwd() / self.path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """CREATE TABLE IF NOT EXISTS operations (
                    run TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    local TEXT NOT NULL,
                    state TEXT NOT NULL,
                    label TEXT NOT NULL,
                    remote TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    detail TEXT NOT NULL DEFAULT '',
                    updated REAL NOT NULL,
                    PRIMARY KEY (run, kind, local))"""
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def record(self, run: str, kind: str, local: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM operations WHERE run=? AND kind=? AND local=?", (run, kind, local)
            ).fetchone()
        return dict(row) if row else None

    def records(self, run: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM operations WHERE run=? ORDER BY updated", (run,)
            ).fetchall()
        return [dict(row) for row in rows]

    def begin(self, run: str, kind: str, local: str) -> str:
        """
        Отметить операцию начатой и вернуть её метку.

        Запись делается ДО запроса: журнал, заполняемый после ответа, не знает
        ровно о том случае, ради которого он заведён, — об оборванном ответе
        на принятый трекером POST.
        """
        label = label_for(run, kind, local)
        with self._connect() as db:
            db.execute(
                """INSERT INTO operations (run, kind, local, state, label, updated)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(run, kind, local) DO UPDATE SET state=excluded.state,
                       updated=excluded.updated""",
                (run, kind, local, PENDING, label, time.time()),
            )
        return label

    def finish(
        self, run: str, kind: str, local: str, *, remote: str = "", url: str = "", detail: str = ""
    ) -> None:
        self._set(run, kind, local, COMPLETED, remote=remote, url=url, detail=detail)

    def fail(self, run: str, kind: str, local: str, detail: str) -> None:
        """Трекер отказал явно: запроса, который мог пройти, не было."""
        self._set(run, kind, local, FAILED, detail=detail)

    def unresolved(self, run: str, kind: str, local: str, detail: str) -> None:
        """Ответа нет и сверка не удалась. Дальше решает человек."""
        self._set(run, kind, local, UNKNOWN, detail=detail)

    def _set(
        self,
        run: str,
        kind: str,
        local: str,
        state: str,
        *,
        remote: str = "",
        url: str = "",
        detail: str = "",
    ) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE operations SET state=?, remote=?, url=?, detail=?, updated=?
                   WHERE run=? AND kind=? AND local=?""",
                (state, remote, url, detail, time.time(), run, kind, local),
            )
