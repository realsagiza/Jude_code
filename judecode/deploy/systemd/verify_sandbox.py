"""Run once using the scheduler unit's sandbox; no model calls or state changes."""
import os
from pathlib import Path
import socket
import urllib.request

assert os.getuid() != 0
status = Path('/proc/self/status').read_text()
assert 'NoNewPrivs:\t1' in status
assert 'CapEff:\t0000000000000000' in status
for target in ('/etc/judecode/agent.env', '/root/.ssh/authorized_keys'):
    try:
        with open(target):
            pass
    except PermissionError:
        pass
    else:
        raise AssertionError('Secret path accessible: ' + target)
for target in ('/etc/judecode-write-probe', '/opt/judecode/write-probe',
               '/var/lib/judecode/.judecode/permissions.json'):
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT, 0o600)
    except OSError as exc:
        assert exc.errno in (1, 13, 30), (target, exc.errno)
    else:
        os.close(fd)
        raise AssertionError('Protected path writable: ' + target)
probe = Path('/var/lib/judecode/workspaces/.sandbox-probe')
probe.write_text('ok')
probe.unlink()
with socket.socket() as sock:
    try:
        sock.bind(('0.0.0.0', 0))
    except PermissionError:
        pass
    else:
        raise AssertionError('Binding a server port was allowed')
for host in ('10.0.0.250', '169.254.169.254', '127.0.0.1'):
    with socket.socket() as sock:
        sock.settimeout(2)
        try:
            sock.connect((host, 22))
        except (PermissionError, TimeoutError):
            pass
        else:
            raise AssertionError('Private/metadata destination not blocked: ' + host)
with urllib.request.urlopen('https://openrouter.ai/api/v1/models', timeout=20) as response:
    assert response.status == 200
from judecode.agent.engine import AgentEngine
from judecode.config import SYSTEM_PROMPT, OPENROUTER_API_KEY
from judecode.api import create_api_client
from judecode.agent.safety import PermissionManager
assert OPENROUTER_API_KEY, 'Model key not configured'
request = urllib.request.Request('https://openrouter.ai/api/v1/auth/key',
                                 headers={'Authorization': 'Bearer ' + OPENROUTER_API_KEY})
with urllib.request.urlopen(request, timeout=20) as response:
    assert response.status == 200
permissions = PermissionManager()
permissions.load_from_env()
permissions.load_from_config()
assert permissions.check_permission('shell', {'command': 'id'})[1] == 'ask'
assert permissions.check_permission('read', {'path': 'README.md'})[1] == 'auto'
assert permissions.check_permission('schedule_checkpoint', {})[1] == 'auto'
print('PASS: headless engine import, API key authenticated, non-root, no capabilities, no privilege escalation, protected paths, writable workspace, no listeners, LAN/metadata blocked, public HTTPS and DNS, permission profile. No paid API calls.')
