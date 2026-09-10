"""InfluxDB 2 Flux / InfluxDB 3 SQL behind one read-only metric interface.

Mappings refer to pre-aggregated series (one value per service and timestamp).
No averaging of exported quantiles or mixing different request populations.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import UTC, datetime

from agent.integrations.http import AdapterError, HTTPClient
from agent.nt.models import MetricSeries, failure, success, window


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("invalid Influx identifier")
    return '"' + value + '"'


class InfluxDB:
    def __init__(self, url: str, token: str = "", *, version: int = 2, database: str = "",
                 org: str = "", mappings: dict | None = None, max_points=200000,
                 max_series=500, max_window=86400, **kwargs):
        if version not in {2, 3}:
            raise ValueError("supported Influx versions: 2, 3")
        self.http = HTTPClient(url, token, auth_scheme="Token" if version == 2 else "Bearer",
                               **kwargs)
        self.version, self.database, self.org = version, database, org
        self.mappings = mappings or {}
        self.max_points, self.max_series, self.max_window = max_points, max_series, max_window

    def query_metric(self, metric, start, end, filters, *, unit: str) -> dict:
        try:
            start, end = window(start, end, max_seconds=self.max_window)
            mapping = self.mappings[metric]
            measurement, field = mapping["measurement"], mapping.get("field", "value")
            dates = [datetime.fromtimestamp(t, UTC).isoformat() for t in (start, end)]
            if self.version == 2:
                query = (f"from(bucket: {json.dumps(self.database)})"
                         f" |> range(start: time(v: {json.dumps(dates[0])}), "
                         f"stop: time(v: {json.dumps(dates[1])}))"
                         f" |> filter(fn: (r) => r._measurement == {json.dumps(measurement)}"
                         f" and r._field == {json.dumps(field)})")
                for key, value in sorted(filters.items()):
                    identifier(key)
                    query += f" |> filter(fn: (r) => r[{json.dumps(key)}] == {json.dumps(str(value))})"
                body = self.http.request("POST", "/api/v2/query", params={"org": self.org},
                    headers={"Accept": "application/csv", "Content-Type": "application/json"},
                    json={"query": query, "type": "flux", "dialect": {"annotations": []}})
                # Flux may repeat headers between tables and prepend annotations.
                rows, header = [], None
                for row in csv.reader(io.StringIO(body)):
                    if not row or row[0].startswith("#"):
                        continue
                    if "_time" in row and "_value" in row:
                        header = row
                    elif header:
                        rows.append(dict(zip(header, row, strict=True)))
                    else:
                        raise AdapterError("INVALID_CSV")
            else:
                predicates = ["time >= $start", "time < $end"]
                params = {"start": dates[0], "end": dates[1]}
                for index, (key, value) in enumerate(sorted(filters.items())):
                    predicates.append(f"{identifier(key)} = $f{index}")
                    params[f"f{index}"] = str(value)
                query = (f"SELECT time AS _time, service, {identifier(field)} AS _value "
                         f"FROM {identifier(measurement)} WHERE " + " AND ".join(predicates)
                         + f" ORDER BY time LIMIT {self.max_points + 1}")
                rows = self.http.json("POST", "/api/v3/query_sql", json={
                    "db": self.database, "q": query, "params": params, "format": "json"})
            if not isinstance(rows, list) or len(rows) > self.max_points:
                raise AdapterError("POINT_LIMIT_OR_INVALID_RESPONSE")
            grouped = {}
            for row in rows:
                service = row["service"]
                if not service:
                    raise AdapterError("MISSING_SERVICE_LABEL")
                grouped.setdefault(service, []).append((row["_time"], row["_value"]))
            if len(grouped) > self.max_series:
                raise AdapterError("SERIES_LIMIT")
            return success([MetricSeries(metric, service, [p[0] for p in points],
                [p[1] for p in points], unit, "influx", dict(filters))
                for service, points in sorted(grouped.items())])
        except (AdapterError, KeyError, ValueError, TypeError, AttributeError) as exc:
            return failure("INFLUX_UNAVAILABLE", str(exc) if isinstance(exc, AdapterError)
                           else "invalid metric mapping or response")
