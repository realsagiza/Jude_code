"""Durable wake-ups and bounded self-planning. No model calls while idle."""
import json
import hashlib
import math
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


def parse_time(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('run_at must include a timezone, e.g. 2026-10-14T09:00:00+07:00')
    return dt.timestamp()


def next_daily(clock, zone, now):
    tz = ZoneInfo(zone)
    hour, minute = map(int, clock.split(':'))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError('daily must be HH:MM')
    local = datetime.fromtimestamp(now, tz)
    for offset in range(3):
        candidate = datetime.combine(local.date() + timedelta(days=offset),
                                     datetime.min.time(), tz).replace(hour=hour, minute=minute)
        # A nonexistent local time is moved forward by the DST gap.
        if candidate.timestamp() > now:
            return candidate.timestamp()
    raise ValueError('Cannot calculate next daily occurrence')


class ScheduleStore:
    def __init__(self, root=None):
        self.root = Path(root or Path.home() / '.judecode' / 'scheduler')
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'jobs.sqlite3'
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, data TEXT NOT NULL,
                    state TEXT NOT NULL, next_at REAL, runs INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, started REAL NOT NULL,
                    finished REAL, status TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}',
                    checkpoint TEXT);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _job(self, row):
        if row is None:
            raise ValueError('Schedule not found')
        job = json.loads(row['data'])
        job.update(id=row['id'], state=row['state'], next_at=row['next_at'], runs=row['runs'])
        job['next_local'] = (datetime.fromtimestamp(row['next_at'], ZoneInfo(job['timezone'])).isoformat()
                             if row['next_at'] is not None else None)
        return job

    def get(self, job_id):
        with self.connect() as db:
            return self._job(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())

    def list(self):
        with self.connect() as db:
            return [self._job(r) for r in db.execute('SELECT * FROM jobs ORDER BY next_at, id')]

    def create(self, name, goal, run_at=None, interval_minutes=None, daily=None,
               timezone='Asia/Bangkok', project=None, max_runs=20, budget=1.0,
               timeout_minutes=15, mode='observe', now=None, request_key=None, conversation_id=None):
        if conversation_id is not None and (not isinstance(conversation_id, str) or not 1 <= len(conversation_id) <= 100):
            raise ValueError('Invalid conversation_id')
        if request_key is not None and (not isinstance(request_key, str) or not 1 <= len(request_key) <= 200):
            raise ValueError('request_key must contain 1..200 characters')
        job_id = (hashlib.sha256(request_key.encode()).hexdigest()[:24] if request_key
                  else uuid.uuid4().hex[:12])
        if request_key:
            with self.connect() as db:
                existing = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
                if existing is not None:
                    return self._job(existing)
        now = time.time() if now is None else now
        if not name.strip() or not goal.strip() or len(goal) > 20000:
            raise ValueError('A name and goal (up to 20000 characters) are required')
        if mode not in ('observe', 'work'):
            raise ValueError('mode must be observe or work')
        ZoneInfo(timezone)
        if isinstance(max_runs, bool) or not isinstance(max_runs, int) or not 1 <= max_runs <= 1000:
            raise ValueError('max_runs must be an integer from 1 to 1000')
        for val, low, high, label in ((budget, .01, 100, 'budget'),
                                    (timeout_minutes, 1, 120, 'timeout_minutes')):
            if not math.isfinite(val) or not low <= val <= high:
                raise ValueError(f'{label} must be between {low} and {high}')
        if interval_minutes is not None and (not math.isfinite(interval_minutes) or interval_minutes < 1):
            raise ValueError('interval_minutes must be at least 1')
        if daily and interval_minutes is not None:
            raise ValueError('Choose daily or interval_minutes')
        if not any((run_at, daily, interval_minutes)):
            raise ValueError('Provide run_at, daily or interval_minutes')
        project = str(Path(project or os.getcwd()).expanduser().resolve())
        if not Path(project).is_dir():
            raise ValueError('Project directory does not exist')
        next_at = parse_time(run_at) if run_at else (next_daily(daily, timezone, now) if daily
                                                   else now + interval_minutes * 60)
        if next_at <= now:
            raise ValueError('run_at must be in the future')
        # Validate daily even when an explicit initial time was supplied.
        if daily:
            next_daily(daily, timezone, now)
        data = dict(name=name, goal=goal, project=project, timezone=timezone, daily=daily,
                    interval_minutes=interval_minutes, max_runs=max_runs, budget=budget,
                    timeout_minutes=timeout_minutes, mode=mode, progress='', created_at=now, conversation_id=conversation_id)
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO jobs(id,data,state,next_at) VALUES(?,?,?,?)',
                       (job_id, json.dumps(data, ensure_ascii=False), 'scheduled', next_at))
        return self.get(job_id)

    def manage(self, job_id, action, run_at=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            job = self._job(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())
            if action not in ('pause', 'resume', 'cancel'):
                raise ValueError('action must be pause, resume or cancel')
            if action == 'resume':
                if job['state'] == 'running' or db.execute(
                    "SELECT 1 FROM runs WHERE job_id=? AND status='running'", (job_id,)).fetchone():
                    raise ValueError('Wait for the current worker to stop before resuming')
                if job['runs'] >= job['max_runs']:
                    raise ValueError('Wake-up limit reached; create a new authorized schedule')
                next_at = parse_time(run_at) if run_at else time.time() + 60
                if next_at <= time.time():
                    raise ValueError('run_at must be in the future')
                state = 'scheduled'
            else:
                next_at, state = None, {'pause': 'paused', 'cancel': 'cancelled'}[action]
            db.execute('UPDATE jobs SET state=?, next_at=? WHERE id=?', (state, next_at, job_id))
        return self.get(job_id)

    def claim(self, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM jobs WHERE state='scheduled' AND next_at<=? "
                             'ORDER BY next_at LIMIT 1', (now,)).fetchone()
            if row is None:
                return None
            job = self._job(row)
            run_id = uuid.uuid4().hex
            db.execute("UPDATE jobs SET state='running', runs=runs+1 WHERE id=?", (job['id'],))
            db.execute('INSERT INTO runs(id,job_id,started,status) VALUES(?,?,?,?)',
                       (run_id, job['id'], now, 'running'))
            job.update(run_id=run_id, runs=job['runs'] + 1)
            return job

    def checkpoint(self, run_id, outcome, progress, next_at=None, now=None):
        now = time.time() if now is None else now
        if outcome not in ('continue', 'complete', 'blocked'):
            raise ValueError('outcome must be continue, complete or blocked')
        if not progress.strip() or len(progress) > 12000:
            raise ValueError('progress must contain 1..12000 characters')
        wake = parse_time(next_at) if next_at else None
        if outcome == 'continue' and (wake is None or wake < now + 60):
            raise ValueError('continue requires next_at at least 60 seconds in the future')
        if outcome != 'continue' and wake is not None:
            raise ValueError('next_at is only valid with continue')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT r.* FROM runs r JOIN jobs j ON j.id=r.job_id "
                             "WHERE r.id=? AND r.status='running' AND j.state='running'", (run_id,)).fetchone()
            if row is None:
                raise ValueError('No active scheduled run')
            db.execute('UPDATE runs SET checkpoint=? WHERE id=?',
                       (json.dumps(dict(outcome=outcome, progress=progress, next_at=wake)), run_id))
        return {'saved': True, 'outcome': outcome, 'next_at': wake}

    def finish(self, run_id, result, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            run = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
            if run is None or run['status'] != 'running':
                return
            job = self._job(db.execute('SELECT * FROM jobs WHERE id=?', (run['job_id'],)).fetchone())
            checkpoint = json.loads(run['checkpoint']) if run['checkpoint'] else None
            success = result.get('status') in ('answered', 'completed')
            state, next_at = 'needs_attention', None
            if checkpoint:
                job['progress'] = checkpoint['progress']
            if success:
                if checkpoint and checkpoint['outcome'] == 'complete':
                    state = 'completed'
                elif checkpoint and checkpoint['outcome'] == 'blocked':
                    state = 'needs_attention'
                elif (not checkpoint and job.get('conversation_id') and not job['daily']
                      and not job['interval_minutes'] and result.get('status') == 'answered'):
                    # A delivered conversational answer needs no model-generated
                    # checkpoint; explicit continuation/blocked checkpoints win.
                    state = 'completed'
                elif job['runs'] >= job['max_runs']:
                    state = 'limit_reached'
                elif checkpoint and checkpoint['outcome'] == 'continue':
                    state, next_at = 'scheduled', max(now + 60, checkpoint['next_at'])
                elif job['daily'] or job['interval_minutes']:
                    state = 'scheduled'
                    next_at = (next_daily(job['daily'], job['timezone'], now) if job['daily']
                               else now + job['interval_minutes'] * 60)
                # One-shot without an explicit completion checkpoint remains reviewable.
            if job['state'] in ('paused', 'cancelled'):
                state, next_at = job['state'], None
            data = {k: v for k, v in job.items() if k not in ('id', 'state', 'next_at', 'runs', 'next_local')}
            db.execute('UPDATE jobs SET data=?,state=?,next_at=? WHERE id=?',
                       (json.dumps(data, ensure_ascii=False), state, next_at, job['id']))
            db.execute('UPDATE runs SET finished=?,status=?,result=? WHERE id=?',
                       (now, result.get('status', 'failed'), json.dumps(result, ensure_ascii=False), run_id))

    def recover(self):
        """Only call under the service lock. Never replay uncertain side effects."""
        with self.connect() as db:
            db.execute("UPDATE jobs SET state='needs_attention',next_at=NULL WHERE state='running'")
            db.execute("UPDATE runs SET status='interrupted',finished=? WHERE status='running'", (time.time(),))

    def history(self, job_id=None, limit=20):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM runs WHERE (? IS NULL OR job_id=?) ORDER BY started DESC LIMIT ?',
                              (job_id, job_id, max(1, min(int(limit), 100))))
            return [dict(r) for r in rows]
