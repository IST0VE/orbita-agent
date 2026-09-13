"""Authenticated control API. Idempotency belongs to the runner, not to a checkpoint."""

import os
import re

from agent.integrations.http import AdapterError, HTTPClient


class RunnerHTTP:
    def __init__(self, url: str, token: str = ""):
        self.http = HTTPClient(url, token, timeout=10, max_bytes=256000, retries=1)

    @classmethod
    def from_env(cls):
        url = os.getenv("NT_RUNNER_URL", "")
        if not url:
            raise ValueError("Настройте NT_RUNNER_URL для запуска НТ")
        return cls(url, os.getenv("NT_RUNNER_TOKEN", ""))

    def call(self, method, path, **kwargs):
        data = self.http.json(method, path, **kwargs)
        if not isinstance(data, dict) or data.get("success") is not True:
            raise AdapterError("RUNNER_OPERATION_FAILED")
        return data["data"]

    def capabilities(self):
        return self.call("GET", "/capabilities")

    def prepare_test(self, plan, key):
        return self.call("POST", "/prepare", json={"plan": plan, "key": key})

    def start_test(self, prepared_id, key, *, smoke=False):
        return self.call("POST", "/start", json={"prepared_id": prepared_id, "key": key, "smoke": smoke})

    def _path(self, test_id):
        if not isinstance(test_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", test_id):
            raise ValueError("invalid test identifier")
        return "/tests/" + test_id

    def get_test_status(self, test_id):
        data = self.call("GET", self._path(test_id))
        if data.get("test_id") != test_id:
            raise AdapterError("RUNNER_TEST_ID_MISMATCH")
        return data

    def get_test_results(self, test_id):
        data = self.call("GET", self._path(test_id) + "/results")
        if data.get("test_id") != test_id:
            raise AdapterError("RUNNER_TEST_ID_MISMATCH")
        return data

    def stop_test(self, test_id):
        return self.call("POST", self._path(test_id) + "/stop", json={})

    def heartbeat(self, test_id):
        return self.call("POST", self._path(test_id) + "/heartbeat", json={})
