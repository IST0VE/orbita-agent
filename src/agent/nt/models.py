"""Source-neutral series; all timestamps are UTC epoch seconds, units explicit."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime


def timestamp(value: str | float | int) -> float:
    if isinstance(value, bool):
        raise ValueError("timestamp must be an epoch or timezone-aware ISO datetime")
    try:
        result = float(value)
    except (TypeError, ValueError):
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timestamp requires an explicit timezone") from None
        result = parsed.timestamp()
    if not math.isfinite(result):
        raise ValueError("timestamp must be finite")
    return result


def window(start, end, *, max_seconds: float = 86400) -> tuple[float, float]:
    start, end = timestamp(start), timestamp(end)
    if not 0 < end - start <= max_seconds:
        raise ValueError("invalid or excessive time window")
    return start, end


@dataclass
class MetricSeries:
    metric: str
    service: str
    timestamps: list[float]
    values: list[float]
    unit: str
    source: str
    labels: dict[str, str] = field(default_factory=dict)
    invalid_points: int = 0

    def __post_init__(self):
        if len(self.timestamps) != len(self.values):
            raise ValueError("timestamps and values have different lengths")
        points = {}
        for time, value in zip(self.timestamps, self.values, strict=True):
            time, value = timestamp(time), float(value)
            if math.isfinite(value):
                if time in points:
                    raise ValueError("duplicate timestamp; aggregate at the source")
                points[time] = value
            else:
                self.invalid_points += 1
        self.timestamps = sorted(points)
        self.values = [points[t] for t in self.timestamps]

    def to_dict(self) -> dict:
        return asdict(self)


def failure(kind: str, message: str) -> dict:
    return {"success": False, "error_type": kind, "message": message}


def success(series: list[MetricSeries], **extra) -> dict:
    return {"success": True, "series": [s.to_dict() for s in series], **extra}
