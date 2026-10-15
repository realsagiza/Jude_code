import json

import pytest

from judecode.agent import dev_preview as preview

NAME = 'judecode-preview-' + 'a' * 32


def test_only_managed_containers_can_be_stopped(monkeypatch):
    calls = []
    monkeypatch.setattr(preview, 'local_daemon', lambda: None)
    def fake(args, timeout=60):
        calls.append(args)
        return json.dumps([{'Config': {'Labels': {}}}])
    monkeypatch.setattr(preview, 'checked', fake)
    with pytest.raises(ValueError, match='not a Jude'):
        preview.preview_stop(NAME)
    assert all(c[0] != 'rm' for c in calls)
    with pytest.raises(ValueError, match='Invalid'):
        preview.preview_stop('other-container')


def test_remote_daemon_rejected(monkeypatch):
    monkeypatch.delenv('DOCKER_CONTEXT', raising=False)
    monkeypatch.setenv('DOCKER_HOST', 'ssh://remote')
    with pytest.raises(ValueError, match='local Docker'):
        preview.local_daemon()


@pytest.mark.parametrize('kwargs', [{'port': 0}, {'port': True}, {'ttl': 0}, {'timeout': 121}, {'image': '--privileged'}, {'command': ''}])
def test_invalid_start(tmp_path, kwargs):
    with pytest.raises(ValueError):
        preview.preview_start(**{'root': str(tmp_path), 'command': 'server', 'port': 8000, **kwargs})


@pytest.mark.parametrize('ready', [True, False])
def test_start_binding_and_failed_readiness_cleanup(tmp_path, monkeypatch, ready):
    calls = []
    monkeypatch.setattr(preview, 'local_daemon', lambda: None)
    def fake(args, timeout=60):
        calls.append(args)
        return 0, 'ok'
    monkeypatch.setattr(preview, 'docker', fake)
    monkeypatch.setattr(preview, 'preview_status', lambda name: {'ready': ready, 'url': 'http://127.0.0.1:1234/'})
    clock = iter([0, 0, 3])
    monkeypatch.setattr(preview.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(preview.time, 'sleep', lambda _: None)
    result = preview.preview_start(str(tmp_path), 'server', 8000, timeout=2)
    assert result['ready'] is ready
    create = next(c for c in calls if c[0] == 'create')
    assert create[create.index('--publish') + 1] == '127.0.0.1::8000'
    assert '--rm' in create and '--mount' not in create
    assert any(c[0] == 'rm' for c in calls) is (not ready)


def test_status_does_not_treat_http_error_as_ready(monkeypatch):
    monkeypatch.setattr(preview, 'local_daemon', lambda: None)
    monkeypatch.setattr(preview, 'inspect_preview', lambda name: {
        'State': {'Running': True}, 'Config': {'Labels': {}},
        'NetworkSettings': {'Ports': {'8000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '1234'}]}}})
    monkeypatch.setattr(preview, 'http_ready', lambda url: 500)
    monkeypatch.setattr(preview, 'docker', lambda *a: (0, 'error log'))
    result = preview.preview_status(NAME)
    assert result['http_status'] == 500 and not result['ready']
    assert result['logs'] == 'error log'


def test_tools_registered_and_failures_propagated(monkeypatch):
    from judecode.agent import tools
    names = {t['function']['name'] for t in tools.TOOL_DEFINITIONS}
    assert set(preview.FUNCTIONS) <= names
    monkeypatch.setitem(tools.PREVIEW_FUNCTIONS, 'dev_preview_start', lambda **kw: {'error': 'failed', 'ready': False})
    assert tools.execute_tool('dev_preview_start', {}).startswith(tools.TOOL_ERROR_PREFIX)
