"""Independent worker. The model and HTTP server are not needed to stop a k6 process."""

from __future__ import annotations

import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

from agent.nt_run.plan import Plan, canonical, compile_script
from agent.nt_run.runner import hidden, process_env


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)] if ordered else None


def run(root: Path, test_id: str):
    def connect():
        db = sqlite3.connect(root / "runner.sqlite3", timeout=10)
        db.row_factory = sqlite3.Row
        return db

    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM jobs WHERE id=?", (test_id,)).fetchone()
        if not row or row["status"] != "starting":
            return  # Duplicate worker cannot duplicate a load generator.
        payload = json.loads(db.execute("SELECT payload FROM prepared WHERE id=?", (row["prepared_id"],)).fetchone()[0])
        db.execute("UPDATE jobs SET status='running', updated=? WHERE id=?", (time.time(), test_id))
    directory = root / test_id
    directory.mkdir(exist_ok=True)
    (directory / "k6-config.json").write_text("{}", encoding="utf-8")
    (directory / "test.js").write_text(compile_script(Plan.model_validate(payload["plan"]), payload["target"]), encoding="utf-8")
    shutil.copyfile(root / row["prepared_id"] / "scenario.json", directory / "scenario.json")
    started = time.time()
    plan, smoke = payload["plan"], bool(row["smoke"])
    deadline = started + (35 if smoke else plan["duration_seconds"] + plan["ramp_up_seconds"] + 15)
    env = process_env()
    env["NT_SMOKE"] = "1" if smoke else "0"
    if payload["auth_env"]:
        token = os.getenv(payload["auth_env"])
        if not token:
            with connect() as db:
                db.execute("UPDATE jobs SET status='failed', result=?, updated=? WHERE id=?",
                           (canonical({"stop_reason": "target_auth_missing", "started_at": started,
                                       "finished_at": time.time()}), time.time(), test_id))
            return
        env["NT_TARGET_TOKEN"] = token
    samples, lock = deque(maxlen=100000), threading.Lock()
    last_point = [started]
    process = None
    reason, status, metrics = "", "failed", {}

    def read_points(pipe):
        # The pipe belongs to this thread alone. Closing it from the monitoring loop would
        # block on the lock this thread holds inside its own blocking read — exactly the
        # state `metrics_lost` exists to escape — and `reader.join` would guard nothing.
        with pipe:
            for line in pipe:
                if len(line) > 64000:
                    continue
                try:
                    point = json.loads(line)
                    if point.get("type") != "Point" or point.get("metric") not in {
                            "http_req_duration", "nt_errors", "http_reqs", "dropped_iterations"}:
                        continue
                    value = point["data"]["value"]
                    if not isinstance(value, (int, float)) or not math.isfinite(value):
                        continue
                    with lock:
                        last_point[0] = time.time()
                        samples.append((last_point[0], point["metric"], value))
                except (ValueError, KeyError, TypeError):
                    continue

    try:
        if row["stop"] or started - row["heartbeat"] > payload["lease_seconds"]:
            reason, status = "cancelled_before_start", "stopped"
            return
        # k6's JSON stdout is consumed continuously; neither raw response bodies nor unbounded
        # metric files are retained. The inspectable final summary is written by handleSummary.
        process = subprocess.Popen([payload["k6_binary"], "run", "--config", "k6-config.json",
                                    "--quiet", "--out", "json", "test.js"],
            cwd=directory, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", **hidden())
        reader = threading.Thread(target=read_points, args=(process.stdout,), daemon=True)
        reader.start()
        while process.poll() is None:
            now = time.time()
            with connect() as db:
                control = db.execute("SELECT stop, heartbeat FROM jobs WHERE id=?", (test_id,)).fetchone()
            with lock:
                recent = [s for s in samples if s[0] >= now - 5]
                fresh = last_point[0]
            durations = [v for _, m, v in recent if m == "http_req_duration"]
            errors = [v for _, m, v in recent if m == "nt_errors"]
            metrics = {"window_seconds": 5, "p95_ms": percentile(durations, .95),
                       "error_rate": sum(errors) / len(errors) if errors else None,
                       "rps": sum(v for _, m, v in recent if m == "http_reqs") / 5,
                       "dropped_iterations": sum(v for _, m, v in recent if m == "dropped_iterations")}
            if control["stop"]:
                reason = "operator_stop"
            elif now >= deadline:
                reason = "deadline"
            elif now - control["heartbeat"] > payload["lease_seconds"]:
                reason = "lease_expired"
            elif now - fresh > 15:
                reason = "metrics_lost"
            elif not smoke and now - started >= 5:
                if metrics["error_rate"] is not None and metrics["error_rate"] > plan["stop_error_rate"]:
                    reason = "error_threshold"
                elif metrics["p95_ms"] is not None and metrics["p95_ms"] > plan["stop_p95_ms"]:
                    reason = "latency_threshold"
            with connect() as db:
                db.execute("UPDATE jobs SET updated=?, result=? WHERE id=?",
                           (now, canonical({"started_at": started, "metrics": metrics}), test_id))
            if reason:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
                break
            time.sleep(.25)
        reader.join(timeout=2)
        status = "stopped" if reason else "completed" if process.returncode == 0 else "failed"
        if not reason and process.returncode:
            reason = "k6_exit_" + str(process.returncode)
    except Exception:
        reason = "worker_error"
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    finally:
        result = {"started_at": started, "finished_at": time.time(), "stop_reason": reason,
                  "metrics": metrics, "files": [f"{test_id}/test.js", f"{test_id}/scenario.json"]}
        summary_path = directory / "summary.json"
        if summary_path.is_file() and summary_path.stat().st_size <= 256000:
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))["metrics"]
                values = {k: summary[k].get("values", {}) for k in (
                    "http_req_duration", "http_reqs", "nt_errors", "dropped_iterations") if k in summary}
                errors = values.get("nt_errors")
                if errors:
                    # У Rate-метрики k6 «passes» — это ненулевые add(), а здесь ненулевой
                    # add() означает отказ. Сырые имена читаются наоборот: в отчёте
                    # успешный прогон выглядит как 903 падения при доле ошибок 0.001.
                    values["nt_errors"] = {"rate": errors.get("rate"),
                                           "failed_requests": errors.get("passes"),
                                           "ok_requests": errors.get("fails")}
                result["summary"] = values
                result["files"].append(f"{test_id}/summary.json")
            except (ValueError, KeyError, TypeError):
                result["summary_error"] = "invalid k6 summary"
        with connect() as db:
            db.execute("UPDATE jobs SET status=?, updated=?, result=? WHERE id=?",
                       (status, time.time(), canonical(result), test_id))


if __name__ == "__main__":
    run(Path(sys.argv[1]).resolve(), sys.argv[2])
