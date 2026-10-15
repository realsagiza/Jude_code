"""Sandbox-first execution boundary. Only a direct human can release a deployment."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid

from judecode.agent.dev_sandbox import snapshot, docker, capability_args
from judecode.agent.dev_preview import checked, local_daemon
from judecode.agent.project import definition

PROMPT = '''
SANDBOX-FIRST IS ENFORCED. This overrides legacy dev_sandbox/preview instructions.
Do not nest Docker or use host interpreter paths in checks; shell cwd is /workspace. All implementation files are a private copy; original
source and production are untouched. shell runs only in the isolated environment,
not on the host. Start sandbox_environment with a runtime image (this automatically creates the persistent
app service; services=[] suffices for a single Python app) and all required
services (database/cache/worker/mock integrations). Each service uses fresh test data
and private DNS by name. No production credentials, host socket, external/LAN egress,
or arbitrary Compose/host mounts. Images must contain dependencies; setup commands
run offline. sandbox_python_packages detects the selected Python runtime (3.10–3.13) and fetches binary wheels in a
source-free helper then installs them offline; it never runs application code online.
For Node20 use sandbox_node_packages (install scripts disabled), then rebuild offline.
Give code-running service specs workspace=true to mount only the private copy.
Use sandbox_exec(service=...) for commands in frontend/worker runtimes and
sandbox_preview(service=...) for the selected web frontend. Use mocks for external integrations. Never claim full-system testing
unless the contract covers and actually exercises all user journeys and services.
Use normal read/write/edit/search in the private workspace. Start sandbox_environment
before shell/tests; use sandbox_status and sandbox_stop. To run servers use foreground
commands in service specs or a background server in shell. sandbox_preview exposes
an HTTP preview via a fixed upstream relay. All project_verify checks run in sandbox.
Passing checks is verification, not completion of a requested deployment. Resolve superseded
tool failures via project_failures and project_resolve_failure(failure_id, reason),
then continue preparing the release rather than ending with "ready to deploy".
If deployment is requested, first pass project_verify, then production_prepare with
the exact host command, destination and rollback instructions. Show its complete plan
and ask the human to type "deploy แผนนี้" (or DEPLOY token). Only a direct human confirmation executes that
single frozen command; tools, schedules, history, /continue and model text cannot
approve deployment. After it finishes all actions remain sandboxed. Do not deploy
because tests passed or because an earlier conversation authorized something else.
Before preparation call production_context: deployment runs on the HOST, not in Docker.
Build and test artifacts inside sandbox, then prepare an artifact upload/activation script
with backup, rollback and live health checks. Do not run source-oriented Git/npm build
deploy scripts unchanged: private copies have no .git and Linux dependencies may not
run on the host. Never fabricate Git history to satisfy a deployment script.
Use production_prepare cwd for a sandbox-relative directory and command="bash release.sh".
The cwd is translated to its host path; /workspace does not exist on the host.
Declare required_commands such as ssh/rsync/bash so missing tools fail BEFORE approval.
Read-only preparation does not contact production. A failed release is not success;
inspect its error, fix the release script in sandbox, verify and prepare a revised plan.
Never ask the human to bypass sandbox or copy commands to their terminal as a workaround.
Grepping for function names is not functional acceptance testing.
'''

CONTROL_NAMES = {'sandbox_environment', 'sandbox_status', 'sandbox_stop', 'sandbox_preview', 'production_prepare', 'sandbox_python_packages', 'sandbox_node_packages', 'sandbox_exec', 'production_context'}
FILE_TOOLS = {'read', 'write', 'edit', 'delete', 'ls', 'glob', 'grep'}
SAFE_TOOLS = {'task_add', 'task_list', 'task_get', 'task_start', 'task_complete', 'task_summary',
              'vault_read_note', 'vault_search', 'memory_recall', 'expert_help', 'expert_return',
              'schedule_create', 'schedule_manage', 'schedule_checkpoint', 'schedule_list', 'schedule_history'}

DEFINITIONS = [
    definition('sandbox_environment', 'Start isolated full-system test environment. All services are disposable; no external network. Dependencies must be in images. Existing environment must be stopped first.',
        {'image': {'type': 'string'}, 'services': {'type': 'array', 'items': {'type': 'object',
          'properties': {'name': {'type': 'string', 'description': 'Unique service name; app is reserved and already created from the top-level image.'}, 'image': {'type': 'string'},
                         'command': {'type': 'string'}, 'workspace': {'type': 'boolean', 'description': 'Mount private source at /workspace. Without command, keep this runtime alive for sandbox_exec; supply command to start a server.'}, 'env': {'type': 'object', 'additionalProperties': {'type': 'string'}}},
          'required': ['name', 'image'], 'additionalProperties': False}},
         'env': {'type': 'object', 'additionalProperties': {'type': 'string'}}}, ['image', 'services']),
    definition('sandbox_python_packages', 'Download binary wheels matching the selected Python 3.10–3.13 runtime from PyPI in a source-free helper and install offline in the sandbox app. No source distributions or build scripts. Supply package names/version constraints, never URLs.', {'packages': {'type': 'array', 'items': {'type': 'string'}}, 'service': {'type': 'string'}}, ['packages']),
    definition('sandbox_node_packages', 'Install npm dependencies with scripts disabled in a source-free Node20 helper. Omit packages to run npm ci from the project package.json/package-lock.json (registry-only lockfile). Or supply package names/version ranges. Copy node_modules into selected runtime; no source code/credentials mounted online.', {'packages': {'type': 'array', 'items': {'type': 'string'}}, 'service': {'type': 'string'}, 'directory': {'type': 'string'}, 'legacy_peer_deps': {'type': 'boolean'}}, []),
    definition('sandbox_exec', 'Run a command in a named sandbox service; same isolated network and no host execution.', {'service': {'type': 'string'}, 'command': {'type': 'string'}, 'cwd': {'type': 'string'}, 'timeout': {'type': 'integer'}}, ['service', 'command']),
    definition('sandbox_status', 'Show private workspace, containers, logs and network.', {}, []),
    definition('sandbox_stop', 'Remove test containers/network; preserve private source edits.', {}, []),
    definition('sandbox_preview', 'Publish an HTTP preview of the app container via localhost-only relay. App must already listen on 0.0.0.0:port. No websocket support.',
        {'port': {'type': 'integer'}, 'service': {'type': 'string'}}, ['port']),
    definition('production_context', 'Read host deployment capabilities and private artifact path without running commands or contacting production.', {}, []),
    definition('production_prepare', 'Prepare immutable exact deployment for direct human approval. Requires passing project contract; does not execute. Token expires in 15 minutes and is single-use.',
        {'command': {'type': 'string'}, 'destination': {'type': 'string'}, 'rollback': {'type': 'string'},
         'cwd': {'type': 'string', 'description': 'Private project directory; relative or /workspace path. Automatically translated to host.'},
         'required_commands': {'type': 'array', 'items': {'type': 'string'}},
         'timeout': {'type': 'integer', 'description': 'Host deployment timeout in seconds, 1..3600; default 600.'}},
        ['command', 'destination', 'rollback']),
]


def image_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:@-]*', value):
        raise ValueError('Invalid image')
    return value


def env_args(values):
    if not isinstance(values, dict) or len(values) > 100:
        raise ValueError('env must contain at most 100 test-only variables')
    result = []
    for key, value in values.items():
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) or not isinstance(value, str) or '\0' in value:
            raise ValueError('Invalid environment variable')
        result += ['--env', key + '=' + value]
    return result


class SandboxFirst:
    def __init__(self, root=None):
        self.source = Path(root or Path.cwd()).resolve()
        self.identifier = uuid.uuid4().hex
        self.base = Path.home() / '.judecode' / 'workspaces' / self.identifier
        self.workspace = self.base / 'project'
        self.network = 'jude-isolated-' + self.identifier
        self.app = 'jude-app-' + self.identifier
        self.containers = []
        self.pending = None
        self.preview_url = None
        self.spec = {}

    def ensure_workspace(self):
        if self.workspace.exists():
            return
        if self.source in {Path.home(), Path(self.source.anchor)}:
            raise ValueError('Open Jude Code in a project directory, not home/root')
        self.base.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / 'source.tar'
            snapshot(self.source, archive)
            self.workspace.mkdir()
            with tarfile.open(archive) as stream:
                stream.extractall(self.workspace, filter='data')
        self.save_state()

    def save_state(self):
        self.base.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.base / 'environment.json').write_text(json.dumps({
            'source': str(self.source), 'workspace': str(self.workspace),
            'network': self.network, 'containers': self.containers, 'spec': self.spec}, indent=2))

    def mapped(self, value='.'):
        self.ensure_workspace()
        path = Path(value)
        if path.is_absolute():
            if str(path).startswith('/workspace/') or str(path) == '/workspace':
                path = self.workspace / path.relative_to('/workspace')
            elif path.is_relative_to(self.workspace):
                pass
            elif path.is_relative_to(self.source):
                path = self.workspace / path.relative_to(self.source)
            else:
                raise ValueError('Path outside sandbox project is blocked')
        else:
            path = self.workspace / path
        path = path.resolve()
        if not path.is_relative_to(self.workspace.resolve()):
            raise ValueError('Path/symlink escapes sandbox project')
        return path

    def file_args(self, name, args):
        result = dict(args)
        if name == 'grep' and str(result.get('pattern', '')).startswith('-'):
            raise ValueError('Search pattern must not be a command option')
        if name == 'glob' and ('..' in Path(str(result.get('pattern', ''))).parts or Path(str(result.get('pattern', ''))).is_absolute()):
            raise ValueError('Glob must stay inside sandbox')
        field = 'root' if name == 'glob' else 'path'
        result[field] = str(self.mapped(result.get(field, '.')))
        return result

    def start(self, image='python:3.12-slim', services=None, env=None):
        if self.containers:
            raise ValueError('Stop current environment before starting another')
        self.ensure_workspace()
        local_daemon()
        version = checked(['version', '--format', '{{.Server.Version}}'], 15)
        if int(version.split('.')[0]) < 28:
            raise ValueError('Docker Engine 28+ required for isolated gateway mode')
        image_name(image)
        services = services or []
        if not isinstance(services, list) or len(services) > 8:
            raise ValueError('services must be a list of at most 8 services')
        names = set()
        specs = []
        for service in services:
            if not isinstance(service, dict) or set(service) - {'name', 'image', 'command', 'env', 'workspace'}:
                raise ValueError('Only name/image/command/test env are allowed for services')
            name = service.get('name', '')
            if not re.fullmatch(r'[a-z][a-z0-9-]{0,30}', name) or name in names or name == 'app':
                raise ValueError('Service names must be unique DNS names, excluding app')
            names.add(name)
            if type(service.get('workspace', False)) is not bool:
                raise ValueError('workspace must be boolean')
            specs.append((name, image_name(service['image']), env_args(service.get('env', {})), service.get('command'), service.get('workspace', False)))
        app_env = env_args(env or {})
        # Only image pull uses host networking. No application command runs there.
        for candidate in {image, *(s[1] for s in specs)}:
            if docker(['image', 'inspect', candidate], 15)[0]:
                checked(['pull', candidate], 300)
        self.spec = {'image': image, 'services': services, 'env': env or {},
                     'image_ids': {candidate: checked(['image', 'inspect', '--format', '{{.Id}}', candidate], 15)
                                   for candidate in {image, *(s[1] for s in specs)}}}
        try:
            checked(['network', 'create', '--internal', '--label', 'judecode.sandbox-first=true',
                     '-o', 'com.docker.network.bridge.gateway_mode_ipv4=isolated', self.network])
            for name, candidate, variables, command, workspace in specs:
                container = 'jude-service-' + self.identifier + '-' + name
                self.containers.append(container)
                argv = self.base_args(container) + [*capability_args(True), '--network-alias', name, *variables]
                if workspace:
                    argv += ['--mount', f'type=bind,src={self.workspace},dst=/workspace', '--workdir', '/workspace']
                if workspace and command is None:
                    argv += ['--entrypoint', 'sh', candidate, '-c', 'while :; do sleep 3600; done']
                else:
                    argv += [candidate]
                if command is not None:
                    if not isinstance(command, str):
                        raise ValueError('Service command must be text')
                    argv += ['sh', '-lc', command]
                checked(argv)
            self.containers.append(self.app)
            checked(self.base_args(self.app) + ['--network-alias', 'app', '--cap-drop', 'ALL',
                '--mount', f'type=bind,src={self.workspace},dst=/workspace',
                '--workdir', '/workspace', *app_env, '--entrypoint', 'sh', image,
                '-c', 'while :; do sleep 3600; done'])
            self.save_state()
            return self.status()
        except Exception:
            self.stop()
            raise

    def base_args(self, name):
        return ['run', '-d', '--name', name, '--label', 'judecode.sandbox-first=' + self.identifier,
                '--network', self.network, '--dns', '127.0.0.1', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '--memory', '1g', '--cpus', '2', '--pids-limit', '256',
                '--log-opt', 'max-size=5m', '--log-opt', 'max-file=1']

    def files(self, name, args):
        """Do active-workspace I/O inside the container namespace (no host symlink race)."""
        field = 'root' if name == 'glob' else 'path'
        relative = Path(args[field]).relative_to(self.workspace).as_posix()
        path = '/workspace/' + relative
        quoted = shlex.quote(path)
        def invoke(command, content=None):
            result = subprocess.run(['docker', 'exec', '-i', self.app, 'sh', '-lc', command],
                                    input=content, capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise RuntimeError(result.stderr[-24000:])
            return result.stdout
        if name == 'write':
            invoke('mkdir -p ' + shlex.quote(str(Path(path).parent)) + ' && cat > ' + quoted, args['content'])
            return 'File written in sandbox: ' + path
        if name in {'read', 'edit'}:
            content = invoke('head -c 2000001 -- ' + quoted)
            if len(content.encode()) > 2000000:
                raise ValueError('File exceeds 2MB; inspect with sandbox shell')
            if name == 'read':
                offset = max(0, int(args.get('offset', 1)) - 1)
                return ''.join(content.splitlines(keepends=True)[offset:offset + int(args.get('limit') or 300)])
            if content.count(args['old_string']) != 1:
                raise ValueError('old_string must occur exactly once')
            invoke('cat > ' + quoted, content.replace(args['old_string'], args['new_string'], 1))
            return 'File edited in sandbox: ' + path
        if name == 'delete':
            invoke('rm -- ' + quoted)
            return 'File deleted in sandbox: ' + path
        if name == 'ls':
            return invoke('ls -la -- ' + quoted)
        if name == 'glob':
            return invoke('find ' + quoted + ' -type f -path ' + shlex.quote('*/' + args['pattern']) + ' | head -200')
        if name == 'grep':
            return invoke('grep -Rn -- ' + shlex.quote(args['pattern']) + ' ' + quoted + ' | head -200')
        raise ValueError('Unsupported file tool')

    def target(self, service='app'):
        names = {'app': self.app}
        names.update({s['name']: 'jude-service-' + self.identifier + '-' + s['name'] for s in self.spec.get('services', [])})
        if service not in names or names[service] not in self.containers:
            raise ValueError('Select app or a declared running sandbox service')
        return names[service]

    def run(self, command, cwd=None, timeout=120, service='app'):
        if self.app not in self.containers:
            return {'exit_code': -1, 'stdout': '', 'stderr': 'Start sandbox_environment first; host execution is blocked'}
        if type(timeout) is not int or not 1 <= timeout <= 600:
            raise ValueError('timeout must be 1..600 seconds')
        target = self.target(service)
        spec = next((s for s in self.spec.get('services', []) if s['name'] == service), None)
        if spec is not None and not spec.get('workspace', False):
            if cwd not in (None, '.', '/'):
                raise ValueError('This service has no source mount; use its default container directory')
            container_cwd = '/'
        else:
            relative = self.mapped(cwd or '.').relative_to(self.workspace).as_posix()
            container_cwd = '/workspace/' + relative
        try:
            code, output = docker(['exec', '--workdir', container_cwd, target,
                                  'sh', '-lc', command], timeout)
            return {'exit_code': code, 'stdout': output, 'stderr': ''}
        except subprocess.TimeoutExpired:
            # docker exec client timeout alone leaves the command alive. Stop environment.
            self.stop()
            return {'exit_code': 124, 'stdout': '', 'stderr': 'Timed out; sandbox containers stopped to terminate descendants'}

    def status(self):
        states = []
        for name in self.containers:
            code, output = docker(['inspect', '--format', '{{.State.Status}}', name], 15)
            _, logs = docker(['logs', '--tail', '15', name], 15)
            states.append({'name': name, 'state': output.strip() if code == 0 else 'missing', 'logs': logs})
        return {'mode': 'sandbox-first', 'source': str(self.source), 'workspace': str(self.workspace),
                'network': self.network, 'containers': states, 'preview_url': self.preview_url,
                'image_ids': self.spec.get('image_ids', {}),
                'production': 'locked: requires a prepared plan and direct human confirmation (deploy แผนนี้)' }

    def stop(self):
        errors = []
        remaining = []
        for name in reversed(self.containers):
            code, output = docker(['rm', '-f', '-v', name], 30)
            if code and 'No such container' not in output:
                errors.append(output); remaining.append(name)
        self.containers = remaining
        code, output = docker(['network', 'rm', self.network], 30)
        if code and 'not found' not in output and 'No such network' not in output:
            errors.append(output)
        self.preview_url = None
        self.save_state()
        if errors:
            raise RuntimeError('Cleanup incomplete: ' + '\n'.join(errors))
        return {'stopped': True, 'workspace_preserved': str(self.workspace)}

    def fingerprint(self):
        self.ensure_workspace()
        digest = hashlib.sha256()
        for path in sorted(self.workspace.rglob('*')):
            if path.is_symlink():
                target = path.resolve()
                if not target.is_relative_to(self.workspace.resolve()) or not target.exists():
                    raise ValueError('Deployment symlink must resolve inside the private workspace')
                relative = str(path.relative_to(self.workspace)).encode()
                link = os.readlink(path).encode()
                digest.update(b'L' + len(relative).to_bytes(8, 'big') + relative + len(link).to_bytes(8, 'big') + link)
                continue
            relative = str(path.relative_to(self.workspace)).encode()
            digest.update(len(relative).to_bytes(8, 'big') + relative)
            digest.update((path.stat().st_mode & 0o777).to_bytes(4, 'big'))
            if path.is_file():
                content_hash = hashlib.sha256()
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        content_hash.update(chunk)
                digest.update(b'F' + content_hash.digest())
            elif path.is_dir():
                digest.update(b'D')
            else:
                raise ValueError('Special files are not deployable artifacts')
        return digest.hexdigest()

    def deployment_context(self):
        self.ensure_workspace()
        commands = ('sh', 'bash', 'ssh', 'scp', 'rsync', 'curl', 'git', 'node', 'npm', 'docker')
        return {'runner': 'host', 'workspace': str(self.workspace),
                'commands': {name: shutil.which(name) for name in commands},
                'git_checkout': False, 'production_contacted': False,
                'guidance': 'Build/test in sandbox; upload tested artifacts using a release script. '
                            'Do not reuse scripts requiring .git, git fetch or host npm builds.'}

    @staticmethod
    def is_approval(message):
        return message.strip() in {'deploy แผนนี้', 'ยืนยัน deploy', 'deploy this plan'} or message.strip().startswith('DEPLOY ')

    def prepare(self, command, destination, rollback, verified, cwd='.', required_commands=None, timeout=600):
        if not verified:
            raise ValueError('All sandbox project acceptance checks must pass first')
        if any(not isinstance(x, str) or not x.strip() for x in (command, destination, rollback)):
            raise ValueError('Exact command, destination and rollback are required')
        self.pending = None
        if type(timeout) is not int or not 1 <= timeout <= 3600:
            raise ValueError('Deployment timeout must be 1..3600 seconds')
        directory = self.mapped(cwd)
        # Normalize only a simple leading cd; never rewrite arbitrary shell syntax.
        leading = re.match(r"^\s*cd\s+(/workspace(?:/[\w./-]+)?|'/workspace[^']*'|\"/workspace[^\"]*\")\s*&&\s*(.+)$", command, re.S)
        if leading:
            directory = self.mapped(shlex.split(leading[1])[0])
            command = leading[2]
        if '/workspace' in command:
            raise ValueError('Host has no /workspace. Use cwd and relative artifact paths in the release command/script.')
        if not directory.is_dir():
            raise ValueError('Deployment cwd must be an existing private project directory')
        required_commands = [] if required_commands is None else required_commands
        if not isinstance(required_commands, list) or any(not isinstance(x, str) or not re.fullmatch(r'[A-Za-z0-9_.+-]+', x) for x in required_commands):
            raise ValueError('required_commands must contain executable names only')
        missing = [name for name in required_commands if not shutil.which(name)]
        if missing:
            raise ValueError('Missing host deployment tools: ' + ', '.join(missing))
        # Detect the common legacy-script mismatch without executing project code on host.
        words = shlex.split(command)
        script_name = words[1] if len(words) > 1 and words[0] in {'sh', 'bash'} else (words[0] if words else '')
        if script_name.endswith('.sh'):
            script = (directory / script_name).resolve()
            if not script.is_relative_to(self.workspace.resolve()) or not script.is_file():
                raise ValueError('Release script must exist in the private workspace')
            content = script.read_text()
            if re.search(r'\bgit\s+(fetch|status|rev-parse|pull|push|commit)\b', content):
                raise ValueError('This source deployment script requires Git, but the private artifact has no .git. '
                                 'Create an artifact release script: upload sandbox-built files, backup/activate on target, '
                                 'then check live health. Do not fabricate Git history or deploy the unchanged original source.')
            if '/workspace/' in content:
                raise ValueError('Release script contains container-only /workspace paths; use paths relative to the script')
        # Stop all background writers before freezing the release candidate.
        if self.containers:
            self.stop()
        token = uuid.uuid4().hex[:16]
        self.pending = {'token': token, 'command': command, 'destination': destination,
                        'rollback': rollback, 'sha256': self.fingerprint(), 'expires': time.time() + 900,
                        'cwd': str(directory), 'timeout': timeout, 'required_commands': required_commands}
        return {**self.pending, 'approval': 'DEPLOY ' + token,
                'confirmation': 'deploy แผนนี้',
                'instruction': 'Show complete command/cwd/destination/rollback. Human may type deploy แผนนี้ or the token. Nothing has been deployed.'}

    def release(self, message, trusted_user, verified):
        plan, self.pending = self.pending, None  # Consume before any possible execution.
        if (not trusted_user or not plan or message.strip() not in {'DEPLOY ' + plan['token'], 'deploy แผนนี้', 'ยืนยัน deploy', 'deploy this plan'} or
                time.time() > plan['expires'] or not verified or self.fingerprint() != plan['sha256']):
            raise ValueError('Deployment blocked: require fresh passing checks and direct human approval of this exact plan')
        try:
            result = subprocess.run(plan['command'], shell=True, cwd=plan['cwd'], capture_output=True,
                                    text=True, timeout=plan.get('timeout', 600))
        except subprocess.TimeoutExpired:
            return {'destination': plan['destination'], 'outcome': 'unknown',
                    'error': 'Approved deployment timed out. Do not retry automatically; inspect destination before preparing another plan.',
                    'mode': 'sandbox-first', 'approval_consumed': True}
        return {'destination': plan['destination'], 'exit_code': result.returncode,
                'stdout': result.stdout[-24000:], 'stderr': result.stderr[-24000:], 'mode': 'sandbox-first',
                'approval_consumed': True,
                'next_step': 'Check the command output and live health evidence.' if result.returncode == 0 else
                    'Deployment failed. Repair the release script in sandbox and prepare a new plan; do not retry or bypass isolation.'}

    def python_packages(self, packages, service='app'):
        if self.app not in self.containers:
            raise ValueError('Start sandbox_environment first')
        if (not isinstance(packages, list) or not 1 <= len(packages) <= 80 or
                any(not isinstance(p, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_,.-]+\])?(?:[=<>!~]{1,2}[A-Za-z0-9.*+-]+(?:,[=<>!~]{1,2}[A-Za-z0-9.*+-]+)*)?', p) for p in packages)):
            raise ValueError('Only package names and version constraints are accepted')
        target = self.target(service)
        version = checked(['exec', target, 'python', '-c',
                           'import sys; print(str(sys.version_info.major) + "." + str(sys.version_info.minor))'], 15).strip()
        if version not in {'3.10', '3.11', '3.12', '3.13'}:
            raise ValueError('Binary wheel helper supports Python 3.10–3.13')
        helper_image = 'python:' + version + '-slim'
        cache = self.base / ('wheels-' + uuid.uuid4().hex)
        cache.mkdir()
        helper = 'jude-packages-' + self.identifier
        try:
            checked(['run', '--name', helper, '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                     '--memory', '1g', '--pids-limit', '128', '--network', 'bridge',
                     '--mount', f'type=bind,src={cache},dst=/wheels', '--entrypoint', 'python',
                     helper_image, '-m', 'pip', '--isolated', 'download', '--only-binary=:all:',
                     '--index-url', 'https://pypi.org/simple', '--dest', '/wheels', *packages], 300)
            # Copy wheels into the container; never let the downloader see source or credentials.
            checked(['cp', str(cache) + '/.', target + ':/tmp/'])
            result = self.run('python -m pip install --no-index --find-links=/tmp ' + shlex.join(packages), service=service)
            if result['exit_code']:
                raise RuntimeError(result['stdout'] + result['stderr'])
            return result
        finally:
            docker(['rm', '-f', '-v', helper], 30)


    def node_packages(self, packages=None, service='app', directory='.', legacy_peer_deps=False):
        target = self.target(service)
        destination = '/workspace/' + self.mapped(directory).relative_to(self.workspace).as_posix()
        pattern = r'(?:@[A-Za-z0-9_.-]+/)?[A-Za-z0-9][A-Za-z0-9_.-]*(?:@[A-Za-z0-9.*^~<>=|+ -]+)?'
        if packages is not None and (not isinstance(packages, list) or not 1 <= len(packages) <= 100 or
                any(not isinstance(p, str) or not re.fullmatch(pattern, p) for p in packages)):
            raise ValueError('Use npm package names/version ranges only, never URLs or paths')
        if type(legacy_peer_deps) is not bool:
            raise ValueError('legacy_peer_deps must be boolean')
        manifest = {'name': 'judecode-sandbox-deps', 'version': '1.0.0', 'private': True}
        lock = None
        if packages is None:
            project_dir = self.mapped(directory)
            original = json.loads(self.mapped(str(project_dir / 'package.json')).read_text())
            lock = json.loads(self.mapped(str(project_dir / 'package-lock.json')).read_text())
            if lock.get('lockfileVersion') not in {2, 3} or not isinstance(lock.get('packages'), dict):
                raise ValueError('Use npm package-lock v2/v3 for reproducible installation')
            for field in ('dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies'):
                values = original.get(field, {})
                if not isinstance(values, dict) or any(not isinstance(v, str) or not re.fullmatch(pattern, k + '@' + v) for k, v in values.items()):
                    raise ValueError('Manifest dependencies must be registry package names/version ranges')
                manifest[field] = values
            for item in lock['packages'].values():
                resolved = item.get('resolved', '')
                if item.get('link') or (resolved and not resolved.startswith('https://registry.npmjs.org/')):
                    raise ValueError('Lockfile may contain only npm registry artifacts, no URLs/links outside registry')
            # Strip scripts, config, npmrc and all source. Preserve the dependency graph.
            manifest['name'] = original.get('name', manifest['name'])
            manifest['version'] = original.get('version', manifest['version'])
        cache = self.base / ('node-' + uuid.uuid4().hex)
        cache.mkdir()
        (cache / 'package.json').write_text(json.dumps(manifest))
        if lock is not None:
            (cache / 'package-lock.json').write_text(json.dumps(lock))
        helper = 'jude-node-packages-' + self.identifier
        try:
            checked(['run', '--name', helper, '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                     '--memory', '1g', '--pids-limit', '128', '--network', 'bridge',
                     '--mount', f'type=bind,src={cache},dst=/packages', '--workdir', '/packages',
                     '--entrypoint', 'npm', 'node:20-alpine', 'ci' if packages is None else 'install', '--ignore-scripts', '--no-audit',
                     '--no-fund', '--registry=https://registry.npmjs.org',
                     *(['--legacy-peer-deps'] if legacy_peer_deps else []), *(packages or [])], 600)
            # Only copy to container paths. Source package manifests remain authored in sandbox.
            result = self.run('mkdir -p ' + shlex.quote(destination), service=service)
            if result['exit_code']:
                raise RuntimeError(result['stdout'] + result['stderr'])
            checked(['cp', str(cache / 'node_modules'), target + ':' + destination + '/'])
            return {'installed': packages if packages is not None else 'project lockfile', 'service': service, 'directory': destination,
                    'scripts': 'disabled; run necessary build/rebuild commands offline in sandbox'}
        finally:
            docker(['rm', '-f', '-v', helper], 30)

    def preview(self, port, service='app'):
        if type(port) is not int or not 1 <= port <= 65535 or self.app not in self.containers:
            raise ValueError('Start environment and provide a valid app port')
        self.target(service)
        name = 'jude-relay-' + self.identifier
        if name in self.containers:
            raise ValueError('Preview already active; use sandbox_status')
        script = self.base / 'relay.py'
        script.write_text(RELAY)
        image = 'python:3.12-slim'
        if docker(['image', 'inspect', image], 15)[0]:
            checked(['pull', image], 300)
        self.containers.append(name)
        try:
            checked(['create', '--name', name, '--label', 'judecode.sandbox-first=' + self.identifier,
                     '--network', 'bridge', '--publish', '127.0.0.1::8080', '--read-only',
                     '--sysctl', 'net.ipv4.ip_forward=0',
                     '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--user', '65534',
                     '--memory', '128m', '--pids-limit', '64',
                     '--log-opt', 'max-size=5m', '--log-opt', 'max-file=1',
                     '--mount', f'type=bind,src={script},dst=/relay.py,readonly',
                     '--entrypoint', 'python', image, '/relay.py', str(port), service])
            checked(['network', 'connect', self.network, name])
            checked(['start', name])
            binding = checked(['port', name, '8080/tcp'], 15).strip()
            if not binding.startswith('127.0.0.1:'):
                raise RuntimeError('Preview must bind localhost')
            self.preview_url = 'http://' + binding + '/'
            self.save_state()
            return {'url': self.preview_url, 'note': 'HTTP relay ready; verify application readiness separately'}
        except Exception:
            docker(['rm', '-f', name], 30)
            self.containers.remove(name)
            raise

    def restore(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{32}', identifier):
            raise ValueError('Invalid sandbox workspace ID')
        base = Path.home() / '.judecode' / 'workspaces' / identifier
        data = json.loads((base / 'environment.json').read_text())
        if Path(data['source']).resolve() != self.source:
            raise ValueError('Sandbox belongs to another source project')
        self.identifier = identifier
        self.base = base
        self.workspace = base / 'project'
        self.network = 'jude-isolated-' + identifier
        self.app = 'jude-app-' + identifier
        if any(not re.fullmatch(r'jude-(?:app|service|relay)-' + identifier + r'(?:-[a-z][a-z0-9-]{0,30})?', n)
               for n in data['containers']):
            raise ValueError('Invalid saved sandbox container name')
        self.containers = data['containers']
        self.spec = data.get('spec', {})
        self.pending = None  # Never restore deployment authorization.


RELAY = r'''
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import sys
PORT = int(sys.argv[1])
UPSTREAM = sys.argv[2]
HOP = {'connection','transfer-encoding','keep-alive','upgrade','proxy-authorization','proxy-authenticate'}
class Relay(BaseHTTPRequestHandler):
    def do_request(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 <= length <= 2097152 or not self.path.startswith('/') or self.path.startswith('//'):
                self.send_error(400); return
            body = self.rfile.read(length)
            # Fixed destination. No CONNECT, URL proxy, redirects, host selection or DNS input.
            conn = http.client.HTTPConnection(UPSTREAM, PORT, timeout=30)
            headers = {k:v for k,v in self.headers.items() if k.lower() not in HOP}
            headers['Host'] = self.headers.get('Host', 'localhost')
            conn.request(self.command, self.path, body, headers)
            response = conn.getresponse()
            location = response.getheader('Location', '')
            if location and (not location.startswith('/') or location.startswith('//')):
                conn.close(); self.send_error(502, 'External redirects blocked in sandbox preview'); return
            self.send_response(response.status)
            for k,v in response.getheaders():
                if k.lower() not in HOP | {'refresh'}: self.send_header(k,v)
            self.send_header('Content-Security-Policy', "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval'; connect-src 'self'; form-action 'self'; object-src 'none'; base-uri 'self'; sandbox allow-scripts allow-same-origin allow-forms")
            self.send_header('Connection','close'); self.end_headers()
            if self.command != 'HEAD':
                while True:
                    chunk=response.read(65536)
                    if not chunk: break
                    self.wfile.write(chunk)
            conn.close()
        except Exception:
            self.send_error(502, 'Sandbox app is not ready')
    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = do_request
ThreadingHTTPServer(('0.0.0.0',8080), Relay).serve_forever()
'''
