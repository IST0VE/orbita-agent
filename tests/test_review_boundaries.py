"""Transport, approval, accounting and evidence regressions from the second review."""

import io
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
import responses
from evals import checks, harness
from scripts.serve_nt_runner import make_handler
from test_nt_run import PLAN, local_runner
from test_review_regressions import jira_setup

from agent import confluence, cost, jira, jira_journal, jira_plan, jira_writer
from agent.nt_run.plan import commitment_of
from agent.state import _merge_spend, _merge_usage


@pytest.mark.parametrize('action', ['create', 'update'])
def test_approved_confluence_plan_reaches_the_write_unchanged(action, monkeypatch):
    from langchain_core.messages import AIMessage, HumanMessage

    from agent import nodes

    monkeypatch.setenv('PUBLISH_TARGET', 'confluence')
    monkeypatch.setenv('PUBLISH_REQUIRE_APPROVAL', '1')
    monkeypatch.setattr(confluence, 'missing_vars', lambda: [])
    settings = confluence.Settings(base_url='https://wiki.example.com', token='fixture', space_key='DEMO')
    monkeypatch.setattr(confluence, 'load_settings', lambda: settings)
    previous = {'id': '42', 'version': {'number': 1}} if action == 'update' else None
    reply = {'id': '42', 'version': {'number': 2 if previous else 1}}
    api = SimpleNamespace(find_page=Mock(return_value=previous),
                          read_page=Mock(return_value={'body': {'storage': {'value': '<p>Old</p>'}}}),
                          create=Mock(return_value=reply), update=Mock(return_value=reply))
    monkeypatch.setattr(confluence, 'backend', lambda *a: api)
    state = {'messages': [HumanMessage('Task'), AIMessage('Answer')]}
    config = {'configurable': {'thread_id': 'approval-regression'}}
    state.update(nodes.prepare_node(state, config))
    state['approval'] = {'decision': 'approved', 'plan_digest': nodes.commitment_digest(state['publication_plan'])}
    result = nodes.publish_node(state, config)
    assert result['publication']['status'] == ('updated' if previous else 'created')
    if previous:
        assert api.update.call_args.args[0] == previous
        api.create.assert_not_called()
    else:
        api.create.assert_called_once()
        api.update.assert_not_called()


@pytest.mark.parametrize('reply', ['timeout', 'disconnect', 'invalid-json', 'missing-key', 302, 408, 500, 503])
@responses.activate
def test_uncertain_issue_response_requires_reconciliation(reply, monkeypatch, tmp_path):
    settings = jira_setup(monkeypatch)
    url = settings.base_url + settings.api_path + '/issue'
    if reply in {'timeout', 'disconnect'}:
        exc = requests.ReadTimeout if reply == 'timeout' else requests.ConnectionError
        responses.add(responses.POST, url, body=exc('lost reply'))
    elif reply == 'invalid-json':
        responses.add(responses.POST, url, body='accepted', status=201)
    else:
        responses.add(responses.POST, url, json={}, status=reply if isinstance(reply, int) else 201)
    book = jira_journal.Journal(tmp_path / 'jira.db')
    plan = jira_plan.parse(json.dumps({'issues': [{'id': 'T-1', 'type': 'Task', 'summary': 'Task'}]}))
    lookup = Mock(side_effect=[[], [{'key': 'ORB-1', 'url': settings.base_url + '/browse/ORB-1'}]])
    monkeypatch.setattr(jira, 'find_by_label', lookup)
    first = jira_writer.create_issues(plan, 'ORB', settings=settings, run='r', journal=book)
    assert first['status'] == 'unknown'
    assert book.record('r', 'issue', 'T-1')['state'] == jira_journal.UNKNOWN
    assert jira_writer.create_issues(plan, 'ORB', settings=settings, run='r', journal=book)['status'] == 'unknown'
    last = jira_writer.create_issues(plan, 'ORB', settings=settings, run='r', journal=book)
    assert last['created'][0]['key'] == 'ORB-1'
    assert len(responses.calls) == 1


@responses.activate
def test_lost_link_response_is_reconciled_on_every_retry(monkeypatch, tmp_path):
    settings = jira_setup(monkeypatch)
    book = jira_journal.Journal(tmp_path / 'jira.db')
    for local, remote in [('T-1', 'ORB-1'), ('T-2', 'ORB-2')]:
        book.begin('r', 'issue', local)
        book.finish('r', 'issue', local, remote=remote, url=settings.base_url + '/browse/' + remote)
    plan = jira_plan.parse(json.dumps({'issues': [
        {'id': 'T-1', 'type': 'Task', 'summary': 'First'},
        {'id': 'T-2', 'type': 'Task', 'summary': 'Second', 'depends_on': ['T-1']},
    ]}))
    responses.add(responses.POST, settings.base_url + settings.api_path + '/issueLink',
                  body=requests.ReadTimeout('lost response'))
    monkeypatch.setattr(jira, 'find_link', Mock(side_effect=[False, True]))
    for expected in ['partial', 'partial', 'created']:
        assert jira_writer.create_issues(plan, 'ORB', settings=settings, run='r', journal=book)['status'] == expected
    assert len(responses.calls) == 1
    assert book.record('r', 'link', 'ORB-1->ORB-2')['state'] == jira_journal.COMPLETED


@pytest.mark.parametrize('epic_field', ['', 'customfield_10014'])
@responses.activate
def test_legacy_orphan_parent_is_restored_after_lost_put(epic_field, monkeypatch, tmp_path):
    settings = jira_setup(monkeypatch)
    monkeypatch.setattr(jira_writer, '_schema', lambda *a: {'epic_link': epic_field})
    book = jira_journal.Journal(tmp_path / 'jira.db')
    for local, remote in [('E-1', 'ORB-1'), ('T-1', 'ORB-2')]:
        book.begin('r', 'issue', local)
        book.finish('r', 'issue', local, remote=remote, url=settings.base_url + '/browse/' + remote)
    plan = jira_plan.parse(json.dumps({'issues': [
        {'id': 'E-1', 'type': 'Epic', 'summary': 'Parent'},
        {'id': 'T-1', 'type': 'Task', 'summary': 'Child', 'parent': 'E-1'},
    ]}))
    url = settings.base_url + settings.api_path + '/issue/ORB-2'
    field, value = (epic_field, 'ORB-1') if epic_field else ('parent', {'key': 'ORB-1'})
    responses.add(responses.GET, url, json={'fields': {}})
    responses.add(responses.PUT, url, body=requests.ReadTimeout('lost reply'))
    responses.add(responses.GET, url, json={'fields': {field: value}})
    assert jira_writer.create_issues(plan, 'ORB', settings=settings, run='r', journal=book)['status'] == 'partial'
    assert jira_writer.create_issues(plan, 'ORB', settings=settings, run='r', journal=book)['status'] == 'created'
    assert jira_writer.create_issues(plan, 'ORB', settings=settings, run='r', journal=book)['status'] == 'created'
    puts = [call for call in responses.calls if call.request.method == 'PUT']
    assert len(puts) == 1
    assert json.loads(puts[0].request.body)['fields'] == {field: value}


@pytest.mark.parametrize('action,existing', [
    ('create', {'id': '42', 'version': {'number': 1}}),
    ('update', {'id': '42', 'version': {'number': 2}}),
    ('update', {'id': '43', 'version': {'number': 1}}),
    ('update', None), ('unknown', None),
])
def test_confluence_refuses_changed_or_unknown_destination(action, existing, monkeypatch):
    api = SimpleNamespace(find_page=Mock(return_value=existing), update=Mock(), create=Mock())
    monkeypatch.setattr(confluence, 'backend', lambda *a: api)
    with pytest.raises(confluence.ConfluenceError):
        confluence.publish_page('Title', '<p>Body</p>',
            confluence.Settings(base_url='https://wiki.example.com', token='fixture', space_key='DEMO'),
            expected={'action': action, 'page_id': '42', 'version': 1})
    api.update.assert_not_called()
    api.create.assert_not_called()


@pytest.mark.parametrize('api_version', ['v1', 'v2'])
@responses.activate
def test_confluence_put_carries_exact_approved_version(api_version):
    settings = confluence.Settings(base_url='https://wiki.example.com', token='fixture',
        space_key='DEMO', space_id='7', api_version=api_version,
        api_path='/rest/api/content' if api_version == 'v1' else '/api/v2')
    url = settings.base_url + settings.api_path + ('/pages' if api_version == 'v2' else '')
    responses.add(responses.GET, url, json={'results': [{'id': '42', 'version': {'number': 3}}]})
    # Another writer wins after our GET. The server rejects our pinned version.
    responses.add(responses.PUT, url + '/42', status=409, json={'message': 'version conflict'})
    with pytest.raises(confluence.ConfluenceError):
        confluence.publish_page('Title', '<p>Body</p>', settings,
                                expected={'action': 'update', 'page_id': '42', 'version': 3})
    assert len(responses.calls) == 2
    assert json.loads(responses.calls[1].request.body)['version']['number'] == 4


def test_confluence_retry_does_not_adopt_a_new_version(monkeypatch):
    api = SimpleNamespace(find_page=Mock(side_effect=[{'id': '42', 'version': {'number': 1}},
                                                     {'id': '42', 'version': {'number': 2}}]),
                          update=Mock(side_effect=confluence.ConfluenceBlocked('lost response')))
    monkeypatch.setattr(confluence, 'backend', lambda *a: api)
    monkeypatch.setattr(confluence.request_pacing, 'block_delay', lambda *a: 0)
    with pytest.raises(confluence.ConfluenceError, match='подтвердите заново'):
        confluence.publish_page('Title', '<p>Body</p>',
            confluence.Settings(base_url='https://wiki.example.com', token='fixture', space_key='DEMO'),
            expected={'action': 'update', 'page_id': '42', 'version': 1})
    assert api.update.call_count == 1


@pytest.mark.parametrize('approved', [None, {}])
@pytest.mark.parametrize('endpoint', ['/prepare', '/start'])
def test_runner_http_requires_explicit_approval_at_both_boundaries(approved, endpoint, monkeypatch, tmp_path):
    runner, spawned = local_runner(tmp_path, monkeypatch)
    saved = commitment_of(PLAN, runner.capabilities())
    prepared = runner.prepare_test(PLAN, 'prepare', saved)['prepared_id']
    payload = {'plan': PLAN, 'prepared_id': prepared, 'key': 'unapproved', 'smoke': True}
    if approved is not None:
        payload['approved'] = approved
    body = json.dumps(payload).encode()
    handler = object.__new__(make_handler(runner, 'fixture'))
    handler.path = endpoint
    handler.headers = {'Authorization': 'Bearer fixture', 'Content-Length': str(len(body))}
    handler.rfile = io.BytesIO(body)
    handler.respond = Mock()
    handler.handle_api('POST')
    assert handler.respond.call_args.args[0] == 400
    assert spawned == []
    with runner.connect() as db:
        assert db.execute('SELECT count(*) FROM prepared').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0


def test_legacy_spend_migrates_once_and_survives_a_price_change(monkeypatch):
    for name in ['CACHE_HIT', 'CACHE_MISS', 'CACHE_WRITE', 'OUTPUT']:
        monkeypatch.setenv('PRICE_' + name + '_PER_MTOK', '1')
    state = {'usage': {'cache_miss': 490000, 'calls': 10}, 'spend': _merge_spend(None, {})}
    turn = {'cache_miss': 10000, 'calls': 1}
    money = cost.charge(turn, state=state)
    state = {'usage': _merge_usage(state['usage'], turn), 'spend': _merge_spend(state['spend'], money)}
    assert cost.spent_usd(state) == pytest.approx(.5)
    monkeypatch.setenv('PRICE_CACHE_MISS_PER_MTOK', '2')
    state['spend'] = _merge_spend(state['spend'], cost.charge(turn, state=state))
    assert cost.spent_usd(state) == pytest.approx(.52)
    assert state['spend']['estimated_calls'] == 10


def test_legacy_thread_stops_at_budget_after_migration(monkeypatch):
    from langchain_core.messages import AIMessage, HumanMessage

    from agent.graph import build_graph

    for name in ['CACHE_HIT', 'CACHE_MISS', 'CACHE_WRITE', 'OUTPUT']:
        monkeypatch.setenv('PRICE_' + name + '_PER_MTOK', '1')
    monkeypatch.setenv('MEMORY_ENABLED', '0')
    monkeypatch.setenv('PUBLISH_TARGET', 'none')
    monkeypatch.setenv('BUDGET_USD_PER_THREAD', '0.5')
    model = Mock()
    model.invoke.return_value = AIMessage(content='Answer', response_metadata={
        'token_usage': {'prompt_cache_miss_tokens': 10000}})
    result = build_graph(llm=model).compile().invoke(
        {'messages': [HumanMessage('Continue')], 'usage': {'cache_miss': 490000, 'calls': 10}},
        {'configurable': {'thread_id': 'legacy-budget'}})
    assert cost.spent_usd(result) == pytest.approx(.5)
    assert model.invoke.call_count == 1


@pytest.mark.parametrize('fails', [False, True])
def test_live_eval_preserves_prices_and_restores_environment_even_on_failure(fails, monkeypatch, tmp_path):
    from agent import builder

    expected = {'LLM_PROVIDER': 'openai', 'LLM_MODEL': 'selected-model',
                'OPENAI_API_KEY': 'fixture', 'PRICE_OUTPUT_PER_MTOK': '42'}
    for name, value in expected.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv('PUBLISH_TARGET', 'confluence')
    monkeypatch.setenv('LANGSMITH_TRACING', 'true')
    before = dict(os.environ)

    def build(**kwargs):
        assert {name: os.environ[name] for name in expected} == expected
        assert os.environ['PUBLISH_TARGET'] == 'file'
        assert os.environ['LANGSMITH_TRACING'] == 'false'
        if fails:
            raise ValueError('construction failed')
        return SimpleNamespace(compile=lambda: SimpleNamespace(invoke=lambda *a, **k: {}))

    monkeypatch.setattr(builder, 'build_graph', build)
    case = harness.load_cases(['incomplete-requirements'])[0]
    if fails:
        with pytest.raises(ValueError, match='construction failed'):
            harness.run_case(case, tmp_path, live=True)
    else:
        harness.run_case(case, tmp_path, live=True)
        harness.run_case(case, tmp_path, live=True)
    assert dict(os.environ) == before


def test_citations_require_matching_material_versions_and_factual_support():
    materials = {'source.md': 'The service must return an identifier.'}
    versions = checks.material_versions(materials)
    supported = 'Сервис должен вернуть идентификатор (source.md).'
    facts = [{'pattern': r'Сервис должен вернуть идентификатор \(source\.md\)\.',
              'sources': {'source.md': materials['source.md']}}]

    def measure(text, **kwargs):
        return checks.measure({'report': text}, materials=kwargs.get('materials', materials),
                              versions=kwargs.get('versions', versions), facts=facts)

    assert measure(supported)['grounded_claims'] == 1
    invented = measure('Сервис должен выдержать миллиард RPS (source.md).')
    assert invented['source_accuracy'] == 1
    assert invented['grounded_claims'] == 0 and invented['unsupported_claims'] == 1
    for bad in [supported.replace('source.md', 'nonexistent.md'),
                supported.replace('source.md', 'source.md@sha256:deadbeef')]:
        assert measure(bad)['source_accuracy'] == 0
        assert measure(bad)['unsupported_claims'] == 1
    assert measure(supported, materials={'source.md': 'Changed requirement'})['source_accuracy'] == 0
    assert measure(supported, versions={})['source_accuracy'] == 0


def test_a_contradiction_needs_both_actual_evidence_spans():
    case = harness.load_cases(['contradiction'])[0]
    kwargs = {'materials': case['materials'], 'versions': case['material_versions'],
              'contradictions': case['contradictions']}
    assert checks.measure({'report': case['answers'][0]}, **kwargs)['verified_contradictions'] == 2
    assert checks.measure({'report': 'Противоречие: source.md не согласуется с fake.md'}, **kwargs)['verified_contradictions'] == 0
