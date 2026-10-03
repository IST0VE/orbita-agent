"""
Реестр графов сверяется с тем, что он описывает.

Описание, которое никто не сверяет, расходится с кодом молча: новый граф
забывают внести, у старого появляется остановка, которой в списке нет, узел
переименовывают. Поэтому каждое утверждение реестра проверяется по
независимому источнику: имена и пути — по `langgraph.json`, остановки — по
формам манифестов интерфейса (без формы остановку нечем показать), узлы —
по скомпилированной топологии, роли — по описанию конвейера.
"""

from __future__ import annotations

import fnmatch
import importlib
import json
from pathlib import Path

import pytest

from agent import graph_registry, proposals, roles
from agent.ui_engine.registry import registry as ui

ROOT = Path(__file__).parent.parent


def served() -> dict:
    return json.loads((ROOT / "langgraph.json").read_text(encoding="utf-8"))["graphs"]


def nodes_of(spec: graph_registry.GraphSpec) -> set[str]:
    return {name for name in spec.graph.get_graph().nodes if not name.startswith("__")}


def test_every_served_graph_is_registered_under_its_path():
    assert {spec.name: spec.reference for spec in graph_registry.GRAPHS} == served()


def test_stops_match_the_forms_the_interface_can_show():
    """Остановка без формы в манифесте — вопрос, который оператор не увидит."""
    for spec in graph_registry.GRAPHS:
        forms = {
            (item.get("match") or {}).get("equals")
            for item in ui.resolve(spec.name).value.get("interrupts") or []
        }
        assert {stop.action for stop in spec.stops} == forms, spec.name


def test_every_named_node_exists_in_the_compiled_graph():
    for spec in graph_registry.GRAPHS:
        real = nodes_of(spec)
        for item in (*spec.stops, *spec.effects):
            assert item.nodes, (spec.name, item)
            for node in item.nodes:
                assert fnmatch.filter(real, node), (spec.name, node)


@pytest.mark.parametrize("name", ["agent", "prep", "drawio", "audit", "jira", "pm"])
def test_pipeline_stops_follow_the_roles_of_the_pipeline(name):
    """Пауза — в каждой роли, ворота — перед каждой ролью после первой."""
    spec = graph_registry.get(name)
    module = importlib.import_module(spec.module)
    pipeline = getattr(module, "PIPELINE", None) or roles.PIPELINE
    keys = tuple(role.key for role in pipeline.roles)
    stops = {stop.action: stop for stop in spec.stops}

    assert stops["pause"].nodes == keys
    assert stops["stage"].nodes == tuple(f"gate_{key}" for key in keys[1:])


def test_nested_behaviour_is_declared_for_every_stop_and_effect():
    for spec in graph_registry.GRAPHS:
        for item in (*spec.stops, *spec.effects):
            assert item.nested in graph_registry.NESTED, (spec.name, item)
        # Пауза — единственное, что остаётся во вложенном прогоне: её просит
        # человек. Всё остальное граф обязан отдать или не делать.
        kept = {stop.action for stop in spec.stops if stop.nested == graph_registry.KEPT}
        assert kept <= {"pause"}, spec.name
        assert not [e for e in spec.effects if e.nested == graph_registry.KEPT], spec.name


def test_every_proposal_kind_is_either_applicable_or_named_as_not():
    kinds = {kind for spec in graph_registry.GRAPHS for kind in spec.proposals()}

    assert proposals.APPLICABLE <= kinds
    assert kinds - proposals.APPLICABLE == {"nt_launch", "nt_clarify"}


def test_nested_graphs_are_registered():
    for spec in graph_registry.GRAPHS:
        for child in spec.nests:
            assert graph_registry.get(child)


def test_build_gives_the_graph_the_server_compiles():
    for spec in graph_registry.GRAPHS:
        built = spec.build().compile()
        assert set(built.get_graph().nodes) == set(spec.graph.get_graph().nodes), spec.name


def test_an_unknown_graph_names_the_known_ones():
    with pytest.raises(KeyError, match="agent"):
        graph_registry.get("missing")
