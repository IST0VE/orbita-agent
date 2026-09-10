"""Contracts, security boundaries and HTTP behavior of the declarative UI engine."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from starlette.testclient import TestClient

from agent import api, audit_graph, drawio_graph, jira_graph, nt_graph, prep_graph, update_graph
from agent import graph as agent_graph
from agent.pipeline import Stage
from agent.ui_engine.capabilities import capabilities
from agent.ui_engine.events import EventNormalizer
from agent.ui_engine.manifests import (
    _ID,
    _VERSION,
    MAX_MANIFEST_BYTES,
    SUPPORTED_SCHEMA_VERSIONS,
    ManifestError,
    content_etag,
    fallback_manifest,
    topology_hash,
    validate_manifest,
)
from agent.ui_engine.redaction import MASK, redact
from agent.ui_engine.registry import ManifestNotFound, UiRegistry, registry


def test_all_builtin_graphs_have_valid_versioned_manifests():
    assert registry.graph_ids() == ("agent", "audit", "drawio", "jira", "nt", "prep", "update")
    for graph_id in registry.graph_ids():
        item = registry.resolve(graph_id)
        assert item.value["schema_version"] == "1.0"
        assert item.value["graph_id"] == graph_id
        assert item.etag == content_etag(item.value)
        assert item.value["nodes"]
        assert item.value["input"]
        assert isinstance(item.value["interrupts"], list)


def test_every_registered_graph_has_a_manifest():
    """
    Граф подключается тремя вещами: модулем, строкой в `langgraph.json` и
    manifest'ом. Раньше это показывал отдельный демонстрационный граф; он был
    пятым графом в каталоге и существовал только ради этой демонстрации.
    Проверка сильнее демонстрации: она смотрит на настоящие графы и падает,
    когда очередной забыли зарегистрировать — а забывается ровно этот шаг.
    """
    path = Path(__file__).parent.parent / "langgraph.json"
    registered = json.loads(path.read_text(encoding="utf-8"))

    assert tuple(sorted(registered["graphs"])) == registry.graph_ids()


def test_manifest_nodes_match_the_compiled_graph():
    """
    Манифест — подпись под топологией, а не список того, что бывает у графов
    вообще. Разъезжались они молча и в обе стороны: узел без описания рисуется
    сырым именем, а описание без узла не рисуется никак — и живёт в манифесте
    сколько угодно долго, потому что на экране его никто не ждёт.

    Так и было. `agent` объявлял `no_input`, которого у него нет: проверки
    входа этому конвейеру не передают, материалом ему может быть само сообщение
    оператора. `drawio` объявлял сверх того `context` и `tools` — оба остались
    от общего фрагмента манифеста, а не от графа. Проверка смотрит на
    скомпилированный граф, поэтому поймает и обратное: новый узел, которому
    забыли написать заголовок.
    """
    compiled = {
        "agent": agent_graph.graph,
        "audit": audit_graph.graph,
        "drawio": drawio_graph.graph,
        "jira": jira_graph.graph,
        "nt": nt_graph.graph,
        "prep": prep_graph.graph,
        "update": update_graph.graph,
    }
    assert tuple(sorted(compiled)) == registry.graph_ids()

    for graph_id, app in compiled.items():
        real = {
            name
            for name in app.get_graph().nodes
            if name not in {"__start__", "__end__"}
        }
        assert set(registry.resolve(graph_id).value["nodes"]) == real, graph_id


def test_a_pipeline_without_readers_has_no_tools_node():
    """
    Узел инструментов заводится только тогда, когда есть кому в него ходить.
    У декомпозиции и у разбора схем таких ролей нет — документ и схему читает
    код, — и узел висел в топологии без единого ребра: коробка «Инструменты»
    в Studio и в собственном интерфейсе, которая не может выполниться никогда.
    """
    for app in (jira_graph.graph, drawio_graph.graph, audit_graph.graph):
        assert "tools" not in app.get_graph().nodes

    for app in (agent_graph.graph, prep_graph.graph):
        assert "tools" in app.get_graph().nodes


def test_declared_topology_and_the_functions_given_to_the_builder_must_agree():
    """
    Описание конвейера обещает узлы, а функции для них передаёт модуль графа,
    и разъехаться они могли молча в обе стороны. Объявленная проверка входа
    без функции — обещанная интерфейсом ветка, которой в графе нет; функция
    без объявления — узел без подписи, нарисованный сырым именем. Оба случая
    доживают до экрана, потому что на экране их никто не ждёт, поэтому
    сборщик отказывается собирать такой граф.
    """
    from dataclasses import replace

    from agent import roles
    from agent.builder import build_graph

    # Объявлено, но не передано.
    with pytest.raises(ValueError, match="prelude"):
        build_graph(
            pipeline=replace(
                roles.PIPELINE,
                prelude=Stage(key="source", title="Источник", summary="Читает материал."),
            )
        )

    # Передано, но не объявлено.
    with pytest.raises(ValueError, match="admission"):
        build_graph(pipeline=roles.PIPELINE, admission=lambda state, config: "")


def test_published_json_schemas_are_valid_json_documents():
    root = Path(api.__file__).parent / "ui_engine" / "schemas"
    manifest_schema = json.loads((root / "ui-manifest-v1.schema.json").read_text(encoding="utf-8"))
    event_schema = json.loads((root / "runtime-event-v1.schema.json").read_text(encoding="utf-8"))

    assert manifest_schema["properties"]["schema_version"]["const"] == "1.0"
    assert event_schema["properties"]["schemaVersion"]["const"] == "1.0"


def test_manifest_rejects_unknown_major_unsafe_keys_and_oversize_payload():
    original = registry.resolve("agent").value

    wrong_version = deepcopy(original)
    wrong_version["schema_version"] = "2.0"
    with pytest.raises(ManifestError, match="major"):
        validate_manifest(wrong_version)

    polluted = deepcopy(original)
    polluted["theme"] = {"__proto__": {"admin": True}}
    with pytest.raises(ManifestError, match="unsafe"):
        validate_manifest(polluted)

    oversized = deepcopy(original)
    oversized["description"] = "x" * (MAX_MANIFEST_BYTES + 1)
    with pytest.raises(ManifestError, match="exceeds"):
        validate_manifest(oversized)


def test_unknown_optional_manifest_field_is_a_warning_not_a_failure():
    value = registry.resolve("agent").value
    value["future_minor_field"] = {"enabled": True}

    validated = validate_manifest(value)

    assert validated.value["future_minor_field"] == {"enabled": True}
    assert validated.warnings == ("unknown top-level field ignored: future_minor_field",)


def test_registry_rejects_duplicates_and_unknown_graphs():
    local = UiRegistry()
    local.register_manifest("agent", registry.resolve("agent").value)
    with pytest.raises(ManifestError, match="already registered"):
        local.register_manifest("agent", registry.resolve("agent").value)
    with pytest.raises(ManifestNotFound):
        local.resolve("missing")


def test_topology_hash_is_deterministic_for_ordering_but_sensitive_to_edges():
    first = {
        "nodes": [{"id": "b"}, {"id": "a"}],
        "edges": [{"source": "a", "target": "b", "conditional": False}],
    }
    reordered = {
        "nodes": list(reversed(first["nodes"])),
        "edges": list(reversed(first["edges"])),
    }
    changed = {"nodes": first["nodes"], "edges": [{"source": "b", "target": "a"}]}

    assert topology_hash(first) == topology_hash(reordered)
    assert topology_hash(first) != topology_hash(changed)


def test_redaction_handles_wildcards_roles_and_never_mutates_source():
    source = {
        "api_key": "secret",
        "messages": [
            {"response_metadata": {"raw_prompt": "private", "safe": "ok"}},
        ],
        "document": "abcdefghij",
        "admin": {"value": 42},
    }
    result = redact(
        source,
        [
            {"path": "document", "mode": "truncate", "max_length": 4},
            {"path": "admin", "mode": "role", "roles": ["admin"]},
        ],
        roles=["viewer"],
    )

    assert result["api_key"] == MASK
    assert "raw_prompt" not in result["messages"][0]["response_metadata"]
    assert result["document"] == "abcd…"
    assert "admin" not in result
    assert source["api_key"] == "secret"


def test_event_normalizer_sequences_deduplicates_and_redacts():
    normalizer = EventNormalizer()
    common = {
        "graph_id": "agent",
        "assistant_id": "assistant",
        "thread_id": "thread",
        "run_id": "run",
    }
    first = normalizer.emit("run.started", {"api_key": "secret"}, event_id="one", **common)
    duplicate = normalizer.emit("run.started", {"api_key": "secret"}, event_id="one", **common)
    second = normalizer.emit("node.completed", {"nodeId": "requirements"}, **common)

    assert first == duplicate
    assert first["sequence"] == 1
    assert first["data"]["api_key"] == MASK
    assert second["sequence"] == 2
    assert [event["sequence"] for event in normalizer.replay("run", after=1)] == [2]


def test_capabilities_do_not_overpromise_lifecycle_support():
    value = capabilities()
    assert value["engine"] == "orbita-ui"
    assert value["features"]["event_replay"] is False
    assert value["features"]["node_lifecycle"] is True
    assert value["limits"]["manifest_bytes"] == MAX_MANIFEST_BYTES


def test_manifest_http_etag_bundle_and_unknown_graph(monkeypatch):
    monkeypatch.setenv("API_ADMIN_TOKEN", "test-only-auth-token-with-32-characters")
    client = TestClient(api.app, headers={"Authorization": "Bearer test-only-auth-token-with-32-characters"})

    response = client.get("/api/ui/graphs/agent/manifest")
    assert response.status_code == 200
    assert response.json()["graph_id"] == "agent"
    assert response.headers["x-ui-schema-version"] == "1.0"

    cached = client.get(
        "/api/ui/graphs/agent/manifest",
        headers={"if-none-match": response.headers["etag"]},
    )
    assert cached.status_code == 304
    assert client.get("/api/ui/graphs/missing/manifest").status_code == 404

    bundle = client.get("/api/ui/assistants/assistant-uuid/bundle?graph_id=agent")
    assert bundle.status_code == 200
    assert bundle.json()["topology"]["url"] == "/assistants/assistant-uuid/graph"
    assert bundle.json()["manifest"]["graph_id"] == "agent"


def test_ui_endpoints_share_admin_authentication(monkeypatch):
    monkeypatch.setenv("API_ADMIN_TOKEN", "test-only-ui-secret-with-32-characters")
    client = TestClient(api.app)

    assert client.get("/api/ui/capabilities").status_code == 401
    assert client.get("/api/ui/graphs/agent/manifest").status_code == 401
    allowed = client.get(
        "/api/ui/capabilities",
        headers={"authorization": "Bearer test-only-ui-secret-with-32-characters"},
    )
    assert allowed.status_code == 200


def test_resource_registry_blocks_arbitrary_urls_and_unknown_operations(monkeypatch):
    monkeypatch.setenv("API_ADMIN_TOKEN", "test-only-auth-token-with-32-characters")
    monkeypatch.setattr(
        api.inputs,
        "create_task",
        lambda name: {"name": name, "title": name, "files": []},
    )
    client = TestClient(api.app, headers={"Authorization": "Bearer test-only-auth-token-with-32-characters"})

    assert client.get("/api/ui/resources/https:%2F%2Fevil.example?operation=list").status_code == 404
    assert client.get("/api/ui/resources/orbita.tasks?operation=delete").status_code == 404
    assert client.get("/api/ui/resources/orbita.tasks?operation=list").status_code == 200
    created = client.post(
        "/api/ui/resources/orbita.tasks?operation=create",
        json={"name": "engine-demo", "idempotency_key": f"resource-{uuid4()}"},
    )
    assert created.status_code == 200
    assert created.json()["created"]["name"] == "engine-demo"


def test_resume_action_is_schema_validated_and_idempotent(monkeypatch):
    monkeypatch.setenv("API_ADMIN_TOKEN", "test-only-auth-token-with-32-characters")
    client = TestClient(api.app, headers={"Authorization": "Bearer test-only-auth-token-with-32-characters"})
    key = f"test-{uuid4()}"
    # Ровно то, что шлёт браузер: рантайм-id остановки и отдельно id правила
    # манифеста. Совпадать они не обязаны и почти никогда не совпадают.
    body = {
        "graph_id": "agent",
        "kind": "interrupt.resume",
        "interrupt_id": f"interrupt-live-{uuid4()}",
        "interrupt_rule_id": "stage-approval",
        "thread_id": f"thread-{uuid4()}",
        "payload": {"decision": "approved", "reason": "ok"},
        "idempotency_key": key,
    }

    first = client.post("/api/ui/actions/validate", json=body)
    duplicate = client.post("/api/ui/actions/validate", json=body)
    same_interrupt_new_key = client.post(
        "/api/ui/actions/validate",
        json={**body, "idempotency_key": f"same-interrupt-{uuid4()}"},
    )
    invalid = client.post(
        "/api/ui/actions/validate",
        json={**body, "idempotency_key": f"bad-{uuid4()}", "payload": {"decision": "later"}},
    )

    assert first.status_code == 200 and first.json()["duplicate"] is False
    assert duplicate.status_code == 200 and duplicate.json()["duplicate"] is True
    assert same_interrupt_new_key.status_code == 200
    assert same_interrupt_new_key.json()["duplicate"] is True
    assert invalid.status_code == 422
    assert invalid.json()["error_code"] == "ui_resume_invalid"


def test_resume_rule_is_resolved_by_manifest_id_not_by_runtime_id(monkeypatch):
    """
    Правило ответа ищется по id из манифеста.

    Рантайм-id остановки у каждой из них свой (`interrupt-live-…`) и ни с
    одним правилом не совпадёт. Пока сервер искал по нему, подтверждение
    этапа не проходило ворота вовсе.
    """
    monkeypatch.setenv("API_ADMIN_TOKEN", "test-only-auth-token-with-32-characters")
    client = TestClient(api.app, headers={"Authorization": "Bearer test-only-auth-token-with-32-characters"})
    base = {
        "graph_id": "agent",
        "kind": "interrupt.resume",
        "interrupt_id": f"interrupt-live-{uuid4()}",
        "thread_id": f"thread-{uuid4()}",
        "payload": {"decision": "approved"},
    }

    known = client.post(
        "/api/ui/actions/validate",
        json={**base, "interrupt_rule_id": "publish-approval", "idempotency_key": f"k-{uuid4()}"},
    )
    unknown = client.post(
        "/api/ui/actions/validate",
        json={**base, "interrupt_rule_id": "no-such-rule", "idempotency_key": f"k-{uuid4()}"},
    )
    generic = client.post(
        "/api/ui/actions/validate",
        json={
            **base,
            "interrupt_id": f"interrupt-live-{uuid4()}",
            "thread_id": f"thread-{uuid4()}",
            "idempotency_key": f"k-{uuid4()}",
        },
    )

    assert known.status_code == 200
    assert unknown.status_code == 400
    assert unknown.json()["error_code"] == "ui_interrupt_unknown"
    # Правило не подошло и на клиенте: показана универсальная форма, и сервер
    # проверяет ровно то же — что ответ является объектом.
    assert generic.status_code == 200


def test_resume_without_thread_or_interrupt_context_is_rejected(monkeypatch):
    monkeypatch.setenv("API_ADMIN_TOKEN", "test-only-auth-token-with-32-characters")
    client = TestClient(api.app, headers={"Authorization": "Bearer test-only-auth-token-with-32-characters"})
    body = {
        "graph_id": "agent",
        "kind": "interrupt.resume",
        "payload": {"decision": "approved"},
        "idempotency_key": f"k-{uuid4()}",
    }

    response = client.post("/api/ui/actions/validate", json=body)

    assert response.status_code == 400
    assert response.json()["error_code"] == "ui_interrupt_context_missing"


def test_unregistered_graph_keeps_a_working_fallback_gate(monkeypatch):
    """
    Fallback mode обещает чат, остановку и ответ на прерывание.

    Отвечать на них «манифест не найден» значило бы обещание не выполнить:
    незнакомый граф открылся бы только на чтение.
    """
    monkeypatch.setenv("API_ADMIN_TOKEN", "test-only-auth-token-with-32-characters")
    client = TestClient(api.app, headers={"Authorization": "Bearer test-only-auth-token-with-32-characters"})

    allowed = client.post(
        "/api/ui/actions/validate",
        json={
            "graph_id": "unregistered",
            "kind": "run.start",
            "payload": {"value": "вопрос"},
            "idempotency_key": f"k-{uuid4()}",
        },
    )
    forbidden = client.post(
        "/api/ui/actions/validate",
        json={
            "graph_id": "unregistered",
            "kind": "thread.fork",
            "idempotency_key": f"k-{uuid4()}",
        },
    )

    assert allowed.status_code == 200 and allowed.json()["duplicate"] is False
    assert forbidden.status_code == 403
    assert forbidden.json()["error_code"] == "ui_action_forbidden"
    assert [item["kind"] for item in fallback_manifest("x")["actions"]] == [
        "thread.create",
        "run.start",
        "run.stop",
        "interrupt.resume",
    ]


def test_missing_idempotency_key_is_a_bad_request_not_a_conflict(monkeypatch):
    monkeypatch.setenv("API_ADMIN_TOKEN", "test-only-auth-token-with-32-characters")
    client = TestClient(api.app, headers={"Authorization": "Bearer test-only-auth-token-with-32-characters"})

    response = client.post(
        "/api/ui/actions/validate",
        json={"graph_id": "agent", "kind": "run.start", "payload": {"value": "x"}},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "ui_idempotency_missing"


def test_event_normalizer_forgets_old_runs_instead_of_growing_forever():
    """
    Память ограничена и по прогонам, а не только по событиям внутри прогона.

    Набор увиденных идентификаторов рос бы вечно: события дек давно вытеснил,
    а ключи от них оставались бы в процессе до перезапуска.
    """
    normalizer = EventNormalizer(retention=2, runs=2)
    common = {"graph_id": "prep", "assistant_id": "assistant", "thread_id": "thread"}
    for index in range(3):
        normalizer.emit("run.started", {}, run_id=f"run-{index}", **common)

    for index in range(4):
        normalizer.emit("node.update", {"step": index}, run_id="run-2", **common)

    assert normalizer.replay("run-0") == []
    assert len(normalizer._logs) == 2
    window = normalizer.replay("run-2")
    assert [event["sequence"] for event in window] == [4, 5]
    assert len(normalizer._logs["run-2"].ids) == 2


def test_json_schema_artifacts_match_the_python_and_typescript_validators():
    """
    Схемы объявлены каноническим описанием контракта — значит, они обязаны
    совпадать с обоими валидаторами, которые его на самом деле применяют.
    """
    root = Path(api.__file__).parent / "ui_engine" / "schemas"
    schema = json.loads((root / "ui-manifest-v1.schema.json").read_text(encoding="utf-8"))
    typescript = (
        Path(api.__file__).parents[2] / "web" / "src" / "engine" / "manifest" / "validate.ts"
    ).read_text(encoding="utf-8")

    assert schema["properties"]["schema_version"]["const"] == SUPPORTED_SCHEMA_VERSIONS[0]
    assert schema["properties"]["manifest_version"]["pattern"] == _VERSION.pattern
    assert schema["$defs"]["id"]["pattern"] == _ID.pattern
    assert set(schema["required"]) == {"schema_version", "manifest_version", "graph_id", "title"}
    # Тот же идентификатор и та же major-версия на другом конце провода.
    assert _ID.pattern in typescript
    assert f'SUPPORTED_MANIFEST_MAJOR = "{SUPPORTED_SCHEMA_VERSIONS[0].split(".")[0]}"' in typescript


def test_pipeline_manifests_declare_opening_a_document():
    """
    Открытие документа — объявленное действие, а не выдумка виджета.

    Диспетчер пропускает только те действия, что перечислены в манифесте;
    без этой строки клик по готовому документу упирался бы в «действие
    недоступно».
    """
    for graph_id in ("agent", "drawio", "jira"):
        kinds = {item["kind"] for item in registry.resolve(graph_id).value["actions"]}
        assert "publication.open" in kinds, graph_id
