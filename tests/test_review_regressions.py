"""Independent review probes: assertions describe the expected fixed behavior.

All transports and model calls are fake. Project files and services are untouched.
"""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
import responses
from langchain_core.messages import AIMessage, HumanMessage

from agent import confluence, jira, jira_journal, jira_plan, jira_writer, nodes, publishers

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))


def jira_setup(monkeypatch):
    monkeypatch.setattr(jira_writer, '_type_rows', lambda *a: [])
    monkeypatch.setattr(jira_writer, '_schema', lambda *a: {})
    return jira.Settings(base_url='https://jira.example.com', token='fixture',
                         email='test@example.com', api_path='/rest/api/3', timeout_s=1)


@responses.activate
def test_lost_jira_response_is_reconciled_before_retry(monkeypatch, tmp_path):
    settings = jira_setup(monkeypatch)
    plan = jira_plan.parse(json.dumps({'issues': [{'id': 'T-1', 'type': 'Task', 'summary': 'Test'}]}))
    journal = jira_journal.Journal(tmp_path / 'journal.db')
    url = settings.base_url + settings.api_path + '/issue'
    # Jira accepted the request, but the response was lost.
    responses.add(responses.POST, url, body=requests.exceptions.ReadTimeout('lost response'))
    responses.add(responses.POST, url, json={'key': 'ORB-2'}, status=201)
    reconcile = Mock(return_value=[{'key': 'ORB-1', 'url': settings.base_url + '/browse/ORB-1'}])
    monkeypatch.setattr(jira, 'find_by_label', reconcile)
    first = jira_writer.create_issues(plan, 'ORB', settings=settings, run='run', journal=journal)
    recorded = journal.record('run', 'issue', 'T-1')['state']
    second = jira_writer.create_issues(plan, 'ORB', settings=settings, run='run', journal=journal)
    print({'first': first['status'], 'journal_after_timeout': recorded,
           'post_count': len(responses.calls), 'reconciliation_calls': reconcile.call_count,
           'second_keys': [row['key'] for row in second['created']]})
    assert reconcile.called and len(responses.calls) == 1


def test_jira_retry_restores_parent_of_already_created_child(monkeypatch, tmp_path):
    settings = jira_setup(monkeypatch)
    plan = jira_plan.parse(json.dumps({'issues': [
        {'id': 'E-1', 'type': 'Epic', 'summary': 'Parent'},
        {'id': 'T-1', 'type': 'Task', 'summary': 'Child', 'parent': 'E-1'},
    ]}))
    journal = jira_journal.Journal(tmp_path / 'journal.db')
    remote = {}
    attempts = {'parent': 0}
    def create(item, project, **kwargs):
        if item.local == 'E-1':
            attempts['parent'] += 1
            if attempts['parent'] == 1:
                raise jira.JiraError('HTTP 400: temporary invalid field')
        key = 'ORB-1' if item.local == 'E-1' else 'ORB-2'
        remote[key] = {'parent': kwargs.get('parent_key', '')}
        return {'key': key, 'url': settings.base_url + '/browse/' + key,
                'local': item.local, 'summary': item.summary, 'type': item.type}
    monkeypatch.setattr(jira_writer, 'create_issue', create)
    jira_writer.create_issues(plan, 'ORB', settings=settings, run='run', journal=journal)
    result = jira_writer.create_issues(plan, 'ORB', settings=settings, run='run', journal=journal)
    print({'status': result['status'], 'child_parent_after_retry': remote['ORB-2']['parent'],
           'warnings': result['warnings']})
    assert remote['ORB-2']['parent'] == 'ORB-1'


def test_confluence_write_uses_approved_version(monkeypatch):
    monkeypatch.setenv('PUBLISH_REQUIRE_APPROVAL', '1')
    monkeypatch.setenv('PUBLISH_TARGET', 'confluence')
    monkeypatch.setattr(confluence, 'missing_vars', lambda: [])
    settings = confluence.Settings(base_url='https://wiki.example.com', token='fixture',
                                   space_key='DEMO')
    monkeypatch.setattr(confluence, 'load_settings', lambda: settings)
    # The last read before publication saw v1. A remote edit lands before the write's GET.
    monkeypatch.setattr(publishers.ConfluencePublisher, 'preview',
                        lambda self, title: {'action': 'update', 'version': 1})
    captured = []
    def update(existing, title, body, settings):
        captured.append(existing['version']['number'])
        return {'id': '42', 'version': {'number': existing['version']['number'] + 1}}
    api = SimpleNamespace(find_page=lambda *a: {'id': '42', 'version': {'number': 2}}, update=update)
    monkeypatch.setattr(confluence, 'backend', lambda *a: api)
    state = {'messages': [HumanMessage('Test task'), AIMessage('Test result')]}
    config = {'configurable': {'thread_id': 'review'}}
    state.update(nodes.prepare_node(state, config))
    state['approval'] = {'decision': 'approved',
                         'plan_digest': nodes.commitment_digest(state['publication_plan'])}
    result = nodes.publish_node(state, config)
    print({'approved_version': 1, 'actually_overwritten_versions': captured,
           'publication': result['publication']['status']})
    assert not captured or captured == [1]


def test_runner_rejects_prepare_without_approved_parameters(monkeypatch, tmp_path):
    from test_nt_run import CAPS, PLAN

    from agent.nt_run.runner import Runner
    monkeypatch.setattr('agent.nt_run.runner.shutil.which', lambda *a: sys.executable)
    monkeypatch.setattr('agent.nt_run.runner.subprocess.run',
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout='', stderr=''))
    start_worker = Mock()
    monkeypatch.setattr('agent.nt_run.runner.subprocess.Popen', start_worker)
    runner = Runner(tmp_path, {'targets': CAPS['targets'], **CAPS['limits']})
    with pytest.raises(ValueError):
        result = runner.prepare_test(PLAN, 'unapproved-request')
        started = runner.start_test(result['prepared_id'], 'unapproved-smoke', smoke=True)
        print({'prepared_without_approval': bool(result['prepared_id']),
               'worker_launch_calls': start_worker.call_count, 'status': started['test_status']})


def test_nt_analysis_accounts_for_its_entire_spend(monkeypatch):
    from agent import nt_graph, nt_run_graph
    from agent.nt.collection import Sources
    monkeypatch.setenv('NT_RUNNER_URL', 'https://runner.example.com')
    monkeypatch.setattr(Sources, 'from_env', lambda *a: SimpleNamespace())
    captured = {}
    class Child:
        async def ainvoke(self, state, config):
            captured.update(state)
            inherited = state.get('spend') or {}
            return {'usage': state['usage'], 'spend': {
                'usd': inherited.get('usd', 0) + 0.2,
                'naive_usd': inherited.get('naive_usd', 0) + 0.2,
                'priced_calls': inherited.get('priced_calls', 0) + 1,
                'unpriced_calls': 0}, 'artifacts': {'report': 'Synthetic report'}}
    monkeypatch.setattr(nt_graph, 'build_graph', lambda **k: SimpleNamespace(compile=lambda: Child()))
    parent = nt_run_graph.build_graph()
    state = {'active_status': {'test_status': 'completed'}, 'attempt': 1,
             'usage': {'calls': 10}, 'spend': {'usd': 1.0, 'naive_usd': 1.0, 'priced_calls': 10}}
    result = asyncio.run(parent.nodes['analyze'].runnable.ainvoke(state, {}))
    print({'child_received_spend': captured.get('spend'), 'analysis_charge': result.get('spend'),
           'analysis': result.get('run_analysis')})
    assert result['spend']['usd'] == pytest.approx(0.2)


def test_live_eval_preserves_selected_model(monkeypatch, tmp_path):
    from evals import harness

    from agent import builder
    monkeypatch.setenv('LLM_PROVIDER', 'openai')
    monkeypatch.setenv('LLM_MODEL', 'configured-model')
    monkeypatch.setenv('LLM_API_KEY', 'synthetic-fixture')
    import os
    captured = {}
    def build(**kwargs):
        captured.update({name: os.getenv(name) for name in ['LLM_PROVIDER', 'LLM_MODEL', 'LLM_API_KEY']})
        return SimpleNamespace(compile=lambda: SimpleNamespace(invoke=lambda *a, **k: {}))
    monkeypatch.setattr(builder, 'build_graph', build)
    case = harness.load_cases(['incomplete-requirements'])[0]
    harness.run_case(case, tmp_path, live=True)
    print({'provider': captured['LLM_PROVIDER'], 'model': captured['LLM_MODEL'],
           'key_preserved': captured['LLM_API_KEY'] == 'synthetic-fixture'})
    assert captured['LLM_MODEL'] == 'configured-model'


def test_source_accuracy_rejects_fabricated_file_reference():
    from evals import checks
    measured = checks.measure({'report': '- The service должен always handle one billion RPS (nonexistent.md). '})
    print({'fake_reference_accuracy': measured['source_accuracy'],
           'unsupported_claims': measured['unsupported_claims']})
    assert measured['source_accuracy'] == 0


def test_simulated_capacity_is_not_exposed_as_established_in_ui_payload():
    from test_nt_verdict import state

    from agent.nt import assessment
    from agent.nt.settings import Settings
    result = assessment.assess(state(), Settings(step=30, simulated_sources=('prometheus',)))
    print({'top_level_capacity': result['maximum_stable_rps'],
           'ui_capacity': result['sla_verdict']['capacity']})
    assert result['sla_verdict']['capacity']['maximum_stable_rps'] is None


def test_old_usage_is_not_dropped_after_first_priced_call(monkeypatch):
    from agent import cost
    from agent.graph import build_graph
    monkeypatch.setenv('MEMORY_ENABLED', '0')
    monkeypatch.setenv('PUBLISH_TARGET', 'none')
    monkeypatch.setenv('BUDGET_USD_PER_THREAD', '0.5')
    for name in ['CACHE_HIT', 'CACHE_MISS', 'CACHE_WRITE', 'OUTPUT']:
        monkeypatch.setenv('PRICE_' + name + '_PER_MTOK', '1')
    model = Mock()
    model.invoke.return_value = AIMessage(content='Synthetic answer', response_metadata={
        'token_usage': {'prompt_cache_miss_tokens': 10, 'completion_tokens': 10}})
    # A legacy checkpoint has token usage, but no spend channel. 0.49 USD is already spent.
    result = build_graph(llm=model).compile().invoke(
        {'messages': [HumanMessage('Continue')], 'usage': {'cache_miss': 490000, 'calls': 10}},
        {'configurable': {'thread_id': 'legacy'}})
    print({'previous_usd': 0.49, 'new_calls': model.invoke.call_count,
           'reported_usd': cost.spent_usd(result)})
    assert cost.spent_usd(result) >= 0.49


def test_secret_scanner_detects_bearer_tokens_with_hyphens():
    sys.path.insert(0, str(ROOT / 'scripts'))
    import scan_secrets

    from agent import outgoing
    fixture = 'Authorization: Bearer ' + 'M' * 14 + '-' + 'Z' * 20
    runtime_findings = outgoing.verify(fixture)
    ci_findings = scan_secrets.scan_text(fixture, 'src/example.py')
    print({'runtime_finding_count': len(runtime_findings), 'ci_finding_count': len(ci_findings)})
    assert ci_findings
