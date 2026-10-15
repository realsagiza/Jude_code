"""Managed localhost web previews in expiring Docker containers."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid

from judecode.agent.dev_sandbox import docker, snapshot
from judecode.agent.project import definition

LABEL = 'judecode.web-preview'


def checked(args, timeout=60):
    code, output = docker(args, timeout)
    if code:
        raise RuntimeError(output.strip() or f'Docker exited {code}')
    return output.strip()


def local_daemon():
    endpoint = os.environ.get('DOCKER_HOST', '') if not os.environ.get('DOCKER_CONTEXT') else ''
    if not endpoint:
        endpoint = checked(['context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'], 15)
    if not endpoint.startswith(('unix://', 'npipe://')):
        raise ValueError('Preview requires a local Docker daemon (Docker Desktop or Colima)')


def inspect_preview(container):
    if not isinstance(container, str) or not re.fullmatch(r'judecode-preview-[0-9a-f]{32}', container):
        raise ValueError('Invalid preview container name')
    data = json.loads(checked(['inspect', container], 15))[0]
    if data.get('Config', {}).get('Labels', {}).get(LABEL) != 'true':
        raise ValueError('Container is not a Jude Code web preview')
    return data


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def http_ready(url):
    # Do not inherit proxies or follow an application redirect to another host.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(url, timeout=1) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (OSError, urllib.error.URLError):
        return None


def preview_status(container):
    local_daemon()
    data = inspect_preview(container)
    ports = data.get('NetworkSettings', {}).get('Ports', {})
    bindings = [binding for values in ports.values() for binding in (values or [])
                if binding.get('HostIp') == '127.0.0.1']
    url = f"http://127.0.0.1:{bindings[0]['HostPort']}/" if bindings else None
    code = http_ready(url) if url and data['State']['Running'] else None
    _, logs = docker(['logs', '--tail', '80', container], 15)
    return {'container': container, 'running': data['State']['Running'], 'url': url,
            'http_status': code, 'ready': code is not None and 200 <= code < 400,
            'logs': logs, 'expires_at': data['Config']['Labels'].get('judecode.expires-at')}


def preview_stop(container):
    local_daemon()
    inspect_preview(container)  # Never delete unrelated containers, even with a matching name.
    checked(['rm', '-f', '-v', container], 30)
    return {'container': container, 'stopped': True}


def preview_start(root, command, port, image='python:3.12-slim', setup='', timeout=60, ttl=3600):
    root = Path(root).expanduser().resolve()
    if not root.is_dir() or root in {Path.home(), Path(root.anchor)}:
        raise ValueError('Use an existing project directory, not home or filesystem root')
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('port must be 1..65535')
    if type(timeout) is not int or not 1 <= timeout <= 120:
        raise ValueError('readiness timeout must be 1..120 seconds')
    if type(ttl) is not int or not 5 <= ttl <= 14400:
        raise ValueError('ttl must be 5..14400 seconds')
    if not isinstance(command, str) or not command.strip() or not isinstance(setup, str):
        raise ValueError('command is required and setup must be text')
    if not isinstance(image, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:@-]*', image):
        raise ValueError('Invalid image')
    local_daemon()
    name = 'judecode-preview-' + uuid.uuid4().hex
    created = False
    receipt = {'container': name, 'ready': False}
    try:
        checked(['info', '--format', '{{.ServerVersion}}'], 15)
        if docker(['image', 'inspect', image], 15)[0]:
            checked(['pull', image], 300)
        receipt['image_id'] = checked(['image', 'inspect', '--format', '{{.Id}}', image], 15)
        with tempfile.TemporaryDirectory(prefix='judecode-preview-') as temporary:
            archive = Path(temporary, 'source.tar')
            receipt['source_sha256'] = snapshot(root, archive)
            # Timer is a sibling of the app shell. PID 1 exiting tears down all descendants.
            runner = ('(sleep "$3"; kill -TERM $$) & timer=$!; '
                      'trap \'kill "$timer" 2>/dev/null; exit 124\' TERM INT; '
                      '(tar xf /tmp/judecode-source.tar -C /workspace && '
                      'rm /tmp/judecode-source.tar && sh -lc "$1" && exec sh -lc "$2") & '
                      'app=$!; wait "$app"; result=$?; kill "$timer" 2>/dev/null; exit "$result"')
            created = True
            checked(['create', '--rm', '--name', name, '--label', LABEL + '=true',
                     '--label', f'judecode.expires-at={int(time.time()) + ttl}',
                     '--publish', f'127.0.0.1::{port}', '--network', 'bridge',
                     '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                     '--memory', '1g', '--memory-swap', '1g', '--cpus', '2', '--pids-limit', '128',
                     '--log-opt', 'max-size=5m', '--log-opt', 'max-file=1',
                     '--init', '--no-healthcheck', '--workdir', '/workspace',
                     '--entrypoint', '/bin/sh', image, '-c', runner, 'preview', setup or ':', command, str(ttl)])
            checked(['cp', str(archive), name + ':/tmp/judecode-source.tar'])
            checked(['start', name])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            receipt.update(preview_status(name))
            if receipt['ready']:
                return receipt
            time.sleep(.25)
        raise RuntimeError('Web preview did not become ready; bind the server to 0.0.0.0 and the requested port')
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        receipt.update(ready=False, error=str(exc))
        if created:
            try:
                code, output = docker(['rm', '-f', '-v', name], 30)
                # --rm may already have removed an exited server.
                receipt['cleaned_up'] = code == 0 or 'No such container' in output
                if not receipt['cleaned_up']:
                    receipt['cleanup_error'] = output
            except (OSError, subprocess.TimeoutExpired) as cleanup:
                receipt['cleanup_error'] = str(cleanup)
        return receipt


FUNCTIONS = {'dev_preview_start': preview_start, 'dev_preview_status': preview_status,
             'dev_preview_stop': preview_stop}
DEFINITIONS = [
    definition('dev_preview_start',
        'Start a web preview in a fresh Linux snapshot and return a localhost URL. Server must bind '
        '0.0.0.0 on port and remain in foreground. Bridge network allows outbound/LAN access; use test data only. '
        'No host mounts or env. Preview expires after ttl seconds (default 1 hour). Restart to pick up source edits. '
        'HTTP readiness is not acceptance-test success. Local Docker required.',
        {'root': {'type': 'string'}, 'command': {'type': 'string'}, 'port': {'type': 'integer'},
         'image': {'type': 'string'}, 'setup': {'type': 'string'}, 'timeout': {'type': 'integer'},
         'ttl': {'type': 'integer'}}, ['root', 'command', 'port']),
    definition('dev_preview_status', 'Read preview URL, HTTP readiness, expiry and recent logs.',
               {'container': {'type': 'string'}}, ['container']),
    definition('dev_preview_stop', 'Stop and remove a Jude Code preview container.',
               {'container': {'type': 'string'}}, ['container']),
]
