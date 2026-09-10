"""Batch per metric across all services, with bounded parallelism and source fallback."""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

from agent.integrations.influx import InfluxDB
from agent.integrations.kubernetes import Kubernetes
from agent.integrations.load_testing import HTTPLoadTesting
from agent.integrations.prometheus import Prometheus
from agent.nt.metric_profiles import unit_for
from agent.nt.models import failure
from agent.nt.settings import Settings


class Sources:
    def __init__(self, settings: Settings, *, prometheus=None, influx=None, kubernetes=None,
                 load_testing=None):
        self.settings = settings
        self.prometheus, self.influx = prometheus, influx
        self.kubernetes, self.load_testing = kubernetes, load_testing

    @classmethod
    def from_env(cls, settings):
        limits = {"max_series": settings.max_series, "max_points": settings.max_points,
                  "max_window": settings.max_window}
        return cls(settings,
            prometheus=Prometheus(os.environ["NT_PROMETHEUS_URL"], os.getenv("NT_PROMETHEUS_TOKEN", ""),
                **limits) if os.getenv("NT_PROMETHEUS_URL") else None,
            influx=InfluxDB(os.environ["NT_INFLUX_URL"], os.getenv("NT_INFLUX_TOKEN", ""),
                version=int(os.getenv("NT_INFLUX_VERSION", "2")),
                database=os.getenv("NT_INFLUX_DATABASE", ""), org=os.getenv("NT_INFLUX_ORG", ""),
                mappings={m: s["influx"] for m, s in settings.queries.items() if s.get("influx")},
                **limits) if os.getenv("NT_INFLUX_URL") else None,
            kubernetes=Kubernetes(os.environ["NT_KUBERNETES_URL"], os.getenv("NT_KUBERNETES_TOKEN", ""))
                if os.getenv("NT_KUBERNETES_URL") else None,
            load_testing=HTTPLoadTesting(os.environ["NT_LOAD_TESTING_URL"],
                os.getenv("NT_LOAD_TESTING_TOKEN", "")) if os.getenv("NT_LOAD_TESTING_URL") else None)

    def query(self, metric: str, state: dict, start, end, *, source: str | None = None) -> dict:
        spec = self.settings.queries.get(metric)
        if not spec:
            return failure("METRIC_NOT_ALLOWED", "metric is not configured on the server")
        results, errors = [], []
        filters = {k: state[k] for k in ("namespace", "environment") if state.get(k)}
        if self.prometheus and spec.get("prometheus") and source in {None, "prometheus"}:
            query = spec["prometheus"]
            # Placeholders include their quotes. Never interpolate source text into PromQL.
            for key, value in filters.items():
                query = query.replace("{{" + key + "}}", json.dumps(str(value)))
            if "{{" in query:
                errors.append(failure("MISSING_QUERY_CONTEXT", "unresolved query placeholder"))
            else:
                results.append(self.prometheus.range_query(query, start, end, self.settings.step,
                                                          metric=metric, unit=unit_for(metric)))
        if self.influx and spec.get("influx") and source in {None, "influx"}:
            results.append(self.influx.query_metric(metric, start, end, filters, unit=unit_for(metric)))
        selected = {}
        for result in results:
            if not result.get("success"):
                errors.append(result)
                continue
            if result.get("partial"):
                errors.append(failure("PARTIAL_SOURCE", "source reported partial results"))
            grouped = {}
            for item in result.get("series", []):
                grouped.setdefault(item["service"], []).append(item)
            for service, items in grouped.items():
                if len(items) != 1:
                    errors.append(failure("AMBIGUOUS_SERIES", "query must aggregate by service"))
                    continue
                item = items[0]
                # No duplicate source mixing. An empty primary series can fall back.
                if not item["values"]:
                    continue
                if item.get("unit") != unit_for(metric):
                    errors.append(failure("UNIT_MISMATCH", "unexpected metric unit"))
                    continue
                profile = state.get("component_profiles", {}).get(service)
                profiles = spec.get("profiles", [spec.get("profile", "http")])
                if profile and profile not in profiles:
                    continue
                if any(item.get("labels", {}).get(k) not in {None, v} for k, v in filters.items()):
                    continue
                if state.get("scope_explicit") and service not in state.get("services", []):
                    continue
                # Both adapters use the same half-open interval, including Prometheus.
                points = [(t, v) for t, v in zip(item["timestamps"], item["values"], strict=True)
                          if start <= t < end]
                valid = [(t, v) for t, v in points if v >= 0 and (item["unit"] != "ratio" or v <= 1)]
                item = {**item, "timestamps": [t for t, _ in valid], "values": [v for _, v in valid],
                        "invalid_points": item.get("invalid_points", 0) + len(points) - len(valid),
                        "partial": bool(result.get("partial"))}
                if item["values"]:
                    # Prefer a clean complete alternative over a sparse/partial primary.
                    # Never merge sources, and retain Prometheus on an equal-quality tie.
                    previous = selected.get(service)
                    def quality(s):
                        return (not s.get("partial"), not s.get("invalid_points"), len(s["values"]))
                    if previous is None or quality(item) > quality(previous):
                        selected[service] = item
        if not results:
            errors.append(failure("SOURCE_NOT_CONFIGURED", "no configured source for metric"))
        return {"success": bool(selected), "series": list(selected.values()), "errors": errors}

    def collect(self, state: dict, start, end) -> dict:
        def fetch(metric):
            if time.time() >= state.get("deadline_at", float("inf")):
                return metric, failure("TIMEOUT", "analysis deadline reached")
            try:
                return metric, self.query(metric, state, start, end)
            except Exception:
                # Do not reflect adapter exceptions which may contain URLs/tokens.
                return metric, failure("SOURCE_ERROR", "metric fetch failed")

        series, errors, points = [], [], 0
        with ThreadPoolExecutor(max_workers=self.settings.concurrency) as executor:
            for metric, result in executor.map(fetch, sorted(self.settings.queries)):
                for error in result.get("errors", []):
                    errors.append({"metric": metric, **error})
                if not result.get("success"):
                    errors.append({"metric": metric, "error_type": result.get("error_type", "NO_DATA"),
                                   "message": result.get("message", "no data for metric")})
                for item in result.get("series", []):
                    points += len(item["values"])
                    if points > self.settings.max_points:
                        errors.append({"metric": metric, "error_type": "POINT_LIMIT",
                                       "message": "total point limit exceeded"})
                        break
                    series.append(item)
        return {"series": series, "errors": errors, "points": sum(len(s["values"]) for s in series)}
