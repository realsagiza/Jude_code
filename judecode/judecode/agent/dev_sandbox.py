"""Disposable Docker verification machines; never mount the source or host socket."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import uuid

from judecode.agent.project import definition, _empty_test_run

EXCLUDED = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.pytest_cache',
            '.judecode', '.ssh', '.aws', '.config', 'logs', 'dist', 'build'}


def snapshot(root, destination):
    """Copy regular files only; omit secrets, symlinks, devices and local state."""
    digest = hashlib.sha256()
    total = 0
    with tarfile.open(destination, 'w') as archive:
        for folder, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDED and not d.startswith('.env') and not Path(folder, d).is_symlink())
            for name in sorted(files):
                path = Path(folder, name)
                if (path.is_symlink() or not path.is_file() or name.startswith('.env') or
                        name.endswith(('.pem', '.key', '.p12', '.pfx')) or
                        name in {'id_rsa', 'id_ed25519', '.npmrc', '.pypirc', '.netrc'}):
                    continue
                size = path.stat().st_size
                total += size
                if total > 256 * 1024 * 1024:
                    raise ValueError('Source snapshot exceeds 256 MiB; narrow the project root')
                relative = path.relative_to(root).as_posix()
                info = archive.gettarinfo(str(path), arcname=relative)
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                info.mode = 0o755 if info.mode & 0o111 else 0o644
                with path.open('rb') as stream:
                    archive.addfile(info, stream)
    with Path(destination).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def docker(args, timeout=60):
    # File-backed output avoids unbounded memory consumption by noisy tests.
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(['docker', *args], stdout=output, stderr=subprocess.STDOUT,
                                timeout=timeout, check=False)
        output.seek(0, 2)
        length = output.tell()
        output.seek(max(0, length - 24000))
        text = output.read().decode('utf-8', errors='replace')
        return result.returncode, ('[output truncated]\n' if length > 24000 else '') + text



def capability_args(system_packages):
    if type(system_packages) is not bool:
        raise ValueError('system_packages must be boolean')
    # Only opt-in package installation capabilities; never privileged/host mounts.
    return [item for cap in ('CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'SETUID', 'SETGID')
            for item in ('--cap-add', cap)] if system_packages else []


def dependency_preflight(setup, command, network, system_packages):
    text = setup + '\n' + command
    if not network and re.search(r'\b(?:pip3?|npm|pnpm|yarn|apt-get|apt|apk)\s+(?:(?:-[^\s]+)\s+)*(?:install|ci|update|add)\b', text):
        raise ValueError('Dependency commands need network=true. Put installs in setup; do not hide failures with pipes or output redirection.')
    if not system_packages and re.search(r'\b(apt-get|apt|apk)\s+(?:update|install|add)\b', text):
        raise ValueError('OS package installation needs system_packages=true and network=true; or use an image with the packages preinstalled. Python/Node packages do not need system_packages.')

def run_sandbox(root, command, image='python:3.12-slim', setup='', network=False, timeout=120, system_packages=False):
    root = Path(root).expanduser().resolve()
    if not root.is_dir() or root == Path(root.anchor) or root == Path.home():
        raise ValueError('Use an existing project directory, not home or filesystem root')
    if not isinstance(command, str) or not command.strip():
        raise ValueError('A real test command is required')
    if type(timeout) is not int or not 1 <= timeout <= 600 or type(network) is not bool:
        raise ValueError('timeout must be 1..600 seconds; network must be boolean')
    if not isinstance(setup, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:@-]*', image):
        raise ValueError('Invalid setup or Docker image')
    caps = capability_args(system_packages)
    dependency_preflight(setup, command, network, system_packages)
    name = 'judecode-dev-' + uuid.uuid4().hex
    receipt = {'container': name, 'root': str(root), 'image': image, 'command': command,
               'setup': setup, 'network': network, 'passed': False, 'exit_code': None}
    created = False
    def checked(args, seconds=60):
        code, output = docker(args, seconds)
        if code:
            raise RuntimeError(output.strip() or f'Docker exited {code}')
        return output.strip()
    try:
        checked(['info', '--format', '{{.ServerVersion}}'], 15)
        with tempfile.TemporaryDirectory(prefix='judecode-dev-') as temporary:
            archive = Path(temporary, 'source.tar')
            receipt['source_sha256'] = snapshot(root, archive)
            # Pull separately, so cleanup cannot race a timed-out docker create pull.
            code, _ = docker(['image', 'inspect', image], 15)
            if code:
                checked(['pull', image], 300)
            receipt['image_id'] = checked(['image', 'inspect', '--format', '{{.Id}}', image], 15)
            created = True
            checked(['create', '--name', name, '--label', 'judecode.dev-sandbox=true',
                     '--network', 'bridge' if network else 'none', '--cap-drop', 'ALL', *caps,
                     '--security-opt', 'no-new-privileges', '--memory', '1g', '--memory-swap', '1g',
                     '--cpus', '2', '--pids-limit', '128', '--init', '--no-healthcheck',
                     '--workdir', '/workspace', '--entrypoint', '/bin/sh', image,
                     '-c', 'while :; do sleep 3600; done'])
            checked(['cp', str(archive), name + ':/tmp/judecode-source.tar'])
            checked(['start', name])
            checked(['exec', name, 'sh', '-c', 'tar xf /tmp/judecode-source.tar -C /workspace && rm /tmp/judecode-source.tar'])
            if setup.strip():
                code, output = docker(['exec', name, 'sh', '-lc', setup], timeout)
                receipt['setup_output'] = output
                if code:
                    receipt['exit_code'] = code
                    raise RuntimeError('Dependency setup failed')
            code, output = docker(['exec', name, 'sh', '-lc', command], timeout)
            receipt.update(exit_code=code, output=output, passed=code == 0 and not _empty_test_run(output))
            if code == 0 and _empty_test_run(output):
                receipt['error'] = 'Empty test suite is not verification'
    except subprocess.TimeoutExpired:
        receipt.update(error='Sandbox operation timed out', exit_code=124)
    except (OSError, RuntimeError, ValueError) as exc:
        receipt['error'] = str(exc)
    finally:
        if created:
            try:
                code, output = docker(['rm', '-f', '-v', name], 30)
                receipt['cleaned_up'] = code == 0
                if code:
                    receipt.update(passed=False, cleanup_error=output)
            except (OSError, subprocess.TimeoutExpired) as exc:
                receipt.update(passed=False, cleaned_up=False, cleanup_error=str(exc))
    return receipt


DEFINITIONS = [definition('dev_sandbox_run',
    'Create a disposable Linux test machine, copy project source, install dependencies, run tests and destroy it. '
    'No host mounts/credentials. Default offline; network=true allows outbound access including LAN. '
    'Use test data only. Commands run in /workspace. Does not deploy or modify source. '
    'Tests may start a server and exercise it inside the same command.',
    {'root': {'type': 'string'}, 'command': {'type': 'string'}, 'image': {'type': 'string'},
     'system_packages': {'type': 'boolean', 'description': 'Opt in to limited capabilities required for apt/apk installs. Default false.'},
     'setup': {'type': 'string'}, 'network': {'type': 'boolean'}, 'timeout': {'type': 'integer'}},
    ['root', 'command'])]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='.')
    parser.add_argument('--image', default='python:3.12-slim')
    parser.add_argument('--setup', default='')
    parser.add_argument('--system-packages', action='store_true')
    parser.add_argument('--network', action='store_true')
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('command')
    result = run_sandbox(**vars(parser.parse_args()))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
