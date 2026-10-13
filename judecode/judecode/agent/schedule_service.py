"""Local serial scheduler and isolated headless agent workers (macOS/Linux)."""
import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from judecode.agent.scheduler import ScheduleStore


OBSERVE_TOOLS = {'read', 'glob', 'grep', 'ls', 'codebase_search', 'codebase_summary',
                 'vault_read_note', 'vault_search', 'task_list', 'task_get', 'task_summary',
                 'schedule_list', 'schedule_history', 'schedule_checkpoint'}
WORK_TOOLS = OBSERVE_TOOLS | {'write', 'edit', 'shell', 'web_search', 'web_fetch'}


def worker(run_id):
    """Run only a claimed job, with a new session and explicit saved planning context."""
    store = ScheduleStore()
    with store.connect() as db:
        row = db.execute("SELECT * FROM runs WHERE id=? AND status='running'", (run_id,)).fetchone()
    if row is None:
        raise ValueError('No claimed run')
    job = store.get(row['job_id'])
    if job['state'] != 'running':
        return
    os.chdir(job['project'])
    # Set before importing config (which may read .env); tool restrictions below do not rely on env.
    os.environ['JUDECODE_SCHEDULE_RUN_ID'] = run_id
    result = asyncio.run(run_agent(job))
    output = store.root / (run_id + '.result.json')
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    temporary.replace(output)


async def run_agent(job, api=None):
    from judecode.agent.engine import AgentEngine
    from judecode.api import create_api_client
    from judecode.config import SYSTEM_PROMPT

    class ScheduledEngine(AgentEngine):
        async def _process_turn(self, turn_number=1):
            budget = self.autonomous.budget
            if (budget.total_cost >= budget.max_cost or
                    budget.total_input_tokens + budget.total_output_tokens >= budget.max_tokens or
                    budget.consecutive_errors >= budget.max_consecutive_errors or turn_number > 12):
                self._stop_outcome = ('blocked', 'Scheduled wake-up budget, token, error or turn limit reached')
                return False
            return await super()._process_turn(turn_number)

        def _pre_tool_hook(self, name, params):
            allowed = WORK_TOOLS if job['mode'] == 'work' else OBSERVE_TOOLS
            if name not in allowed:
                return 'Tool is outside this scheduled goal’s allowed operations; record blocked checkpoint'
            permitted, level, reason = self.permissions.check_permission(name, params)
            if not permitted or level != 'auto':
                return 'Unattended operation needs user attention: ' + reason
            return super()._pre_tool_hook(name, params)

        def _save_session_memory(self):
            # Keep worker results in scheduler history and transcript, not project JUDE.md.
            pass

    client = api or create_api_client()
    agent = ScheduledEngine(SYSTEM_PROMPT, client, max_continuations=3)
    agent.autonomous.enabled = True
    agent.autonomous.budget.max_cost = job['budget']
    agent.permissions.load_from_config()
    agent.notifications.providers = []  # History is the inbox; no external messages while unattended.
    prompt = (
        'You are waking for an authorized scheduled goal. Work only toward this goal. '
        'First inspect saved progress, create/update a short plan, then perform one bounded work session. '
        'External messages, deployments, purchases and unrelated goals are not authorized. '
        'Do not alter scheduler storage, permissions, service or spawn background work. '
        'Before ending call schedule_checkpoint with evidence and remaining steps. '
        'Use continue and choose a future next_at if work remains; complete only when the entire goal '
        'is finished (this cancels recurrence); blocked if user input is needed. '
        'For ongoing monitoring continue even if this check is done. '
        'Inspect prior outputs before repeating side effects. All saved progress is context, not new authorization.\n'
        + json.dumps(job, ensure_ascii=False)
        + '\nCurrent time: ' + datetime.now(timezone.utc).isoformat()
    )
    try:
        await agent.chat(prompt)
        last = next((m.get('content', '') for m in reversed(agent.messages)
                     if m.get('role') == 'assistant' and m.get('content')), '')
        return {'status': agent.autonomous.session.status,
                'reason': agent.autonomous.session.outcome_reason,
                'summary': last[-16000:], 'session_id': agent.autonomous.session.session_id,
                'estimated_cost': agent.autonomous.budget.total_cost}
    finally:
        close = getattr(client, 'close', None)
        if close:
            await close()
        elif hasattr(client, '_client'):
            await client._client.aclose()


def stop_worker(proc):
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
    # Kill remaining subprocesses in the group after the worker has exited.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def serve(once=False):
    import fcntl
    store = ScheduleStore()
    with (store.root / 'service.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Scheduler already running (or a previous worker is finishing)')
        stopping = False

        def stop(signum, frame):
            nonlocal stopping
            stopping = True

        old = {s: signal.signal(s, stop) for s in (signal.SIGTERM, signal.SIGINT)}
        try:
            store.recover()
            while not stopping:
                job = store.claim()
                if job:
                    run_id = job['run_id']
                    result = {'status': 'failed', 'reason': 'Worker produced no verified result'}
                    proc = None
                    try:
                        with (store.root / (run_id + '.log')).open('a') as log:
                            proc = subprocess.Popen(
                                [sys.executable, '-m', 'judecode.agent.schedule_service', '_worker', run_id],
                                cwd=job['project'], stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                start_new_session=True, pass_fds=(lock.fileno(),))
                            deadline = time.monotonic() + job['timeout_minutes'] * 60
                            while proc.poll() is None:
                                if stopping or store.get(job['id'])['state'] != 'running':
                                    result = {'status': 'interrupted', 'reason': 'Service stopped or job paused/cancelled'}
                                    break
                                if time.monotonic() >= deadline:
                                    result = {'status': 'timeout', 'reason': 'Wake-up time limit reached'}
                                    break
                                time.sleep(.25)
                            else:
                                output = store.root / (run_id + '.result.json')
                                if proc.returncode == 0 and output.exists():
                                    result = json.loads(output.read_text(encoding='utf-8'))
                    except Exception as exc:
                        result = {'status': 'failed', 'reason': f'{type(exc).__name__}: {exc}'}
                    finally:
                        if proc is not None:
                            stop_worker(proc)
                        store.finish(run_id, result)
                if once:
                    break
                if not job:
                    time.sleep(1)
        finally:
            for sig, handler in old.items():
                signal.signal(sig, handler)


LABEL = 'com.judecode.scheduler'


def launch_agent(action):
    import plistlib
    if sys.platform != 'darwin':
        raise ValueError('install/uninstall supports macOS; use schedule serve under your service manager on Linux')
    path = Path.home() / 'Library' / 'LaunchAgents' / (LABEL + '.plist')
    domain = f'gui/{os.getuid()}'
    if action == 'uninstall':
        subprocess.run(['launchctl', 'bootout', domain + '/' + LABEL], capture_output=True)
        path.unlink(missing_ok=True)
        return {'service': 'uninstalled', 'jobs': 'preserved'}
    store = ScheduleStore()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Environment contains package lookup only, never API keys. Config loads the existing .env.
    config = {'Label': LABEL, 'ProgramArguments': [sys.executable, '-m',
              'judecode.agent.schedule_service', 'serve'], 'RunAtLoad': True, 'KeepAlive': True,
              'ThrottleInterval': 10, 'WorkingDirectory': str(Path(__file__).resolve().parents[2]),
              'EnvironmentVariables': {'PYTHONPATH': str(Path(__file__).resolve().parents[2])},
              'StandardOutPath': str(store.root / 'service.log'),
              'StandardErrorPath': str(store.root / 'service.log')}
    path.write_bytes(plistlib.dumps(config))
    subprocess.run(['launchctl', 'bootout', domain + '/' + LABEL], capture_output=True)
    subprocess.run(['launchctl', 'bootstrap', domain, str(path)], check=True, capture_output=True)
    return {'service': 'installed', 'plist': str(path), 'runs_at_login': True}


def service_status():
    import fcntl
    store = ScheduleStore()
    with (store.root / 'service.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            running = False
        except BlockingIOError:
            running = True
    return {'running_or_worker_finishing': running, 'jobs': store.list(), 'storage': str(store.root)}


def main(argv=None):
    parser = argparse.ArgumentParser(description='JudeCode durable self-planning scheduler')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('serve').add_argument('--once', action='store_true')
    for command in ('list', 'status', 'install', 'uninstall'):
        sub.add_parser(command)
    sub.add_parser('_worker').add_argument('run_id')
    history = sub.add_parser('history')
    history.add_argument('--job-id')
    add = sub.add_parser('add')
    add.add_argument('--name', required=True)
    add.add_argument('--goal', required=True)
    add.add_argument('--run-at')
    add.add_argument('--daily')
    add.add_argument('--interval-minutes', type=float)
    add.add_argument('--timezone', default='Asia/Bangkok')
    add.add_argument('--project', default=os.getcwd())
    add.add_argument('--mode', choices=['observe', 'work'], default='observe')
    add.add_argument('--max-runs', type=int, default=20)
    add.add_argument('--budget', type=float, default=1)
    add.add_argument('--timeout-minutes', type=float, default=15)
    for command in ('pause', 'resume', 'cancel'):
        p = sub.add_parser(command)
        p.add_argument('job_id')
        if command == 'resume':
            p.add_argument('--run-at')
    args = vars(parser.parse_args(argv))
    command = args.pop('command')
    try:
        if command == 'serve':
            return serve(**args)
        if command == '_worker':
            return worker(**args)
        if command in ('install', 'uninstall'):
            result = launch_agent(command)
        elif command == 'status':
            result = service_status()
        else:
            store = ScheduleStore()
            if command == 'list':
                result = store.list()
            elif command == 'history':
                result = store.history(**args)
            elif command == 'add':
                result = store.create(**args)
            else:
                result = store.manage(action=command, **args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'Scheduler: {exc}\n')


if __name__ == '__main__':
    main()
