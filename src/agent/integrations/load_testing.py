"""Backend-neutral contract plus a read-only HTTP gateway for completed test metadata.

The optional gateway exposes GET /tests/{id} and GET /tests/{id}/results.
Control methods intentionally have no HTTP implementation in MVP 1.
"""

import re
from typing import Protocol

from agent.integrations.http import AdapterError, HTTPClient
from agent.nt.context import validate_fields
from agent.nt.models import failure


class LoadTestingBackend(Protocol):
    def prepare_test(self, plan: dict) -> dict: ...
    def start_test(self, prepared_id: str) -> dict: ...
    def stop_test(self, test_id: str) -> dict: ...
    def get_test_status(self, test_id: str) -> dict: ...
    def get_test_results(self, test_id: str) -> dict: ...


class HTTPLoadTesting:
    def __init__(self, url: str, token: str = ""):
        self.http = HTTPClient(url, token, max_bytes=64000)

    def _get(self, test_id, suffix=""):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", test_id):
            return failure("INVALID_TEST_ID", "invalid test identifier")
        try:
            data = self.http.json("GET", f"/tests/{test_id}{suffix}")
            if not isinstance(data, dict):
                raise AdapterError("INVALID_RESPONSE")
            allowed = {"test_id", "test_status", "started_at", "finished_at", "target_service",
                       "namespace", "environment", "target_rps", "duration_seconds", "scenario",
                       "sla_p95_ms", "sla_p99_ms", "sla_error_rate", "sla_max_cpu", "sla_max_memory"}
            fields, errors = validate_fields({k: v for k, v in data.items() if k in allowed})
            if errors:
                return failure("INVALID_TEST_METADATA", "invalid fields in test metadata")
            return {"success": True, "data": fields}
        except (AdapterError, ValueError):
            return failure("LOAD_TESTING_UNAVAILABLE", "test metadata unavailable")

    def get_test_status(self, test_id):
        return self._get(test_id)

    def get_test_results(self, test_id):
        return self._get(test_id, "/results")

    def prepare_test(self, plan):
        return failure("POLICY_DENIED", "MVP 1 is read-only")

    def start_test(self, prepared_id):
        return failure("POLICY_DENIED", "MVP 1 is read-only")

    def stop_test(self, test_id):
        return failure("POLICY_DENIED", "MVP 1 is read-only")
