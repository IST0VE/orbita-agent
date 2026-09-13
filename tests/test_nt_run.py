"""Execution boundaries and complete graph paths without network, k6 or paid model calls."""

import asyncio
import json
import os
import sys
import threading
from copy import deepcopy
from types import SimpleNamespace

import pytest
import responses
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, interrupt

from agent.nt_run import worker
from agent.nt_run.client import RunnerHTTP
from agent.nt_run.plan import compile_script, validate_plan
from agent.nt_run.runner import Runner
from agent.nt_run_graph import build_graph

CAPS = {"idempotent_start": True, "watchdog": True, "lease": True,
        "targets": {"local": {"url": "http://127.0.0.1:8088", "target_service": "demo",
                               "environment": "nt", "namespace": "test"}},
        "limits": {"max_rps": 10, "max_vus": 5, "max_duration_seconds": 60}}
PLAN = {"objective": "Проверить GET /health", "target": "local", "target_rps": 2,
        "duration_seconds": 2, "virtual_users": 1, "sla_p95_ms": 500.0,
        "sla_error_rate": .01, "stop_p95_ms": 1000.0, "stop_error_rate": .1,
        "steps": [{"name": "health", "path": "/health"}]}


def write(plan=None, call_id="write"):
    return AIMessage(content="", tool_calls=[{"id": call_id, "name": "write_test_files",
                      "args": {"plan": plan or PLAN}}])


def decision(action, **kw):
    return AIMessage(content=json.dumps({"action": action, **kw}))


class FakeRunner:
    def __init__(self, *, smoke_fail=False, running=False, lost=False, stop_unknown=False):
        self.smoke_fail, self.running = smoke_fail, running
        self.lost, self.stop_unknown = lost, stop_unknown
        self.prepared, self.starts, self.jobs, self.stops = [], [], {}, []
        self.beats = []
        self.approved_seen = []

    def capabilities(self):
        return deepcopy(CAPS)

    def prepare_test(self, plan, key, approved=None):
        # Одобренный набор едет с каждой записью (R3): runner обязан получить
        # именно то, на что согласился оператор, и проверить это у себя.
        self.approved_seen.append(approved)
        self.prepared.append((plan, key))
        return {"prepared_id": key}

    def start_test(self, prepared_id, key, *, smoke=False, approved=None):
        self.approved_seen.append(approved)
        if key not in self.jobs:
            self.starts.append((key, smoke))
            self.jobs[key] = {"test_id": key, "test_status": "failed" if smoke and self.smoke_fail
                else "running" if self.running and not smoke else "completed", "started_at": 100.0,
                "finished_at": 102.0, "metrics": {"p95_ms": 20.0}}
        return self.jobs[key]

    def get_test_status(self, test_id):
        if self.lost and self.jobs[test_id]["test_status"] == "running":
            raise RuntimeError("network down")
        return self.jobs[test_id]

    def get_test_results(self, test_id):
        return self.jobs[test_id]

    def heartbeat(self, test_id):
        self.beats.append(test_id)
        self.jobs[test_id] = {**self.jobs[test_id], "test_status": "completed"}

    def stop_test(self, test_id):
        self.stops.append(test_id)
        if not self.stop_unknown:
            self.jobs[test_id] = {**self.jobs[test_id], "test_status": "stopped"}
        return self.jobs[test_id]


def analyzer(result):
    return {"analysis_result": "PASSED" if result["test_status"] == "completed" else "INCONCLUSIVE",
            "artifacts": {"report": "Historical analysis: " + result["test_status"]}}


def graph(runner, *answers, **kw):
    return build_graph(GenericFakeChatModel(messages=iter(answers)), runner=runner,
                       analyzer=analyzer, poll_seconds=0, **kw).compile(checkpointer=InMemorySaver())


CONFIG = {"configurable": {"thread_id": "run", "publish": False}, "recursion_limit": 200}
INPUT = {"messages": [HumanMessage("Проведи НТ на local: GET /health") ]}


def invoke(app, data=INPUT):
    return asyncio.run(app.ainvoke(data, CONFIG))


def test_preparation_approval_smoke_load_analysis_and_live_metrics():
    runner = FakeRunner(running=True)
    app = graph(runner, write(), decision("ready"), decision("finish", conclusion="Готово"))
    paused = invoke(app)
    assert paused["__interrupt__"][0].value["action"] == "nt_launch"
    assert "import http" in paused["artifacts"]["script"]
    assert not runner.starts and not runner.prepared
    result = invoke(app, Command(resume={"decision": "approved"}))
    assert [smoke for _, smoke in runner.starts] == [True, False]
    assert len(runner.beats) == 1
    assert [r["kind"] for r in result["runs"]] == ["smoke", "load"]
    assert result["run_analysis"]["analysis_result"] == "PASSED"
    assert "Historical analysis" in result["artifacts"]["analysis_1"]
    assert result["usage"]["calls"] == 3


def test_rejected_launch_has_no_runner_mutations():
    runner = FakeRunner()
    app = graph(runner, write(), decision("ready"))
    invoke(app)
    result = invoke(app, Command(resume={"decision": "rejected"}))
    assert not runner.prepared and not runner.starts
    assert "отклонён" in result["artifacts"]["report"]


def test_clarification_resumes_planning():
    runner = FakeRunner()
    app = graph(runner, decision("clarify", question="Какой SLA?"), write(), decision("ready"),
                decision("finish"), auto_approve=True)
    assert invoke(app)["__interrupt__"][0].value["action"] == "nt_clarify"
    result = invoke(app, Command(resume={"answer": "p95 500 ms"}))
    assert len(result["runs"]) == 2


def test_failed_smoke_blocks_load_and_is_returned_to_model():
    runner = FakeRunner(smoke_fail=True)
    result = invoke(graph(runner, write(), decision("ready"), decision("finish"), auto_approve=True))
    assert len(runner.starts) == 1 and runner.starts[0][1]
    assert "failed" in result["artifacts"]["report"]
    assert result["attempt"] == 1


@pytest.mark.parametrize("unknown", [False, True])
def test_monitoring_loss_stops_and_never_claims_unconfirmed_stop(unknown):
    runner = FakeRunner(running=True, lost=True, stop_unknown=unknown)
    result = invoke(graph(runner, write(), decision("ready"), decision("finish"), auto_approve=True))
    assert runner.stops
    if unknown:
        assert "не подтверждена" in result["artifacts"]["report"]
        assert result["active_status"]["test_status"] == "running"
    else:
        assert result["run_analysis"]["analysis_result"] == "INCONCLUSIVE"
        assert result["runs"][-1]["result"]["test_status"] == "stopped"


def test_retry_limit_blocks_extra_execution():
    runner = FakeRunner()
    app = graph(runner, write(), decision("ready"), write(call_id="again"), decision("ready"),
                auto_approve=True, max_runs=1)
    result = invoke(app)
    assert len(runner.starts) == 2
    assert "бюджет" in result["artifacts"]["report"]


def test_unrecognized_model_output_has_bounded_repair_loop():
    result = invoke(graph(FakeRunner(), AIMessage("nonsense"), AIMessage("nonsense"),
                          auto_approve=True, max_steps=2))
    assert result["planning_steps"] == 2
    assert "лимит" in result["artifacts"]["report"]


def test_analysis_failure_keeps_actual_run_results():
    def broken(_):
        raise RuntimeError("analyzer unavailable")

    runner = FakeRunner()
    model = GenericFakeChatModel(messages=iter([write(), decision("ready"), decision("finish")]))
    app = build_graph(model, runner=runner, analyzer=broken, auto_approve=True, poll_seconds=0).compile()
    result = invoke(app)
    assert len(result["runs"]) == 2
    assert result["run_analysis"]["analysis_result"] == "INCONCLUSIVE"
    assert "runner" in result["run_analysis"]["diagnostic_gaps"][0]


def test_nested_analysis_interrupt_is_not_swallowed_or_load_repeated():
    def pausing(result):
        interrupt({"action": "query", "document": "Check metric query"})
        return analyzer(result)

    runner = FakeRunner()
    model = GenericFakeChatModel(messages=iter([write(), decision("ready"), decision("finish")]))
    app = build_graph(model, runner=runner, analyzer=pausing, auto_approve=True, poll_seconds=0).compile(
        checkpointer=InMemorySaver())
    paused = invoke(app)
    assert paused["__interrupt__"][0].value["action"] == "query"
    result = invoke(app, Command(resume={"decision": "approved"}))
    assert len(runner.starts) == 2
    assert result["run_analysis"]["analysis_result"] == "PASSED"


def test_failed_revision_cannot_launch_previous_candidate():
    runner = FakeRunner()
    result = invoke(graph(runner, write(), write({**PLAN, "target": "unknown"}, "bad"),
                          decision("ready"), decision("finish"), auto_approve=True))
    assert not runner.starts and not result["candidate"]


def test_recovery_of_unconfirmed_smoke_never_starts_main_load():
    runner = FakeRunner()
    runner.jobs["old-smoke"] = {"test_id": "old-smoke", "test_status": "completed", "metrics": {}}
    result = invoke(graph(runner, auto_approve=True), {**INPUT, "active_test_id": "old-smoke",
        "active_status": {"test_status": "unknown"}, "active_kind": "smoke", "candidate": PLAN,
        "attempt": 0, "runs": []})
    assert not runner.starts
    assert result["runs"][0]["result"]["test_status"] == "stopped"


@pytest.mark.parametrize("changes", [
    {"target": "unconfigured"}, {"target_rps": 11}, {"duration_seconds": 61},
    {"virtual_users": 6}, {"steps": [{"name": "bad", "path": "//external.test/"}]},
    {"steps": [{"name": "bad", "path": "/{{unknown}}"}]},
    {"stop_error_rate": .001}, {"script": "execute whatever"}, {"target_rps": True},
    {"sla_p95_ms": float("nan")},
])
def test_plan_rejects_unsafe_or_inconsistent_inputs(changes):
    with pytest.raises((ValueError, TypeError)):
        validate_plan({**PLAN, **changes}, CAPS)


def test_compiler_keeps_data_as_data_and_supports_correlated_steps():
    plan, target = validate_plan({**PLAN, "dataset": [{"sku": "x'); throw new Error('oops"}],
        "steps": [{"name": "create", "method": "POST", "path": "/orders", "expected_status": 201,
                   "body": {"sku": "{{sku}}"}, "extract": {"order_id": "data.id"}},
                  {"name": "read", "path": "/orders/{{order_id}}"}]}, CAPS)
    script = compile_script(plan, target)
    assert "redirects: 0" in script and "abortOnFail: true" in script
    assert "encodeURIComponent" in script
    assert '"sku":"x\'); throw new Error(\'oops"' in script
    assert "const origin = \"http://127.0.0.1:8088\"" in script


@responses.activate
def test_control_transport_retries_with_same_idempotency_key_and_validates_identity():
    client = RunnerHTTP("http://runner.test", "secret")
    responses.post("http://runner.test/start", status=503)
    responses.post("http://runner.test/start", json={"success": True, "data": {"test_id": "k6-1"}})
    assert client.start_test("p-1", "same-key")["test_id"] == "k6-1"
    assert responses.calls[0].request.body == responses.calls[1].request.body
    responses.get("http://runner.test/tests/k6-1", json={"success": True, "data": {"test_id": "different"}})
    with pytest.raises(RuntimeError, match="MISMATCH"):
        client.get_test_status("k6-1")


def local_runner(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.nt_run.runner.shutil.which", lambda _: "k6")
    monkeypatch.setattr("agent.nt_run.runner.subprocess.run", lambda *a, **k: SimpleNamespace(returncode=0))
    spawned = []
    monkeypatch.setattr("agent.nt_run.runner.subprocess.Popen", lambda *a, **k: spawned.append(a))
    return Runner(tmp_path, {"targets": CAPS["targets"]}), spawned


def test_runner_idempotency_survives_restart_and_rejects_conflicts(tmp_path, monkeypatch):
    runner, spawned = local_runner(tmp_path, monkeypatch)
    prepared = runner.prepare_test(PLAN, "prepare")["prepared_id"]
    first = runner.start_test(prepared, "unique", smoke=True)
    restarted = Runner(tmp_path, runner.config)
    assert restarted.start_test(prepared, "unique", smoke=True)["test_id"] == first["test_id"]
    assert len(spawned) == 1
    with pytest.raises(ValueError, match="different"):
        restarted.start_test(prepared, "unique", smoke=False)
    with pytest.raises(ValueError, match="active"):
        restarted.start_test(prepared, "second", smoke=True)


def test_runner_requires_successful_smoke_even_if_graph_is_bypassed(tmp_path, monkeypatch):
    runner, spawned = local_runner(tmp_path, monkeypatch)
    prepared = runner.prepare_test(PLAN, "prepare")["prepared_id"]
    with pytest.raises(ValueError, match="smoke"):
        runner.start_test(prepared, "load", smoke=False)
    assert not spawned


def test_old_worker_state_is_unknown_not_completed(tmp_path, monkeypatch):
    runner, _ = local_runner(tmp_path, monkeypatch)
    prepared = runner.prepare_test(PLAN, "prepare")["prepared_id"]
    test_id = runner.start_test(prepared, "smoke", smoke=True)["test_id"]
    with runner.connect() as db:
        db.execute("UPDATE jobs SET updated=0 WHERE id=?", (test_id,))
    assert runner.get_test_status(test_id)["test_status"] == "unknown"


def test_worker_never_blocks_on_a_pipe_its_reader_still_holds(tmp_path, monkeypatch):
    """A k6 that went quiet is what `metrics_lost` is for; its pipe must not deadlock the worker.

    The reader sits in a blocking read holding the buffer lock, so closing the same pipe from
    the monitoring loop waits for the writer to disappear — and `reader.join(timeout=...)` right
    before it would guard nothing.
    """
    runner, _ = local_runner(tmp_path, monkeypatch)
    prepared = runner.prepare_test(PLAN, "prepare")["prepared_id"]
    test_id = runner.start_test(prepared, "smoke", smoke=True)["test_id"]
    read_fd, write_fd = os.pipe()

    class Silent:
        """Exits at once, but leaves a pipe nobody writes to and nobody else closes."""

        returncode = 0
        stdout = os.fdopen(read_fd, "r", encoding="utf-8")

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **k: Silent())
    finished = threading.Thread(target=worker.run, args=(runner.root, test_id), daemon=True)
    finished.start()
    finished.join(timeout=30)
    try:
        assert not finished.is_alive(), "worker blocked closing a pipe its reader still holds"
        assert runner.get_test_status(test_id)["test_status"] == "completed"
    finally:
        os.close(write_fd)


def test_error_counters_are_named_by_meaning_not_by_k6_rate_semantics(tmp_path, monkeypatch):
    """У Rate-метрики k6 «passes» — ненулевые add(), а здесь это отказы.

    В сыром виде успешный прогон выглядит в отчёте как 903 падения при доле ошибок
    0.001, и читатель — человек или модель — принимает это за дефект измерения.
    """
    runner, _ = local_runner(tmp_path, monkeypatch)
    prepared = runner.prepare_test(PLAN, "prepare")["prepared_id"]
    test_id = runner.start_test(prepared, "smoke", smoke=True)["test_id"]
    directory = runner.root / test_id
    directory.mkdir(exist_ok=True)
    (directory / "summary.json").write_text(json.dumps({"metrics": {
        "http_reqs": {"values": {"count": 904}},
        "nt_errors": {"values": {"rate": 0.0011, "passes": 1, "fails": 903}}}}), encoding="utf-8")
    read_fd, write_fd = os.pipe()

    class Finished:
        returncode = 0
        stdout = os.fdopen(read_fd, "r", encoding="utf-8")

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **k: Finished())
    os.close(write_fd)
    worker.run(runner.root, test_id)
    errors = runner.get_test_status(test_id)["summary"]["nt_errors"]
    assert errors == {"rate": 0.0011, "failed_requests": 1, "ok_requests": 903}


def test_vanished_worker_is_released_only_by_an_operator_and_then_unblocks_the_runner(
        tmp_path, monkeypatch):
    runner, _ = local_runner(tmp_path, monkeypatch)
    prepared = runner.prepare_test(PLAN, "prepare")["prepared_id"]
    test_id = runner.start_test(prepared, "smoke", smoke=True)["test_id"]
    with pytest.raises(ValueError, match="still reporting"):
        runner.release_lost_job(test_id)
    with runner.connect() as db:
        db.execute("UPDATE jobs SET updated=0 WHERE id=?", (test_id,))
    with pytest.raises(ValueError, match="active"):
        runner.start_test(prepared, "blocked", smoke=True)
    released = runner.release_lost_job(test_id)
    assert released["test_status"] == "failed" and released["stop_reason"] == "worker_lost"
    assert runner.start_test(prepared, "after-release", smoke=True)["test_id"] != test_id


def test_script_escapes_javascript_line_terminators_smuggled_through_plan_text():
    separators = "\u2028\u2029"
    plan, target = validate_plan({**PLAN, "objective": "Цель" + separators + "продолжение"}, CAPS)
    script = compile_script(plan, target)
    assert not set(separators) & set(script)
    assert "\\u2028" in script and "\\u2029" in script


def test_launch_approval_shows_the_script_compiled_from_the_plan_being_approved():
    paused = invoke(graph(FakeRunner(), write(), decision("ready")))
    assert compile_script(*validate_plan(PLAN, CAPS)) in paused["__interrupt__"][0].value["document"]


def test_more_than_three_tool_calls_still_invalidate_the_previous_plan():
    runner = FakeRunner()
    flood = AIMessage(content="", tool_calls=[
        {"id": f"call-{i}", "name": "write_test_files", "args": {"plan": PLAN}} for i in range(4)])
    result = invoke(graph(runner, write(), flood, decision("ready"), decision("finish"),
                          auto_approve=True))
    assert not runner.starts and not result["candidate"]


# --------------------------------------------------------------------------
# R3: одобрена фактическая цель, а не логический ключ
#
# В подтверждённом плане стоит `target: "local"` — логическое имя. Адрес за ним
# runner разрешает заново, и правка его конфигурации между предпросмотром и
# prepare уводила одобренную нагрузку на другой стенд.
# --------------------------------------------------------------------------
def test_the_approved_set_names_the_resolved_target_and_limits():
    runner = FakeRunner()
    app = graph(runner, write(), decision("ready"), decision("finish"))
    payload = invoke(app)["__interrupt__"][0].value

    assert payload["action"] == "nt_launch"
    # Адрес стенда виден оператору до запуска, а не только ключ цели.
    assert "http://127.0.0.1:8088" in payload["document"]
    assert "max_rps" in payload["document"]
    # Секретов в наборе нет: capabilities отдаёт только адрес и координаты
    # стенда. Имя переменной окружения остаётся в шаблоне скрипта — это
    # подстановка, которую делает runner, а не значение.
    approved = json.loads(payload["document"].split("```json", 1)[1].split("```", 1)[0])
    assert "auth_env" not in json.dumps(approved)
    assert "NT_TARGET_TOKEN" not in json.dumps(approved)
    assert sorted(approved) == ["limits", "plan", "runner", "target"]


def test_the_approved_set_travels_to_the_runner():
    runner = FakeRunner()
    app = graph(runner, write(), decision("ready"), decision("finish"))
    invoke(app)
    invoke(app, Command(resume={"decision": "approved"}))

    assert runner.approved_seen, "runner обязан получить одобренный набор"
    approved = runner.approved_seen[0]
    assert approved["target"]["url"] == "http://127.0.0.1:8088"
    assert approved["limits"] == CAPS["limits"]
    assert approved["plan"]["target_rps"] == PLAN["target_rps"]


def test_a_target_url_changed_after_the_preview_blocks_the_start():
    """Тот же ключ цели, другой адрес: запуск не должен состояться."""
    runner = FakeRunner()
    app = graph(runner, write(), decision("ready"), decision("finish"))
    invoke(app)

    moved = deepcopy(CAPS)
    moved["targets"]["local"]["url"] = "http://10.0.0.9:8088"
    runner.capabilities = lambda: deepcopy(moved)

    result = invoke(app, Command(resume={"decision": "approved"}))

    assert runner.starts == []
    assert "подтвердите запуск заново" in result["last_error"]


def test_relaxed_runner_limits_after_the_preview_block_the_start():
    runner = FakeRunner()
    app = graph(runner, write(), decision("ready"), decision("finish"))
    invoke(app)

    raised = deepcopy(CAPS)
    raised["limits"]["max_rps"] = 10000
    runner.capabilities = lambda: deepcopy(raised)

    result = invoke(app, Command(resume={"decision": "approved"}))

    assert runner.starts == []
    assert "подтвердите запуск заново" in result["last_error"]


def test_an_unchanged_scenario_still_runs_smoke_and_load():
    runner = FakeRunner()
    app = graph(runner, write(), decision("ready"), decision("finish"))
    invoke(app)
    result = invoke(app, Command(resume={"decision": "approved"}))

    assert [smoke for _, smoke in runner.starts] == [True, False]
    assert result["runs"]


def test_the_runner_refuses_a_start_whose_target_moved(tmp_path):
    """Проверка живёт и в runner: обращение мимо графа получает тот же отказ."""
    from agent.nt_run.plan import commitment_of

    config = {"k6_binary": sys.executable, "max_rps": 10, "max_vus": 5,
              "max_duration_seconds": 60,
              "targets": {"local": {"url": "http://127.0.0.1:8088", "target_service": "demo",
                                     "environment": "nt", "namespace": "test"}}}
    runner = Runner(tmp_path, config)
    approved = commitment_of(PLAN, runner.capabilities())

    config["targets"]["local"]["url"] = "http://10.0.0.9:8088"

    with pytest.raises(ValueError, match="no longer match"):
        runner.prepare_test(PLAN, "key-1", approved)
