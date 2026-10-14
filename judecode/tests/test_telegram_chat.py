import asyncio
import copy
import json
import time
import pytest
from judecode.agent.telegram_chat import ChatStore, ChatSession
from judecode.agent.telegram_bridge import ConversationalBridge
from judecode.agent.scheduler import ScheduleStore


class FakeAPI:
    def __init__(self, tool=None, params=None):
        self.tool, self.params = tool, params
        self.sent = False
        self.requests = []
    async def chat_completion(self, messages, tools=None, stream=True):
        self.requests.append(copy.deepcopy(messages))
        if self.tool and not self.sent:
            self.sent = True
            delta = {'tool_calls': [{'index': 0, 'id': 'call1', 'type': 'function',
                     'function': {'name': self.tool, 'arguments': json.dumps(self.params)}}]}
            finish = 'tool_calls'
        else:
            delta, finish = {'content': 'สวัสดีครับ'}, 'stop'
        yield {'choices': [{'delta': delta, 'finish_reason': finish}]}
    def _extract_reasoning(self, chunk):
        return ''


@pytest.fixture
def native(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv('JUDECODE_SCHEDULE_RUN_ID', raising=False)
    monkeypatch.chdir(tmp_path)
    from judecode.agent.engine import AgentEngine
    from judecode.config import SYSTEM_PROMPT
    api = FakeAPI()
    agent = AgentEngine(SYSTEM_PROMPT, api)
    store = ChatStore(tmp_path / 'transport')
    session = ChatSession(agent, store)
    session.restore()
    return session, api, tmp_path


def test_greeting_no_tools_no_schedule_and_same_engine_history(native):
    session, api, root = native
    session.store.enqueue(1, 'สวัสดี ฉันชื่อจูด')
    asyncio.run(session.handle(session.store.claim()))
    session.store.enqueue(2, 'ฉันชื่ออะไร')
    asyncio.run(session.handle(session.store.claim()))
    assert len(api.requests) == 2
    assert any(m.get('content') == 'สวัสดี ฉันชื่อจูด' for m in api.requests[-1])
    assert not ScheduleStore().list()
    assert all(r['response'] == 'สวัสดีครับ' for r in session.store.pending_replies())
    assert not any('scheduled goal' in str(m.get('content','')).lower() for m in session.agent.messages if m['role']=='user')


def test_native_read_tool_only_when_model_requests(native):
    session, api, root = native
    (root / 'example.txt').write_text('real-file-evidence')
    api.tool, api.params = 'read', {'file_path': str(root/'example.txt')}
    # Match the actual tool schema used by the terminal engine.
    from judecode.agent.tools import TOOL_DEFINITIONS
    definition = next(d for d in TOOL_DEFINITIONS if d['function']['name'] == 'read')
    required = definition['function']['parameters']['required'][0]
    api.params = {required: str(root/'example.txt')}
    session.store.enqueue(1, 'อ่าน example.txt')
    asyncio.run(session.handle(session.store.claim()))
    assert any(m['role']=='tool' and 'real-file-evidence' in str(m.get('content')) for m in session.agent.messages)
    assert not ScheduleStore().list()


def test_schedule_only_via_native_tool(native):
    session, api, root = native
    api.tool, api.params = 'schedule_create', {'name':'monitor', 'goal':'อ่าน TODO', 'interval_minutes':10, 'project':str(root)}
    session.store.enqueue(1, 'อีก10นาทีอ่านTODO')
    asyncio.run(session.handle(session.store.claim()))
    jobs = ScheduleStore().list()
    assert len(jobs)==1
    assert jobs[0]['id'] in session.store.schedule_ids()


def test_native_transcript_restore_without_replaying(native):
    session, api, root = native
    session.store.enqueue(1, 'remember me')
    asyncio.run(session.handle(session.store.claim()))
    from judecode.agent.engine import AgentEngine
    restored = ChatSession(AgentEngine(session.agent.system_prompt, api), session.store)
    count = len(api.requests)
    restored.restore()
    assert len(api.requests) == count
    assert any(m.get('content') == 'remember me' for m in restored.agent.messages)


def test_mailbox_replay_and_crash_no_reexecution(tmp_path):
    s=ChatStore(tmp_path)
    s.enqueue(1,'hello'); s.enqueue(1,'hello')
    assert s.claim()['id']==1
    s.recover()
    assert s.claim() is None
    assert s.pending_replies()[0]['state']=='interrupted'


def test_stop_cancels_waiting_and_signals_current(tmp_path):
    s=ChatStore(tmp_path)
    s.enqueue(1,'first'); s.enqueue(2,'next')
    s.claim(); s.stop()
    assert s.stop_requested(1)
    assert s.claim() is None


def test_owner_bridge_routes_plain_text_to_chat_not_scheduler(tmp_path):
    class API:
        def __init__(self): self.sent=[]
        def send(self,*args): self.sent.append(args)
    api=API(); store=ScheduleStore(tmp_path/'shared')
    bridge=ConversationalBridge(123,api,store,tmp_path/'bridge',tmp_path,456,clock=lambda:2000000000)
    event={'update_id':1,'message':{'from':{'id':123},'chat':{'id':123,'type':'private'},'date':2000000000,'text':'hello'}}
    bridge.process(event)
    assert not store.list()
    assert bridge.chat.claim()['text']=='hello'
    assert api.sent==[]
    event['update_id']=2;event['message']['from']['id']=999
    bridge.process(event)
    assert bridge.chat.claim() is None


def test_sqlite_group_write_even_with_restrictive_umask(tmp_path):
    import os, stat
    old = os.umask(0o077)
    try:
        store = ChatStore(tmp_path)
    finally:
        os.umask(old)
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o660


def test_failed_handoff_does_not_consume_update(tmp_path, monkeypatch):
    import sqlite3
    class API:
        def send(self,*args): pass
    b=ConversationalBridge(123,API(),ScheduleStore(tmp_path/'shared'),tmp_path/'bridge',tmp_path,456,clock=lambda:2000000000)
    event={'update_id':1,'message':{'from':{'id':123},'chat':{'id':123,'type':'private'},'date':2000000000,'text':'hello'}}
    original=b.chat.enqueue
    def fail(*args): raise sqlite3.OperationalError('readonly')
    monkeypatch.setattr(b.chat,'enqueue',fail)
    with pytest.raises(sqlite3.OperationalError): b.process(event)
    assert b.db.execute('SELECT 1 FROM updates WHERE id=1').fetchone() is None
    monkeypatch.setattr(b.chat,'enqueue',original)
    b.process(event)
    assert b.chat.claim()['text']=='hello'
