"""Project completion is based on executed checks, never model prose."""
import asyncio
import json
from pathlib import Path

import pytest
from judecode.agent.project import ProjectContract
from judecode.agent.engine import AgentEngine
from judecode.agent.telegram_chat import ChatSession, ChatStore
from tests.test_reliability import ScriptedAPI, calls


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    obj = AgentEngine('system', ScriptedAPI(), sandbox_first=False)
    obj.autonomous.enabled = False
    obj.notifications.providers = []
    return obj


def plan(root):
    return {'goal': 'Deliver two features', 'root': str(root), 'features': [
        {'id': 'sale', 'criteria': 'sale reduces stock', 'checks': ['test -f sale.ok']},
        {'id': 'refund', 'criteria': 'refund restores stock', 'checks': ['test -f refund.ok']} ]}


def test_premature_final_keeps_running_but_is_bounded(agent, tmp_path):
    agent.api = ScriptedAPI(calls(('project_plan', plan(tmp_path))))
    asyncio.run(agent.chat('Build full project'))
    assert len(agent.api.requests) == 5
    assert agent.autonomous.session.status == 'unverified'
    assert not agent.project.complete
    assert 'refund' in agent.delivery_response('100% ready')
    assert '100%' not in agent.delivery_response('100% ready')


def test_real_checks_fail_then_repair_and_finish(agent, tmp_path):
    agent.api = ScriptedAPI(calls(('project_plan', plan(tmp_path))),
        calls(('project_verify', {})),
        calls(('write', {'path': str(tmp_path/'sale.ok'), 'content': 'ok'})),
        calls(('write', {'path': str(tmp_path/'refund.ok'), 'content': 'ok'})),
        calls(('project_verify', {})))
    asyncio.run(agent.chat('Build project'))
    assert agent.project.complete
    assert agent.autonomous.session.status == 'completed'
    assert agent.project.state['features']['sale']['evidence'][0]['exit_code'] == 0


def test_checks_use_root_not_process_cwd(agent, tmp_path):
    root = tmp_path/'actual'; root.mkdir()
    (root/'sale.ok').touch(); (root/'refund.ok').touch()
    agent._project_tool('project_plan', plan(root))
    agent._project_tool('project_verify', {})
    assert agent.project.complete


def test_mutation_invalidates_and_plan_cannot_erase_missing_features(agent, tmp_path):
    agent._project_tool('project_plan', plan(tmp_path))
    agent.project.verify(lambda *a: {'exit_code': 0}, lambda: False)
    agent._execute_tool_safe('write', {'path': str(tmp_path/'change'), 'content': 'x'})
    assert not agent.project.complete
    with pytest.raises(ValueError):
        agent.project.plan(**plan(tmp_path))
    assert len(agent.project.state['features']) == 2


def test_restore_keeps_contract_without_execution(agent, tmp_path):
    agent._project_tool('project_plan', plan(tmp_path))
    identifier = agent.autonomous.session.session_id
    restored = AgentEngine('system', ScriptedAPI(), sandbox_first=False)
    restored.resume_session(identifier)
    assert restored.project.state == agent.project.state
    assert not restored.api.requests
    assert restored.autonomous.evaluator.project_root == str(tmp_path)


def test_verification_obeys_shell_permissions(agent, tmp_path, monkeypatch):
    agent._project_tool('project_plan', plan(tmp_path))
    monkeypatch.setattr(agent.permissions, 'check_permission', lambda *a: (False, 'deny', 'blocked'))
    agent._project_tool('project_verify', {})
    assert not agent.project.complete
    assert agent.project.state['features']['sale']['evidence'][0]['exit_code'] == -1


def test_telegram_cannot_publish_unverified_success(agent, tmp_path):
    agent.api = ScriptedAPI(calls(('project_plan', plan(tmp_path))),
        calls(('project_pause', {'reason': 'Need deployment access'})))
    store = ChatStore(tmp_path/'transport'); store.enqueue(1, 'Build project')
    asyncio.run(ChatSession(agent, store).handle(store.claim()))
    response = store.pending_replies()[0]['response']
    assert 'unverified' in response and 'Need deployment access' in response


def test_repeated_action_stops(agent, tmp_path):
    for _ in range(6):
        agent._execute_tool_safe('write', {'path': str(tmp_path/'same'), 'content': 'same'})
    assert agent._stop_outcome[0] == 'unverified'


def test_cancel_does_not_mark_remaining_checks_passed(tmp_path):
    contract = ProjectContract(); contract.plan(**plan(tmp_path))
    contract.verify(lambda *a: {'exit_code': 0}, lambda: True)
    assert not contract.complete


def test_tampered_success_snapshot_rejected(tmp_path):
    contract = ProjectContract(); contract.plan(**plan(tmp_path))
    contract.state['features']['sale']['passed'] = True
    with pytest.raises(ValueError):
        ProjectContract(contract.state)


def test_followup_keeps_original_goal(agent, tmp_path):
    agent._project_tool('project_plan', plan(tmp_path))
    agent.api = ScriptedAPI(calls(('project_pause', {'reason': 'Need clarification'})))
    asyncio.run(agent.chat('Explain current progress'))
    assert agent.autonomous.session.original_goal == 'Deliver two features'
    assert len(agent.project.state['features']) == 2


def test_verify_retry_limit(tmp_path):
    contract = ProjectContract(); contract.plan(**plan(tmp_path))
    for _ in range(13):
        contract.verify(lambda *a: {'exit_code': 1}, lambda: False)
    assert contract.state['paused']
    assert not contract.complete


def test_multifile_work_cannot_skip_contract(agent, tmp_path):
    agent.api = ScriptedAPI(calls(
        ('write', {'path': str(tmp_path/'one.py'), 'content': 'x=1'}),
        ('write', {'path': str(tmp_path/'two.py'), 'content': 'x=2'})))
    asyncio.run(agent.chat('Implement two features'))
    assert agent._project_required
    assert agent.autonomous.session.status == 'unverified'
    assert 'not registered' in agent.autonomous.session.outcome_reason


def test_historical_failure_requires_evidence_before_resolution(agent, tmp_path):
    args = {'command': 'missing-python -m unittest'}
    agent._tool_failures[('shell', json.dumps(args, sort_keys=True))] = 'not found'
    resolution = {'failed_tool': 'shell', 'failed_arguments': json.dumps(args), 'reason': 'python3 checks now pass'}
    with pytest.raises(ValueError): agent._project_tool('project_resolve_failure', resolution)
    agent._project_tool('project_plan', plan(tmp_path))
    (tmp_path/'sale.ok').touch(); (tmp_path/'refund.ok').touch()
    agent._project_tool('project_verify', {})
    agent._project_tool('project_resolve_failure', resolution)
    assert not agent._tool_failures


def test_check_invocation_repair_preserves_criteria_and_audit(agent, tmp_path):
    agent._project_tool('project_plan', plan(tmp_path))
    agent._project_tool('project_revise_checks', {'id': 'sale', 'checks': ['test -e sale.ok'],
        'reason': 'Use portable file existence check'})
    feature = agent.project.state['features']['sale']
    assert feature['criteria'] == 'sale reduces stock'
    assert feature['revisions'][0]['checks'] == ['test -f sale.ok']
    assert not feature['passed']


def test_corrected_plan_clears_validation_failure(agent, tmp_path):
    invalid = plan(tmp_path); invalid['features'] = 'wrong type'
    assert agent._execute_tool_safe('project_plan', invalid).startswith('Error executing tool')
    agent._execute_tool_safe('project_plan', plan(tmp_path))
    assert not agent._tool_failures


def test_restore_rechecks_previously_passing_files(agent, tmp_path):
    agent._project_tool('project_plan', plan(tmp_path))
    (tmp_path/'sale.ok').touch(); (tmp_path/'refund.ok').touch()
    agent._project_tool('project_verify', {})
    assert agent.project.complete
    restored = AgentEngine('system', ScriptedAPI(), sandbox_first=False)
    restored.resume_session(agent.autonomous.session.session_id)
    assert not restored.project.complete
    assert restored.project.state['features']['sale']['evidence']


def test_provider_double_encoded_features_supported_without_losing_checks(tmp_path):
    args = plan(tmp_path); args['features'] = json.dumps(args['features'])
    contract = ProjectContract(); contract.plan(**args)
    assert len(contract.state['features']) == 2
    args['features'] = '[{"id":"sale","criteria":"x","checks":["true"],"checks":["false"]}]'
    with pytest.raises(ValueError): ProjectContract().plan(**args)


def test_invalid_control_arguments_do_not_create_unresolvable_failure(agent, tmp_path):
    agent._project_tool('project_plan', plan(tmp_path))
    (tmp_path/'sale.ok').touch(); (tmp_path/'refund.ok').touch()
    agent._project_tool('project_verify', {})
    result = agent._execute_tool_safe('project_resolve_failure', {
        'failed_tool': 'shell', 'failed_arguments': '', 'reason': 'bad invocation'})
    assert 'no unresolved implementation failures' in result
    assert not agent._tool_failures
    agent._finish_session()
    assert agent.autonomous.session.status == 'completed'


def test_real_empty_unittest_suite_cannot_complete(tmp_path):
    import subprocess
    import sys
    contract = ProjectContract()
    args = plan(tmp_path)
    args['features'] = [{'id': 'behavior', 'criteria': 'Requested behavior is tested',
                         'checks': [f'{sys.executable} -m unittest discover']}]
    contract.plan(**args)
    def run(command, root):
        result = subprocess.run(command, shell=True, cwd=root, capture_output=True, text=True)
        return dict(exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
    contract.verify(run, lambda: False)
    evidence = contract.state['features']['behavior']['evidence'][0]
    assert evidence['exit_code'] == 0
    assert evidence['validation_error'] == 'Test runner executed no tests'
    assert not contract.complete
    contract.state['features']['behavior']['passed'] = True
    with pytest.raises(ValueError):
        ProjectContract(contract.state)


def test_non_test_checks_and_real_tests_still_pass(tmp_path):
    contract = ProjectContract(); contract.plan(**plan(tmp_path))
    for output in ['', 'Ran 2 tests in 0.002s\n\nOK', 'Expected text: No tests found']:
        contract.verify(lambda *a: dict(exit_code=0, stdout=output), lambda: False)
        assert contract.complete


def test_failure_ids_resolve_exact_error_and_preserve_audit(agent, tmp_path):
    key = ('shell', json.dumps({'command':'missing-python test.py'}, sort_keys=True))
    agent._tool_failures[key] = 'not found'
    records = json.loads(agent._project_tool('project_failures', {}))
    resolution = {'failure_id': records[0]['failure_id'], 'reason': 'Tests now passed with python3'}
    with pytest.raises(ValueError):
        agent._project_tool('project_resolve_failure', resolution)
    agent._project_tool('project_plan', plan(tmp_path))
    (tmp_path/'sale.ok').touch(); (tmp_path/'refund.ok').touch()
    agent._project_tool('project_verify', {})
    agent._project_tool('project_resolve_failure', resolution)
    assert not agent._tool_failures
    assert agent.project.state['resolved_failures'][0]['error'] == 'not found'


@pytest.mark.parametrize('bad', ['', 'ls workspace', '[]', 'null'])
def test_bad_legacy_resolution_returns_recoverable_instructions(agent, tmp_path, bad):
    agent._tool_failures[('shell', '{}')] = 'historical failure'
    agent._project_tool('project_plan', plan(tmp_path))
    (tmp_path/'sale.ok').touch(); (tmp_path/'refund.ok').touch()
    agent._project_tool('project_verify', {})
    result = agent._execute_tool_safe('project_resolve_failure', {'failed_tool':'shell', 'failed_arguments':bad, 'reason':'superseded'})
    assert 'project_failures' in result and 'failure_id' in result
    assert 'JSONDecodeError' not in result
    assert agent._tool_failures
