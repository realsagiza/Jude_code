import json
import subprocess
import tarfile

import pytest

from judecode.agent import dev_sandbox as sandbox


def test_snapshot_filters_secrets_links_and_local_state(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'app.py').write_text('print(1)')
    (root / '.env').write_text('SECRET')
    (root / '.env.local').write_text('SECRET')
    (root / 'private.pem').write_text('SECRET')
    (root / 'outside').symlink_to('/etc/passwd')
    (root / '.git').mkdir()
    (root / '.git' / 'config').write_text('SECRET')
    archive = tmp_path / 'source.tar'
    first = sandbox.snapshot(root, archive)
    with tarfile.open(archive) as stream:
        assert stream.getnames() == ['app.py']
        assert stream.extractfile('app.py').read() == b'print(1)'
    (root / 'app.py').write_text('print(2)')
    assert sandbox.snapshot(root, archive) != first


@pytest.mark.parametrize('failure', ['test', 'setup', 'timeout', 'copy', 'cleanup', 'empty', None])
def test_lifecycle_failure_propagation_and_cleanup(tmp_path, monkeypatch, failure):
    calls = []
    def fake(args, timeout=60):
        calls.append(args)
        if failure == 'copy' and args[0] == 'cp':
            return 1, 'copy failed'
        if failure == 'cleanup' and args[0] == 'rm':
            return 1, 'daemon unavailable'
        if args[0] == 'exec' and args[-1] == 'install':
            return (1, 'setup failed') if failure == 'setup' else (0, 'installed')
        if args[0] == 'exec' and args[-1] == 'test':
            if failure == 'timeout':
                raise subprocess.TimeoutExpired(args, timeout)
            if failure == 'empty':
                return 0, 'Ran 0 tests in 0.000s\n'
            return (1, 'failed') if failure == 'test' else (0, 'Ran 3 tests\nOK')
        return 0, 'ok'
    monkeypatch.setattr(sandbox, 'docker', fake)
    result = sandbox.run_sandbox(str(tmp_path), 'test', setup='install')
    assert result['passed'] is (failure is None)
    assert calls[-1][:3] == ['rm', '-f', '-v']
    create = next(c for c in calls if c[0] == 'create')
    assert create[create.index('--network') + 1] == 'none'
    assert '--privileged' not in create and '--mount' not in create
    assert '--cap-drop' in create
    if failure == 'setup':
        assert not any(c[-1] == 'test' for c in calls)


def test_no_daemon_never_runs_host_command(tmp_path, monkeypatch):
    calls = []
    def fake(args, timeout=60):
        calls.append(args)
        return 1, 'Docker is unavailable'
    monkeypatch.setattr(sandbox, 'docker', fake)
    result = sandbox.run_sandbox(str(tmp_path), 'touch should-not-exist')
    assert not result['passed']
    assert len(calls) == 1
    assert not (tmp_path / 'should-not-exist').exists()


@pytest.mark.parametrize('kwargs', [{'image': '--privileged'}, {'timeout': 0}, {'timeout': True}, {'network': 'yes'}, {'command': ''}])
def test_invalid_inputs(tmp_path, kwargs):
    args = {'root': str(tmp_path), 'command': 'test', **kwargs}
    with pytest.raises(ValueError):
        sandbox.run_sandbox(**args)


def test_registered_tool_failure_is_engine_error(monkeypatch):
    from judecode.agent import tools
    assert any(t['function']['name'] == 'dev_sandbox_run' for t in tools.TOOL_DEFINITIONS)
    monkeypatch.setattr(tools, 'run_sandbox', lambda **kw: {'passed': False, 'exit_code': 7})
    assert tools.execute_tool('dev_sandbox_run', {}).startswith(tools.TOOL_ERROR_PREFIX)
    monkeypatch.setattr(tools, 'run_sandbox', lambda **kw: {'passed': True, 'exit_code': 0})
    assert json.loads(tools.execute_tool('dev_sandbox_run', {}))['passed']


@pytest.mark.parametrize('passed', [True, False])
def test_cli_exit_status_matches_evidence(tmp_path, monkeypatch, capsys, passed):
    import sys
    monkeypatch.setattr(sys, 'argv', ['dev_sandbox', '--root', str(tmp_path), 'python test.py'])
    monkeypatch.setattr(sandbox, 'run_sandbox', lambda **kw: {'passed': passed, 'exit_code': 0 if passed else 1})
    with pytest.raises(SystemExit) as exc:
        sandbox.main()
    assert exc.value.code == (0 if passed else 1)
    assert json.loads(capsys.readouterr().out)['passed'] is passed


def test_dependency_preflight_actionable_and_offline_tests_allowed():
    with pytest.raises(ValueError, match='network=true'):
        sandbox.dependency_preflight('pip install -r requirements.txt', '', False, False)
    with pytest.raises(ValueError, match='system_packages=true'):
        sandbox.dependency_preflight('apt-get update', '', True, False)
    sandbox.dependency_preflight('', 'npm test', False, False)
    sandbox.dependency_preflight('apt-get update', '', True, True)
    assert '--privileged' not in sandbox.capability_args(True)
    assert sandbox.capability_args(False) == []
