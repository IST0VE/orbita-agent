"""Prometheus HTTP API -> canonical finite MetricSeries, never raw API data to LLM."""

from __future__ import annotations

from agent.integrations.http import AdapterError, HTTPClient
from agent.nt.models import MetricSeries, failure, success, timestamp, window


class Prometheus:
    def __init__(self, url: str, token: str = "", *, max_series=500,
                 max_points=200000, max_window=86400, **kwargs):
        self.http = HTTPClient(url, token, **kwargs)
        self.max_series, self.max_points, self.max_window = max_series, max_points, max_window

    def _query(self, path, params, metric, unit):
        try:
            response = self.http.json("GET", path, params={**params, "timeout": "10s"})
            if response.get("status") != "success":
                raise AdapterError("QUERY_FAILED")
            data = response["data"]
            kind = data["resultType"]
            if kind not in {"matrix", "vector"}:
                raise AdapterError("EXPECTED_SERVICE_SERIES")
            rows = data["result"]
            if len(rows) > self.max_series:
                raise AdapterError("SERIES_LIMIT")
            series, count = [], 0
            for row in rows:
                labels = row["metric"]
                service = labels.get("service")
                if not service:
                    raise AdapterError("MISSING_SERVICE_LABEL")
                points = row.get("values", []) if kind == "matrix" else [row["value"]]
                count += len(points)
                if count > self.max_points:
                    raise AdapterError("POINT_LIMIT")
                series.append(MetricSeries(metric, service, [p[0] for p in points],
                                           [p[1] for p in points], unit, "prometheus", labels))
            return success(series, partial=bool(response.get("warnings")))
        except (AdapterError, ValueError, KeyError, TypeError, AttributeError) as exc:
            return failure("PROMETHEUS_UNAVAILABLE", str(exc) if isinstance(exc, AdapterError)
                           else "invalid metric response")

    def instant_query(self, query: str, *, metric: str, unit: str, time=None) -> dict:
        try:
            params = {"query": query}
            if time is not None:
                params["time"] = timestamp(time)
            return self._query("/api/v1/query", params, metric, unit)
        except ValueError:
            return failure("INVALID_WINDOW", "invalid instant timestamp")

    def series(self, match: str, start, end, *, limit: int = 2000) -> dict:
        """Наборы меток под селектор: какие метрики вообще есть у этого сервиса.

        Запрос метаданных, а не значений: Prometheus отвечает списком меток без
        точек, поэтому он дёшев там, где `query_range` по тому же селектору
        развернул бы тысячи рядов.
        """
        try:
            start, end = window(start, end, max_seconds=self.max_window)
        except (ValueError, TypeError):
            return failure("INVALID_WINDOW", "invalid discovery range")
        try:
            response = self.http.json("GET", "/api/v1/series", params={
                "match[]": match, "start": start, "end": end, "limit": limit})
            if response.get("status") != "success":
                raise AdapterError("QUERY_FAILED")
            rows = [row for row in response.get("data", []) if isinstance(row, dict)]
            return {"success": True, "series": rows[:limit],
                    "truncated": len(rows) > limit or bool(response.get("warnings"))}
        except (AdapterError, ValueError, KeyError, TypeError, AttributeError) as exc:
            return failure("PROMETHEUS_UNAVAILABLE", str(exc) if isinstance(exc, AdapterError)
                           else "invalid series response")

    def metadata(self, *, limit: int = 500) -> dict:
        """Тип и HELP метрики так, как их объявил exporter."""
        try:
            response = self.http.json("GET", "/api/v1/metadata", params={"limit": limit})
            if response.get("status") != "success":
                raise AdapterError("QUERY_FAILED")
            data = response.get("data", {})
            if not isinstance(data, dict):
                raise AdapterError("INVALID_METADATA")
            described = {}
            for name, entries in data.items():
                entry = entries[0] if isinstance(entries, list) and entries else {}
                if isinstance(entry, dict):
                    described[name] = {"type": str(entry.get("type", "")),
                                       "help": str(entry.get("help", ""))}
            return {"success": True, "metadata": described}
        except (AdapterError, ValueError, KeyError, TypeError, AttributeError) as exc:
            return failure("PROMETHEUS_UNAVAILABLE", str(exc) if isinstance(exc, AdapterError)
                           else "invalid metadata response")

    def range_query(self, query: str, start, end, step: float, *, metric: str, unit: str) -> dict:
        try:
            start, end = window(start, end, max_seconds=self.max_window)
            if not 0 < step <= self.max_window or (end - start) / step > self.max_points:
                raise ValueError("invalid step")
        except (ValueError, TypeError):
            return failure("INVALID_WINDOW", "invalid range or step")
        return self._query("/api/v1/query_range", {"query": query, "start": start,
                           "end": end, "step": step}, metric, unit)
