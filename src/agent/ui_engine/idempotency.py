"""Small process-local guard against repeated UI action submissions."""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from threading import Lock
from typing import Any

from agent.ui_engine.manifests import canonical_json


class IdempotencyConflict(ValueError):
    pass


class IdempotencyLedger:
    def __init__(self, limit: int = 10_000) -> None:
        self._limit = limit
        self._items: OrderedDict[str, str] = OrderedDict()
        self._lock = Lock()

    def register(self, key: str, payload: Any) -> bool:
        if not key or len(key) > 200:
            raise IdempotencyConflict("invalid idempotency key")
        digest = hashlib.sha256(canonical_json(payload)).hexdigest()
        with self._lock:
            previous = self._items.get(key)
            if previous is not None:
                if previous != digest:
                    raise IdempotencyConflict("idempotency key was reused with another payload")
                self._items.move_to_end(key)
                return False
            self._items[key] = digest
            while len(self._items) > self._limit:
                self._items.popitem(last=False)
            return True


actions = IdempotencyLedger()
