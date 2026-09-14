"""Local k6 runner: SQLite identities, isolated worker processes and bounded artifacts."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from agent.nt_run.plan import (
    canonical,
    check_commitment,
    compile_script,
    fingerprint,
    validate_plan,
)

TERMINAL = {"completed", "failed", "stopped"}


class Runner:
    def __init__(self, root: Path, config: dict):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.database = self.root / "runner.sqlite3"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS prepared (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, prepared_id TEXT NOT NULL, smoke INTEGER NOT NULL,
                status TEXT NOT NULL, heartbeat REAL NOT NULL, updated REAL NOT NULL,
                stop INTEGER NOT NULL DEFAULT 0, result TEXT NOT NULL DEFAULT '{}')""")

    def connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def capabilities(self):
        return {"engine": "k6", "idempotent_start": True, "watchdog": True, "lease": True,
                "limits": {"max_rps": int(self.config.get("max_rps", 100)),
                           "max_vus": int(self.config.get("max_vus", 20)),
                           "max_duration_seconds": int(self.config.get("max_duration_seconds", 600))},
                "targets": {name: {k: v for k, v in target.items() if k in {
                    "url", "target_service", "environment", "namespace"}}
                    for name, target in self.config["targets"].items()}}

    def prepare_test(self, data, key, approved=None):
        # The approved set is checked here, not only in the graph: the graph's
        # "target" is a logical key, and the URL behind it is resolved from this
        # config. Editing the config between the preview and prepare would
        # otherwise send the approved load to a different address.
        capabilities = self.capabilities()
        check_commitment(data, capabilities, approved)
        plan, target = validate_plan(data, capabilities)
        key = self.valid_key(key)
        prepared_id = "p-" + fingerprint({"key": key, "plan": plan.model_dump(),
                                          "approved": approved or {}})[:40]
        directory = self.root / prepared_id
        directory.mkdir(exist_ok=True)
        executable = shutil.which(self.config.get("k6_binary", "k6"))
        if not executable:
            raise ValueError("k6 executable not found; set k6_binary in runner config")
        target_config = self.config["targets"][plan.target]
        payload = {"plan": plan.model_dump(), "target": target,
                   "approved": approved or {},
                   "auth_env": target_config.get("auth_env", ""), "k6_binary": executable,
                   "lease_seconds": min(60, max(10, int(self.config.get("lease_seconds", 20))))}
        script = compile_script(plan, target)
        # Generated code only. An operator/model cannot substitute a file or subprocess argument.
        (directory / "scenario.json").write_text(canonical(plan.model_dump()), encoding="utf-8")
        (directory / "test.js").write_text(script, encoding="utf-8")
        (directory / "k6-config.json").write_text("{}", encoding="utf-8")
        check = subprocess.run([executable, "inspect", "--config", str(directory / "k6-config.json"), str(directory / "test.js")],
                               capture_output=True, timeout=15, env=process_env(), **hidden())
        if check.returncode:
            raise ValueError("k6 inspect failed")
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO prepared VALUES (?, ?)", (prepared_id, canonical(payload)))
            saved = db.execute("SELECT payload FROM prepared WHERE id=?", (prepared_id,)).fetchone()
            if saved["payload"] != canonical(payload):
                raise ValueError("runner configuration changed; prepare a new attempt")
        return {"prepared_id": prepared_id, "validated": True,
                "files": [f"{prepared_id}/scenario.json", f"{prepared_id}/test.js"]}

    @staticmethod
    def valid_key(key):
        if not isinstance(key, str) or not 1 <= len(key) <= 160:
            raise ValueError("invalid idempotency key")
        return key

    def start_test(self, prepared_id, key, *, smoke=False, approved=None):
        if not isinstance(smoke, bool):
            raise ValueError("smoke must be boolean")
        test_id = "k6-" + fingerprint({"key": self.valid_key(key)})[:40]
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM prepared WHERE id=?", (prepared_id,)).fetchone()
            if not row:
                raise ValueError("unknown prepared test")
            payload = json.loads(row["payload"])
            capabilities = self.capabilities()
            # Both the set approved by the operator and the set fixed at prepare
            # time: config changes after either point must stop the start, not
            # be resolved again into whatever the config now says.
            check_commitment(payload["plan"], capabilities, approved)
            if approved != payload.get("approved"):
                raise ValueError("approved run parameters differ from the prepared attempt")
            _, current_target = validate_plan(payload["plan"], capabilities)
            if current_target != payload["target"]:
                raise ValueError("target changed after preparation")
            old = db.execute("SELECT * FROM jobs WHERE id=?", (test_id,)).fetchone()
            if old:
                if old["prepared_id"] != prepared_id or old["smoke"] != int(smoke):
                    raise ValueError("idempotency key reused with different parameters")
                return self._status(old, payload)
            if db.execute("SELECT 1 FROM jobs WHERE status NOT IN ('completed','failed','stopped') LIMIT 1").fetchone():
                raise ValueError("runner already has an active or unresolved job")
            if not smoke and not db.execute(
                    "SELECT 1 FROM jobs WHERE prepared_id=? AND smoke=1 AND status='completed'",
                    (prepared_id,)).fetchone():
                raise ValueError("successful smoke test is required")
            db.execute("INSERT INTO jobs (id, prepared_id, smoke, status, heartbeat, updated) VALUES (?,?,?,?,?,?)",
                       (test_id, prepared_id, int(smoke), "starting", now, now))
        try:
            # This worker survives the HTTP server and holds the lease/deadline independently.
            subprocess.Popen([sys.executable, "-m", "agent.nt_run.worker", str(self.root), test_id],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, **hidden())
        except Exception:
            with self.connect() as db:
                db.execute("UPDATE jobs SET status='failed', result=? WHERE id=? AND status='starting'",
                           (canonical({"stop_reason": "worker_start_failed"}), test_id))
            raise
        return self.get_test_status(test_id)

    def _status(self, row, payload):
        result = json.loads(row["result"])
        # A dead worker is not a successfully terminated test. Never auto-restart its load.
        status = row["status"]
        if status in {"starting", "running"} and time.time() - row["updated"] > 45:
            status = "unknown"
        plan, target = payload["plan"], payload["target"]
        return {**{k: target[k] for k in ("target_service", "environment", "namespace")},
                **{k: plan[k] for k in ("target_rps", "duration_seconds", "ramp_up_seconds", "virtual_users",
                                       "sla_p95_ms", "sla_error_rate")},
                "scenario": row["prepared_id"], "workload_fingerprint": fingerprint(plan),
                "test_id": row["id"], "test_status": status, **result}

    def get_test_status(self, test_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (test_id,)).fetchone()
            if not row:
                raise ValueError("unknown test")
            payload = json.loads(db.execute("SELECT payload FROM prepared WHERE id=?", (row["prepared_id"],)).fetchone()[0])
        return self._status(row, payload)

    def get_test_results(self, test_id):
        return self.get_test_status(test_id)

    def heartbeat(self, test_id):
        with self.connect() as db:
            db.execute("UPDATE jobs SET heartbeat=? WHERE id=?", (time.time(), test_id))
        return self.get_test_status(test_id)

    def stop_test(self, test_id):
        with self.connect() as db:
            db.execute("UPDATE jobs SET stop=1 WHERE id=?", (test_id,))
        for _ in range(40):
            result = self.get_test_status(test_id)
            if result["test_status"] in TERMINAL:
                return result
            time.sleep(.2)
        raise ValueError("stop unconfirmed; if the worker is gone, release it with "
                         "serve_nt_runner.py --release " + test_id)

    def release_lost_job(self, test_id):
        """Operator-only recovery for a worker that vanished without reporting an end.

        Such a job blocks every later test, and it should: the runner cannot tell a dead
        worker from a live one that stopped writing, and guessing would either strand a
        running k6 or invite a second load generator onto the same stand. So the decision
        stays with a person who has checked the processes, and it is deliberately not
        reachable over HTTP — the graph must never be able to clear its own lost run.
        """
        if self.get_test_status(test_id)["test_status"] != "unknown":
            raise ValueError("job is still reporting; only an unresolved worker can be released")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT result, status FROM jobs WHERE id=?", (test_id,)).fetchone()
            if row["status"] in TERMINAL:
                raise ValueError("job already finished")
            result = {**json.loads(row["result"]), "stop_reason": "worker_lost",
                      "finished_at": time.time(), "released_by_operator": True}
            db.execute("UPDATE jobs SET status='failed', updated=?, result=? WHERE id=?",
                       (time.time(), canonical(result), test_id))
        return self.get_test_status(test_id)


def hidden():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def process_env():
    # Inherited K6_* settings could replace target, options, TLS checks or outputs.
    names = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "SSL_CERT_FILE"}
    return {key: value for key, value in os.environ.items() if key.upper() in names}
