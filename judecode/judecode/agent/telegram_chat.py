"""Durable transport for the SAME interactive AgentEngine used by the terminal.

Chat turns are not scheduled goals. One live engine retains full native history;
restart uses native transcript recovery, never replaying an interrupted turn.
"""
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import sqlite3
import threading
import time


class ChatStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'chat.sqlite3'
        with self.connect() as db:
            # SQLite creates 0644 files independently of the process umask.
            # The owner must explicitly grant the transport group write access.
            if self.path.stat().st_uid == os.geteuid():
                self.path.chmod(0o660)
            db.executescript('''
                CREATE TABLE IF NOT EXISTS turns (
                  id INTEGER PRIMARY KEY, text TEXT NOT NULL, state TEXT NOT NULL,
                  response TEXT, delivered INTEGER DEFAULT 0, stop INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS scheduled (id TEXT PRIMARY KEY);
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

    def enqueue(self, uid, text):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO turns(id,text,state) VALUES(?,?,?)', (uid, text, 'pending'))

    def claim(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM turns WHERE state='pending' ORDER BY id LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE turns SET state='running' WHERE id=?", (row['id'],))
                return dict(row)

    def finish(self, uid, response, state='done'):
        with self.connect() as db:
            db.execute('UPDATE turns SET state=?,response=? WHERE id=?', (state, response, uid))

    def pending_replies(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM turns WHERE response IS NOT NULL AND delivered=0 ORDER BY id")]

    def mark_delivered(self, uid, state=1):
        with self.connect() as db:
            db.execute('UPDATE turns SET delivered=? WHERE id=?', (state, uid))

    def stop(self):
        with self.connect() as db:
            db.execute("UPDATE turns SET stop=1 WHERE state='running'")
            db.execute("UPDATE turns SET state='cancelled' WHERE state='pending'")

    def stop_requested(self, uid):
        with self.connect() as db:
            return bool(db.execute('SELECT stop FROM turns WHERE id=?', (uid,)).fetchone()[0])

    def busy(self):
        with self.connect() as db:
            return bool(db.execute("SELECT 1 FROM turns WHERE state='running'").fetchone())

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE turns SET state='interrupted', response=? WHERE state='running'",
                       ('การเชื่อมต่อขาดระหว่างทำงานครับ ผมเก็บบทสนทนาไว้แล้ว แต่จะไม่รันคำสั่งเดิมซ้ำโดยไม่ตรวจผลก่อน',))

    def session(self, value=None):
        with self.connect() as db:
            if value is not None:
                db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', ('session', value))
            row = db.execute('SELECT value FROM metadata WHERE key=?', ('session',)).fetchone()
            return row[0] if row else None

    def track_schedules(self, messages):
        calls = {}
        with self.connect() as db:
            for msg in messages:
                for call in msg.get('tool_calls', []):
                    calls[call['id']] = call.get('function', {}).get('name')
                if msg.get('role') == 'tool' and calls.get(msg.get('tool_call_id')) == 'schedule_create':
                    try:
                        result = json.loads(msg['content'])
                        if isinstance(result, dict) and isinstance(result.get('id'), str):
                            db.execute('INSERT OR IGNORE INTO scheduled VALUES(?)', (result['id'],))
                    except (ValueError, TypeError):
                        pass

    def schedule_ids(self):
        with self.connect() as db:
            return {r[0] for r in db.execute('SELECT id FROM scheduled')}


class ChatSession:
    def __init__(self, agent, store):
        self.agent, self.store = agent, store

    def restore(self):
        previous = self.store.session()
        if previous:
            self.agent.resume_session(previous)
        self.agent.save_transcript()
        self.store.session(self.agent.autonomous.session.session_id)

    async def handle(self, turn):
        agent = self.agent
        if turn['text'] == '/new':
            agent.new_conversation()
            response = 'ได้ครับ เริ่มคุยเรื่องใหม่กัน'
        else:
            agent.reset_stop()
            if turn['text'] == '/continue':
                await agent.continue_task()
            else:
                # Exact same call and tool loop as run_agent_interactive on Mac.
                await agent.chat(turn['text'])
            last_user = max((i for i, m in enumerate(agent.messages) if m.get('role') == 'user'), default=-1)
            new_messages = agent.messages[last_user + 1:]
            self.store.track_schedules(agent.messages)
            response = next((m['content'] for m in reversed(new_messages)
                             if m.get('role') == 'assistant' and m.get('content')), '')
            if not response:
                response = ('หยุดแล้วครับ' if self.store.stop_requested(turn['id'])
                            else 'รอบนี้ผมยังตอบไม่สำเร็จครับ ลองบอกผมให้ทำต่อได้')
        agent.save_transcript()
        self.store.session(agent.autonomous.session.session_id)
        if turn["text"] != "/new":
            response = agent.delivery_response(response)
        self.store.finish(turn['id'], response)


async def serve():
    import fcntl
    from judecode.agent.engine import AgentEngine
    from judecode.api import create_api_client
    from judecode.config import SYSTEM_PROMPT
    os.umask(0o007)
    store = ChatStore(os.environ['JUDECODE_TELEGRAM_SCHEDULER_ROOT'])
    with (store.root / 'chat-service.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        store.recover()
        api = create_api_client()
        agent = AgentEngine(SYSTEM_PROMPT, api)
        agent.permissions.load_from_config()
        # Transport handles Telegram delivery; disable duplicate notification channels.
        agent.notifications.providers = []
        session = ChatSession(agent, store)
        session.restore()
        stopping = threading.Event()
        active = [None]

        def stop(*_):
            stopping.set()
            agent.cancel_requested = True
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

        def watch():
            while not stopping.wait(.25):
                uid = active[0]
                if uid is not None and store.stop_requested(uid):
                    agent.cancel_requested = True
        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        print('Native AgentEngine conversation ready', flush=True)
        try:
            while not stopping.is_set():
                turn = store.claim()
                if not turn:
                    await asyncio.sleep(.25)
                    continue
                active[0] = turn['id']
                try:
                    await session.handle(turn)
                except Exception as exc:
                    # Native transcripts retain tool results; never replay automatically.
                    store.finish(turn['id'], 'ผมติดปัญหาระหว่างตอบครับ (' + type(exc).__name__ + ') เก็บบทสนทนาไว้แล้ว', 'failed')
                finally:
                    active[0] = None
        finally:
            stopping.set()
            watcher.join(timeout=1)
            close = getattr(api, 'close', None)
            if close:
                await close()


if __name__ == '__main__':
    asyncio.run(serve())
