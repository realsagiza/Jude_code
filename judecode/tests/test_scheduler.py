"""Durable scheduling invariants; no live model, no real user schedules."""
import asyncio
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from judecode.agent.scheduler import ScheduleStore, next_daily, parse_time


@pytest.fixture
def store(tmp_path):
    return ScheduleStore(tmp_path / 'scheduler')


def create(store, **kwargs):
    return store.create('test', 'Inspect project', interval_minutes=1, now=100, **kwargs)


def test_persistence_and_overdue_coalescing(store):
    job = create(store)
    reopened = ScheduleStore(store.root)
    assert reopened.claim(now=159) is None
    run = reopened.claim(now=10000)
    assert run['id'] == job['id']
    assert reopened.claim(now=10000) is None
    reopened.finish(run['run_id'], {'status': 'answered'}, now=10001)
    assert reopened.get(job['id'])['next_at'] == 10061
    assert len(reopened.history(job['id'])) == 1


def test_concurrent_claim_is_exactly_once(store):
    create(store)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: ScheduleStore(store.root).claim(now=200), range(6)))
    assert sum(x is not None for x in results) == 1


def test_self_planning_checkpoint_persists_only_after_success(store):
    job = create(store)
    run = store.claim(now=200)
    store.checkpoint(run['run_id'], 'continue', 'Step 1 done; step 2 next',
                     '2030-01-01T09:00:00+07:00', now=200)
    assert store.get(job['id'])['state'] == 'running'
    store.finish(run['run_id'], {'status': 'answered'}, now=201)
    saved = store.get(job['id'])
    assert saved['next_local'] == '2030-01-01T09:00:00+07:00'
    assert saved['progress'] == 'Step 1 done; step 2 next'


@pytest.mark.parametrize('outcome,expected', [('complete', 'completed'), ('blocked', 'needs_attention')])
def test_checkpoint_terminal_states(store, outcome, expected):
    job = create(store)
    run = store.claim(now=200)
    store.checkpoint(run['run_id'], outcome, 'Evidence', now=200)
    store.finish(run['run_id'], {'status': 'answered'}, now=201)
    assert store.get(job['id'])['state'] == expected
    assert store.get(job['id'])['next_at'] is None


@pytest.mark.parametrize('status', ['failed', 'unverified', 'timeout', 'interrupted', 'blocked'])
def test_failed_worker_never_replays_even_with_checkpoint(store, status):
    job = create(store)
    run = store.claim(now=200)
    store.checkpoint(run['run_id'], 'continue', 'Saved before failure',
                     '2030-01-01T09:00:00+07:00', now=200)
    store.finish(run['run_id'], {'status': status}, now=201)
    assert store.get(job['id'])['state'] == 'needs_attention'
    assert store.claim(now=9999999999) is None


def test_recovery_marks_unknown_not_retry(store):
    job = create(store)
    store.claim(now=200)
    store.recover()
    assert store.get(job['id'])['state'] == 'needs_attention'
    assert store.history()[0]['status'] == 'interrupted'
    assert store.claim(now=10000) is None


@pytest.mark.parametrize('action,state', [('pause', 'paused'), ('cancel', 'cancelled')])
def test_inflight_control_is_not_overwritten_by_worker(store, action, state):
    job = create(store)
    run = store.claim(now=200)
    store.manage(job['id'], action)
    with pytest.raises(ValueError, match='current worker'):
        store.manage(job['id'], 'resume')
    with pytest.raises(ValueError, match='active'):
        store.checkpoint(run['run_id'], 'complete', 'done')
    store.finish(run['run_id'], {'status': 'answered'}, now=201)
    assert store.get(job['id'])['state'] == state
    store.manage(job['id'], 'resume')
    assert store.get(job['id'])['state'] == 'scheduled'


def test_wake_limit_and_finish_idempotency(store):
    job = create(store, max_runs=1)
    run = store.claim(now=200)
    store.finish(run['run_id'], {'status': 'answered'}, now=201)
    store.finish(run['run_id'], {'status': 'failed'}, now=202)
    assert store.get(job['id'])['state'] == 'limit_reached'
    assert store.history()[0]['status'] == 'answered'
    with pytest.raises(ValueError, match='limit'):
        store.manage(job['id'], 'resume')


@pytest.mark.parametrize('kwargs', [dict(budget=float('nan')), dict(budget=0), dict(max_runs=0),
                                     dict(max_runs=1.5), dict(timeout_minutes=0), dict(timezone='Nonsense'),
                                     dict(mode='unrestricted'), dict(daily='25:99')])
def test_invalid_configuration_rejected(store, kwargs):
    with pytest.raises((ValueError, KeyError)):
        create(store, **kwargs)
    assert store.list() == []


def test_naive_and_past_times_rejected(store):
    for value in ('2030-01-01T09:00:00', '1970-01-01T00:00:01+00:00'):
        with pytest.raises(ValueError):
            store.create('test', 'goal', run_at=value, now=100)


def test_timezone_and_dst():
    now = parse_time('2030-01-01T08:59:00+07:00')
    assert next_daily('09:00', 'Asia/Bangkok', now) == now + 60
    assert next_daily('09:00', 'Asia/Bangkok', now + 60) == now + 60 + 86400
    spring = parse_time('2026-03-08T00:00:00-05:00')
    assert next_daily('02:30', 'America/New_York', spring) == parse_time('2026-03-08T03:30:00-04:00')


def test_checkpoint_validation_and_one_shot_review(store):
    store.create('once', 'goal', run_at='2030-01-01T00:00:00+00:00', now=100)
    run = store.claim(now=2000000000)
    with pytest.raises(ValueError):
        store.checkpoint(run['run_id'], 'continue', 'waiting', now=100)
    store.finish(run['run_id'], {'status': 'answered'})
    assert store.list()[0]['state'] == 'needs_attention'


def test_tools_registered_and_worker_cannot_expand_scope(tmp_path, monkeypatch):
    from judecode.agent.tools import TOOL_DEFINITIONS, execute_tool
    from judecode.agent.schedule_tools import SCHEDULE_NAMES
    monkeypatch.setenv('HOME', str(tmp_path))
    assert SCHEDULE_NAMES <= {t['function']['name'] for t in TOOL_DEFINITIONS}
    assert 'only available' in execute_tool('schedule_checkpoint', {'outcome': 'complete', 'progress': 'done'})
    monkeypatch.setenv('JUDECODE_SCHEDULE_RUN_ID', 'worker')
    assert 'only plan its own' in execute_tool('schedule_create', {'name': 'bad', 'goal': 'bad'})
    assert 'only plan its own' in execute_tool('schedule_manage', {'job_id': 'other', 'action': 'resume'})


class FakeAPI:
    def __init__(self, tool):
        self.tool = tool
        self.calls = 0
        self.closed = False

    async def chat_completion(self, messages, tools=None, stream=True):
        self.calls += 1
        if self.calls == 1:
            yield {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'call1', 'type': 'function',
                   'function': {'name': self.tool, 'arguments': json.dumps(
                       {'outcome': 'complete', 'progress': 'Inspected successfully'} if self.tool == 'schedule_checkpoint'
                       else {'command': 'touch SHOULD_NOT_EXIST'})}}]}, 'finish_reason': 'tool_calls'}]}
        else:
            yield {'choices': [{'delta': {'content': 'Finished.'}, 'finish_reason': 'stop'}]}

    def _extract_reasoning(self, chunk):
        return ''

    async def close(self):
        self.closed = True


@pytest.mark.parametrize('tool,expected', [('schedule_checkpoint', 'answered'), ('shell', 'failed')])
def test_real_engine_headless_flow(tmp_path, monkeypatch, tool, expected):
    from judecode.agent.schedule_service import run_agent
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.chdir(tmp_path)
    store = ScheduleStore()
    create(store, project=str(tmp_path))
    run = store.claim(now=200)
    monkeypatch.setenv('JUDECODE_SCHEDULE_RUN_ID', run['run_id'])
    fake = FakeAPI(tool)
    result = asyncio.run(run_agent(run, api=fake))
    assert result['status'] == expected
    assert fake.closed
    assert not (tmp_path / 'SHOULD_NOT_EXIST').exists()
    store.finish(run['run_id'], result)
    assert store.get(run['id'])['state'] == ('completed' if tool == 'schedule_checkpoint' else 'needs_attention')


def test_cli_and_idle_service_no_api(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path))
    result = subprocess.run([sys.executable, '-m', 'judecode', 'schedule', 'status'], env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['jobs'] == []
    result = subprocess.run([sys.executable, '-m', 'judecode.agent.schedule_service', 'serve', '--once'],
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_budget_limit_stops_next_model_request(tmp_path, monkeypatch):
    from judecode.agent.schedule_service import run_agent
    from judecode.agent.autonomous import BudgetTracker
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.chdir(tmp_path)
    original = BudgetTracker.__init__

    def exhausted(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.total_cost = 10

    monkeypatch.setattr(BudgetTracker, '__init__', exhausted)
    fake = FakeAPI('shell')
    store = ScheduleStore()
    create(store)
    run = store.claim(now=200)
    result = asyncio.run(run_agent(run, api=fake))
    assert result['status'] == 'blocked'
    assert fake.calls == 0


def test_ui_controls_and_help(tmp_path, monkeypatch):
    from judecode.ui.schedule_commands import is_schedule_command, render_schedule_command
    from judecode.ui.command_help import render_help
    from rich.text import Text
    monkeypatch.setenv('HOME', str(tmp_path))
    assert is_schedule_command('/schedule list')
    assert not is_schedule_command('/scheduler')
    assert 'jobs' in render_schedule_command('/schedule')
    assert '/schedule' in render_help('tui')
    assert 'ใช้ /schedule' in render_schedule_command('/schedule install')
    assert json.loads(Text.from_markup(render_schedule_command('/schedule list')).plain) == []


def test_service_finishes_worker_result(tmp_path, monkeypatch):
    from judecode.agent import schedule_service as svc
    monkeypatch.setenv('HOME', str(tmp_path))
    store = ScheduleStore()
    job = create(store)

    class Process:
        returncode = 0
        pid = 987654321

        def __init__(self, cmd, **kwargs):
            run_id = cmd[-1]
            store.checkpoint(run_id, 'complete', 'Result verified')
            (store.root / (run_id + '.result.json')).write_text(json.dumps({'status': 'answered'}))
            assert kwargs['cwd'] == job['project']
            assert kwargs['pass_fds']

        def poll(self):
            return self.returncode

    monkeypatch.setattr(svc.subprocess, 'Popen', Process)
    monkeypatch.setattr(svc, 'stop_worker', lambda proc: None)
    svc.serve(once=True)
    assert store.get(job['id'])['state'] == 'completed'


def test_service_worker_launch_failure_requires_attention(tmp_path, monkeypatch):
    from judecode.agent import schedule_service as svc
    monkeypatch.setenv('HOME', str(tmp_path))
    store = ScheduleStore()
    job = create(store)

    def fail(*args, **kwargs):
        raise OSError('no worker')

    monkeypatch.setattr(svc.subprocess, 'Popen', fail)
    svc.serve(once=True)
    assert store.get(job['id'])['state'] == 'needs_attention'
    assert 'no worker' in store.history()[0]['result']


def test_service_timeout_kills_worker(tmp_path, monkeypatch):
    from judecode.agent import schedule_service as svc
    monkeypatch.setenv('HOME', str(tmp_path))
    store = ScheduleStore()
    job = create(store, timeout_minutes=1)
    killed = []

    class Process:
        def __init__(self, *args, **kwargs):
            pass

        def poll(self):
            return None

    ticks = iter([0, 61])
    monkeypatch.setattr(svc.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(svc.subprocess, 'Popen', Process)
    monkeypatch.setattr(svc, 'stop_worker', lambda proc: killed.append(True))
    svc.serve(once=True)
    assert killed == [True]
    assert store.get(job['id'])['state'] == 'needs_attention'
    assert store.history()[0]['status'] == 'timeout'


def test_second_service_cannot_recover_active_work(tmp_path, monkeypatch):
    import fcntl
    from judecode.agent import schedule_service as svc
    monkeypatch.setenv('HOME', str(tmp_path))
    store = ScheduleStore()
    job = create(store)
    store.claim(now=200)
    with (store.root / 'service.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SystemExit, match='already running'):
            svc.serve(once=True)
    assert store.get(job['id'])['state'] == 'running'
