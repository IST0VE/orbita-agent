"""Create or update the single Orbita review comment on a GitHub pull request."""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

MARKER = "<!-- orbita-system-analysis-ci -->"


def _request(method: str, url: str, token: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read()
    return json.loads(payload.decode("utf-8")) if payload else None


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: upsert_orbita_pr_comment.py REPORT.md")

    token = os.environ["GITHUB_TOKEN"]
    repository = os.environ["GITHUB_REPOSITORY"]
    pr_number = os.environ["PR_NUMBER"]
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    body = Path(sys.argv[1]).read_text(encoding="utf-8")
    if MARKER not in body:
        raise SystemExit("report does not contain the Orbita marker")

    comments_url = f"{api}/repos/{repository}/issues/{pr_number}/comments?per_page=100"
    comments = _request("GET", comments_url, token)
    existing = next(
        (
            comment
            for comment in comments
            if MARKER in str(comment.get("body") or "")
            and str((comment.get("user") or {}).get("type") or "").lower() == "bot"
        ),
        None,
    )

    if existing is None:
        target = f"{api}/repos/{repository}/issues/{pr_number}/comments"
        _request("POST", target, token, {"body": body})
        print("Created Orbita PR comment")
    else:
        target = f"{api}/repos/{repository}/issues/comments/{existing['id']}"
        _request("PATCH", target, token, {"body": body})
        print("Updated Orbita PR comment")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
