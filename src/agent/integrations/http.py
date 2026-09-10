"""Small bounded HTTP transport. No redirects, response bodies or credentials in logs."""

from __future__ import annotations

import json
import logging
import time
from urllib.parse import urlsplit

import requests

log = logging.getLogger(__name__)


class AdapterError(RuntimeError):
    pass


class HTTPClient:
    def __init__(self, url: str, token: str = "", *, timeout: float = 10,
                 max_bytes: int = 8_000_000, retries: int = 1, auth_scheme: str = "Bearer"):
        parsed = urlsplit(url)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username:
            raise ValueError("adapter URL requires HTTP(S) without embedded credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("adapter URL must not contain query or fragment")
        if not 0 < timeout <= 30 or not 0 <= retries <= 2 or max_bytes <= 0:
            raise ValueError("invalid transport limits")
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"{auth_scheme} {token}"} if token else {}
        self.timeout, self.max_bytes, self.retries = timeout, max_bytes, retries

    def request(self, method: str, path: str, **kwargs) -> str:
        began = time.monotonic()
        status = "error"
        headers = {**self.headers, **kwargs.pop("headers", {})}
        try:
            for attempt in range(self.retries + 1):
                try:
                    with requests.request(
                        method, self.url + path, headers=headers,
                        timeout=self.timeout, allow_redirects=False, stream=True, **kwargs,
                    ) as response:
                        if response.status_code in {429, 502, 503, 504} and attempt < self.retries:
                            time.sleep(0.2 * (attempt + 1))
                            continue
                        if not 200 <= response.status_code < 300:
                            raise AdapterError(f"HTTP_{response.status_code}")
                        body = bytearray()
                        for chunk in response.iter_content(16384):
                            body.extend(chunk)
                            if len(body) > self.max_bytes:
                                raise AdapterError("RESPONSE_TOO_LARGE")
                            if time.monotonic() - began > self.timeout * (self.retries + 1):
                                raise AdapterError("TIMEOUT")
                        status = "success"
                        return body.decode("utf-8")
                except (requests.Timeout, requests.ConnectionError):
                    if attempt == self.retries:
                        raise AdapterError("UNAVAILABLE") from None
                    time.sleep(0.2 * (attempt + 1))
                except requests.RequestException:
                    raise AdapterError("REQUEST_FAILED") from None
            raise AdapterError("UNAVAILABLE")
        finally:
            log.info("nt_adapter method=%s path=%s status=%s duration_ms=%d",
                     method, path, status, (time.monotonic() - began) * 1000)

    def json(self, method: str, path: str, **kwargs):
        try:
            return json.loads(self.request(method, path, **kwargs))
        except (ValueError, UnicodeError):
            raise AdapterError("INVALID_RESPONSE") from None
