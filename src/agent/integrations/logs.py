"""Backend contract and bounded grouping; no concrete log system assumed."""

from collections import Counter
from collections.abc import Iterable
from typing import Protocol

from agent import confluence


class LogsBackend(Protocol):
    def get_logs(self, service: str, start: float, end: float, query: str | None = None) -> dict:
        """Read a bounded time range; implement timeouts and byte limits at transport."""
        ...


def group_errors(lines: Iterable[str], *, max_lines=500, max_chars=12000) -> dict:
    counts, consumed, size, truncated = Counter(), 0, 0, False
    for line in lines:
        if consumed >= max_lines or size + len(line) > max_chars:
            truncated = True
            break
        text = confluence.mask_text(line[:1000])
        counts[text] += 1
        consumed += 1
        size += len(line)
    return {"success": True, "lines_read": consumed, "truncated": truncated,
            "groups": [{"message": text, "count": n} for text, n in counts.most_common(20)]}
