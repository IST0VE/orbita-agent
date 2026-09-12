"""Normalization primitives for ordered, duplicate-safe runtime events."""

from __future__ import annotations

from collections import OrderedDict, deque
from copy import deepcopy
from datetime import UTC, datetime
from itertools import islice
from threading import Lock
from typing import Any
from uuid import uuid4

from agent.ui_engine.redaction import redact

EVENT_TYPES = frozenset(
    {
        "thread.created",
        "run.created",
        "run.queued",
        "run.started",
        "node.queued",
        "node.started",
        "node.update",
        "node.completed",
        "node.failed",
        "interrupt.created",
        "interrupt.resolved",
        "state.snapshot",
        "run.completed",
        "run.failed",
        "run.cancelled",
        "connection.warning",
        "tool.started",
        "tool.completed",
        "tool.failed",
        "llm.started",
        "llm.token",
        "llm.completed",
        "artifact.created",
        "artifact.updated",
        "publication.started",
        "publication.completed",
        "metric.recorded",
        "log.created",
    }
)


class _RunLog:
    """Порядок, окно повтора и ключи дедупликации одного прогона."""

    __slots__ = ("events", "ids", "sequence")

    def __init__(self, retention: int) -> None:
        self.sequence = 0
        self.events: deque[dict[str, Any]] = deque(maxlen=retention)
        self.ids: dict[str, dict[str, Any]] = {}


class EventNormalizer:
    """
    Assign per-run sequences and retain a bounded replay window.

    Всё, что помнится о прогоне, лежит в одном журнале и выбрасывается разом.
    Отдельный набор увиденных идентификаторов рос бы вечно: события из окна
    повтора дек давно вытеснил, а ключи от них остались бы в памяти процесса
    до перезапуска.
    """

    def __init__(self, *, retention: int = 10_000, runs: int = 200) -> None:
        self._retention = retention
        self._runs = runs
        self._logs: OrderedDict[str, _RunLog] = OrderedDict()
        self._lock = Lock()

    def _log(self, run_id: str) -> _RunLog:
        log = self._logs.get(run_id)
        if log is not None:
            self._logs.move_to_end(run_id)
            return log
        log = _RunLog(self._retention)
        self._logs[run_id] = log
        while len(self._logs) > self._runs:
            self._logs.popitem(last=False)
        return log

    def emit(
        self,
        event_type: str,
        data: Any,
        *,
        graph_id: str,
        assistant_id: str,
        thread_id: str,
        run_id: str,
        event_id: str | None = None,
        timestamp: str | None = None,
        redaction_rules: list[dict[str, Any]] | None = None,
        roles: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unsupported runtime event type: {event_type}")
        identity = event_id or str(uuid4())
        with self._lock:
            log = self._log(run_id)
            known = log.ids.get(identity)
            if known is not None:
                return deepcopy(known)
            log.sequence += 1
            event = {
                "eventId": identity,
                "sequence": log.sequence,
                "timestamp": timestamp or datetime.now(UTC).isoformat(),
                "graphId": graph_id,
                "assistantId": assistant_id,
                "threadId": thread_id,
                "runId": run_id,
                "type": event_type,
                "data": redact(data, redaction_rules or (), roles=roles),
                "schemaVersion": "1.0",
            }
            if len(log.events) == self._retention:
                log.ids.pop(log.events[0]["eventId"], None)
            log.events.append(event)
            log.ids[identity] = event
            return deepcopy(event)

    def replay(self, run_id: str, *, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        size = max(1, min(limit, 1000))
        with self._lock:
            log = self._logs.get(run_id)
            if log is None:
                return []
            selected = list(islice(
                (event for event in log.events if event["sequence"] > after), size,
            ))
        # Stored events are never mutated. Keep only selection under the lock;
        # copying large payloads must not block emitters or other readers.
        return deepcopy(selected)


events = EventNormalizer()
