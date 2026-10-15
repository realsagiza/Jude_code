import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from judecode.agent.engine import AgentEngine
from judecode.agent.sandbox_first import SandboxFirst, CONTROL_NAMES


@pytest.fixture
def engine(tmp_path, monkeypatch):
    project = tmp_path / 'source'
    project.mkdir()
    (project / 'app.py').write_text('original')
    (project / '.env').write_text('PROD_SECRET=hidden')
    monkeypatch.chdir(project)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    agent = AgentEngine('test', SimpleNamespace(), sandbox_first=True)
    agent.autonomous.enabled = False
    return agent


def test_default_is_sandbox_first(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    assert AgentEngine('test', SimpleNamespace()).isolation is not None


def test_file_edits_never_touch_source(engine):
    result = engine._execute_tool_safe('write', {'path': 'app.py', 'content': 'sandbox only'})
    assert not result.startswith('Error'), result
    assert Path('app.py').read_text() == 'original'
    assert engine.isolation.mapped('app.py').read_text() == 'sandbox only'
    assert not (engine.isolation.workspace / '.env').exists()


@pytest.mark.parametrize('path', ['../escaped', '/etc/hosts', '/tmp/deploy.sh'])
def test_path_escape_blocked(engine, path):
    assert engine._execute_tool_safe('write', {'path': path, 'content': 'bad'}).startswith('Error')


def test_symlink_escape_and_grep_option_blocked(engine, tmp_path):
    engine.isolation.ensure_workspace()
    (engine.isolation.workspace / 'escape').symlink_to(tmp_path)
    assert engine._execute_tool_safe('read', {'path': 'escape/anything'}).startswith('Error')
    assert engine._execute_tool_safe('grep', {'pattern': '--pre=evil'}).startswith('Error')


@pytest.mark.parametrize('tool,args', [('dev_sandbox_run', {'root': '.', 'command': 'ssh production', 'network': True}),
    ('dev_preview_start', {}), ('web_fetch', {'url': 'http://production/delete'}),
    ('unknown_host_tool', {}), ('keyboard_type', {'text': 'deploy'}), ('batch_copy', {})])
def test_alternative_host_routes_blocked(engine, tool, args):
    assert 'Sandbox-first blocks' in engine._execute_tool_safe(tool, args)


def test_shell_without_container_never_falls_back(engine, tmp_path):
    marker = tmp_path / 'production'
    result = engine._execute_tool_safe('shell', {'command': f'touch {marker}'})
    assert 'host execution is blocked' in result
    assert not marker.exists()


def test_model_only_sees_safe_tools(engine):
    names = {t['function']['name'] for t in engine._base_tools()}
    assert CONTROL_NAMES <= names
    assert 'dev_preview_start' not in names and 'web_fetch' not in names


def test_acceptance_runs_via_sandbox(engine, monkeypatch):
    engine._project_tool('project_plan', {'goal': 'test', 'root': str(Path.cwd()),
        'features': [{'id':'flow','criteria':'real behavior','checks':['python test.py']}]})
    calls = []
    def run(**args):
        calls.append(args)
        return {'exit_code': 0, 'stdout': 'one real check passed', 'stderr': ''}
    monkeypatch.setattr(engine.isolation, 'run', run)
    engine._project_tool('project_verify', {})
    assert calls[0]['cwd'] == str(engine.isolation.workspace)
    assert engine.project.complete


def test_host_evaluator_not_used(engine, monkeypatch):
    monkeypatch.setattr(engine.autonomous.evaluator, 'run_verification', lambda: pytest.fail('host verification ran'))
    assert not engine._verify_work()['passed']


def prepare(engine):
    return engine.isolation.prepare('printf approved > release.txt', 'test destination', 'restore backup', True)


def test_prepare_requires_checks(engine):
    with pytest.raises(ValueError, match='checks'):
        engine.isolation.prepare('touch production', 'production', 'restore', False)


@pytest.mark.parametrize('trusted,verified', [(False,True),(True,False)])
def test_untrusted_or_unverified_cannot_deploy(engine, trusted, verified):
    plan = prepare(engine)
    with pytest.raises(ValueError, match='blocked'):
        engine.isolation.release(plan['approval'], trusted, verified)
    assert not (engine.isolation.workspace / 'release.txt').exists()
    assert engine.isolation.pending is None


def test_approval_exact_single_use_and_stale_protected(engine):
    plan = prepare(engine)
    engine.isolation.mapped('app.py').write_text('new version')
    with pytest.raises(ValueError):
        engine.isolation.release(plan['approval'], True, True)
    plan = prepare(engine)
    result = engine.isolation.release(plan['approval'], True, True)
    assert result['exit_code'] == 0
    assert engine.isolation.mapped('release.txt').read_text() == 'approved'
    with pytest.raises(ValueError):
        engine.isolation.release(plan['approval'], True, True)


def test_expired_approval_blocked(engine):
    plan = prepare(engine)
    engine.isolation.pending['expires'] = 0
    with pytest.raises(ValueError):
        engine.isolation.release(plan['approval'], True, True)


def test_scheduled_chat_cannot_release(engine):
    plan = prepare(engine)
    asyncio.run(engine.chat(plan['approval']))
    assert 'locked' in engine.messages[-1]['content']
    assert not engine.isolation.mapped('release.txt').exists()


def test_restore_never_restores_deploy_permission(engine):
    prepare(engine)
    other = SandboxFirst()
    other.restore(engine.isolation.identifier)
    assert other.workspace == engine.isolation.workspace
    assert other.pending is None


def test_active_files_use_container_namespace_not_host_io(engine, monkeypatch):
    engine.isolation.ensure_workspace()
    engine.isolation.containers = [engine.isolation.app]
    monkeypatch.setattr(engine, '_snapshot_file', lambda *a: pytest.fail('host file snapshot'))
    calls = []
    monkeypatch.setattr(engine.isolation, 'files', lambda name, args: calls.append((name,args)) or 'container write')
    result = engine._execute_tool_safe('write', {'path': 'app.py', 'content': 'new'})
    assert result == 'container write' and calls
    assert Path('app.py').read_text() == 'original'


def test_session_memory_does_not_modify_project_files(engine):
    Path('package.json').write_text('{}')
    engine.autonomous.session.original_goal = 'A sufficiently long implementation request'
    engine._save_session_memory()
    assert not Path('JUDE.md').exists()


def test_direct_trusted_approval_executes_exact_plan_once(engine):
    engine.isolation.ensure_workspace()
    engine.project.plan('test', str(engine.isolation.workspace), [{'id':'test','criteria':'test passed','checks':['check']}])
    engine.project.verify(lambda *a: {'exit_code':0,'stdout':'assertion passed'}, lambda:False)
    plan = engine.isolation.prepare('printf approved > release.txt','temporary test artifact','remove test artifact',True)
    asyncio.run(engine.chat(plan['approval'], trusted_user=True))
    assert engine.isolation.mapped('release.txt').read_text() == 'approved'
    assert engine.isolation.pending is None
    assert not Path('release.txt').exists()
    assert engine.isolation is not None


def test_approval_delivery_includes_concrete_command(engine):
    plan = prepare(engine)
    response = engine.delivery_response('Please review')
    assert plan['command'] in response
    assert plan['destination'] in response
    assert plan['rollback'] in response
    assert plan['approval'] in response


@pytest.mark.parametrize('extra', [{'privileged': True}, {'volumes': ['/var/run/docker.sock:/sock']}, {'network_mode':'host'}])
def test_service_cannot_request_host_access(engine, monkeypatch, extra):
    from judecode.agent import sandbox_first as module
    monkeypatch.setattr(module, 'local_daemon', lambda: None)
    monkeypatch.setattr(module, 'checked', lambda *a: '29.2.1')
    monkeypatch.setattr(module, 'docker', lambda *a: pytest.fail('Docker launch should not occur'))
    with pytest.raises(ValueError, match='Only'):
        engine.isolation.start(services=[{'name':'db','image':'mongo:4.4', **extra}])


def test_artifact_digest_binds_file_boundaries_and_permissions(engine):
    root = engine.isolation
    root.ensure_workspace()
    root.mapped('a').write_bytes(b'x')
    root.mapped('b').write_bytes(b'y')
    first = root.fingerprint()
    root.mapped('a').write_bytes(b'xb\x00y')
    root.mapped('b').unlink()
    assert root.fingerprint() != first
    first = root.fingerprint()
    root.mapped('a').chmod(0o755)
    assert root.fingerprint() != first


@pytest.mark.parametrize('packages', [['https://example.com/package.tgz'], ['../source'], ['--script=bad'], ['pkg@file:/tmp/package']])
def test_node_downloader_rejects_urls_paths_options(engine, monkeypatch, packages):
    monkeypatch.setattr(engine.isolation, 'target', lambda service: 'fake')
    with pytest.raises(ValueError, match='package names'):
        engine.isolation.node_packages(packages)


def test_service_target_cannot_select_production_container(engine):
    engine.isolation.containers = [engine.isolation.app, 'unrelated-production']
    with pytest.raises(ValueError, match='declared'):
        engine.isolation.target('unrelated-production')


def test_remaining_service_cannot_trigger_host_file_fallback(engine, monkeypatch):
    engine.isolation.ensure_workspace()
    engine.isolation.containers = ['still-running-workspace-service']
    monkeypatch.setattr(engine, '_snapshot_file', lambda *a: pytest.fail('host snapshot'))
    monkeypatch.setattr(engine.isolation, 'files', lambda *a: 'namespace-only')
    assert engine._execute_tool_safe('write', {'path':'app.py','content':'changed'}) == 'namespace-only'
    assert Path('app.py').read_text() == 'original'


def test_container_cd_translated_and_artifact_released(engine, tmp_path):
    """Regression: actual /workspace/hr-system command must work on host."""
    root = engine.isolation
    folder = root.mapped('hr-system')
    folder.mkdir()
    (folder / 'build.txt').write_text('tested artifact')
    target = tmp_path / 'local-target'
    import shlex
    (folder / 'release.sh').write_text('set -eu\ncp build.txt ' + shlex.quote(str(target)) + '\n')
    plan = root.prepare('cd /workspace/hr-system && bash release.sh', str(target), 'remove local test target', True)
    assert plan['cwd'] == str(folder)
    assert not target.exists()
    result = root.release('deploy แผนนี้', True, True)
    assert result['exit_code'] == 0, result
    assert target.read_text() == 'tested artifact'
    assert not (root.source / 'hr-system').exists()
    with pytest.raises(ValueError):
        root.release('deploy แผนนี้', True, True)


def test_thai_space_directory_release(engine):
    root = engine.isolation
    root.mapped('ระบบ HR').mkdir()
    plan = root.prepare("cd '/workspace/ระบบ HR' && printf ok > result", 'local test', 'delete result', True)
    assert root.release(plan['approval'], True, True)['exit_code'] == 0
    assert root.mapped('ระบบ HR/result').read_text() == 'ok'


def test_git_script_rejected_before_stopping_preview(engine, monkeypatch):
    root = engine.isolation
    root.mapped('deploy.sh').write_text('git fetch origin\ngit rev-parse HEAD\n')
    root.containers = [root.app]
    monkeypatch.setattr(root, 'stop', lambda: pytest.fail('preview stopped before preflight'))
    with pytest.raises(ValueError, match='artifact release script'):
        root.prepare('bash deploy.sh', 'production', 'restore', True)
    assert root.pending is None


def test_missing_host_tool_preflight_never_executes_code(engine, monkeypatch):
    from judecode.agent import sandbox_first as module
    monkeypatch.setattr(module.shutil, 'which', lambda name: None)
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **kw: pytest.fail('host command ran'))
    context = engine.isolation.deployment_context()
    assert not context['production_contacted']
    assert context['commands']['ssh'] is None
    with pytest.raises(ValueError, match='Missing host'):
        engine.isolation.prepare('ssh production deploy', 'production', 'restore', True, required_commands=['ssh'])


@pytest.mark.parametrize('command', ['rsync /workspace/build prod:app', 'cd /workspace/hr; bash deploy.sh'])
def test_ambiguous_container_paths_rejected(engine, command):
    with pytest.raises(ValueError, match='Host has no /workspace'):
        engine.isolation.prepare(command, 'production', 'restore', True)


@pytest.mark.parametrize('message', ['deploy แผนนี้', 'ยืนยัน deploy', 'deploy this plan'])
def test_simple_confirmation_still_requires_direct_human(engine, message):
    prepare(engine)
    asyncio.run(engine.chat(message))
    assert 'locked' in engine.messages[-1]['content']
    assert not engine.isolation.mapped('release.txt').exists()


def test_simple_confirmation_engine_dispatch(engine):
    root = engine.isolation
    root.ensure_workspace()
    engine.project.plan('test', str(root.workspace), [{'id':'test','criteria':'passed','checks':['check']}])
    engine.project.verify(lambda *a: {'exit_code':0,'stdout':'assertion passed'}, lambda:False)
    prepare(engine)
    asyncio.run(engine.chat('deploy แผนนี้', trusted_user=True))
    assert root.mapped('release.txt').read_text() == 'approved'


@pytest.mark.parametrize('message', ['อย่า deploy แผนนี้', 'deploy แผนนี้ได้ไหม', 'continue', 'tests passed'])
def test_questions_negations_and_continuation_are_not_approval(message):
    assert not SandboxFirst.is_approval(message)


def test_internal_npm_symlink_allowed_but_external_rejected(engine, tmp_path):
    root = engine.isolation
    root.mapped('node_modules/.bin').mkdir(parents=True)
    root.mapped('node_modules/cli.js').write_text('script')
    link = root.workspace / 'node_modules/.bin/cli'
    link.symlink_to('../cli.js')
    before = root.fingerprint()
    root.mapped('node_modules/cli.js').write_text('changed')
    assert before != root.fingerprint()
    link.unlink()
    link.symlink_to(tmp_path)
    with pytest.raises(ValueError, match='symlink'):
        root.fingerprint()


def test_failure_is_reported_without_automatic_retry(engine):
    root = engine.isolation
    root.prepare('echo failed >&2; exit 9', 'local test', 'none', True)
    result = root.release('deploy แผนนี้', True, True)
    assert result['exit_code'] == 9
    assert 'failed' in result['stderr']
    assert 'Repair' in result['next_step']
    assert root.pending is None


def test_deployment_result_not_hidden_by_prior_session_failure(engine):
    prepare(engine)
    engine.autonomous.session.status = 'failed'
    asyncio.run(engine.chat('deploy แผนนี้', trusted_user=True))
    result = engine.messages[-1]['content']
    # Even blocked approval must report its actual cause to Telegram.
    assert engine.delivery_response(result) == result
