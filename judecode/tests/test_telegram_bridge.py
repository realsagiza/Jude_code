import json
from pathlib import Path
import pytest
from judecode.agent.scheduler import ScheduleStore
from judecode.agent.telegram_bridge import Bridge, TelegramAPI, TelegramError


class API:
    def __init__(self):
        self.sent = []
    def send(self, owner, text):
        self.sent.append((owner, text))


@pytest.fixture
def bridge(tmp_path):
    return Bridge(123, API(), ScheduleStore(tmp_path / 'scheduler'), tmp_path / 'bot',
                  tmp_path, 456, clock=lambda: 2000000000)


def update(text='ตรวจ TODO', uid=1, owner=123, **extra):
    msg = {'from': {'id': owner}, 'chat': {'id': owner, 'type': 'private'},
           'date': 2000000000, 'text': text}
    msg.update(extra)
    return {'update_id': uid, 'message': msg}


@pytest.mark.parametrize('change', [
    {'owner': 124}, {'chat': {'id': -123, 'type': 'group'}},
    {'chat': {'id': 123, 'type': 'supergroup'}}, {'date': 1999999990},
    {'forward_origin': {'type': 'user'}}, {'sender_chat': {'id': 123}},
    {'from': {'id': 123, 'is_bot': True}}, {'text': None},
])
def test_unauthorized_never_queues_or_replies(bridge, change):
    bridge.process(update(**change))
    assert not bridge.store.list()
    assert not bridge.api.sent


def test_edited_message_ignored(bridge):
    data = update()
    data['edited_message'] = data.pop('message')
    bridge.process(data)
    assert not bridge.store.list()


def test_replay_is_deduplicated_after_restart(bridge, tmp_path):
    bridge.process(update())
    bridge.db.close()
    bridge = Bridge(123, bridge.api, bridge.store, tmp_path / 'bot', tmp_path, 456, clock=lambda: 2000000000)
    bridge.process(update())
    assert len(bridge.store.list()) == 1
    assert len(bridge.api.sent) == 1
    job = bridge.store.list()[0]
    assert job['mode'] == 'observe' and job['max_runs'] == 10 and job['budget'] == .25
    assert job['project'] == bridge.project


def test_idempotent_scheduler_request(bridge):
    store = bridge.store
    args = dict(name='t', goal='g', run_at='2035-01-01T00:00:00+00:00',
                project=bridge.project, request_key='telegram:1:1')
    a = store.create(**args)
    b = store.create(**args)
    assert a['id'] == b['id']
    assert len(store.list()) == 1


@pytest.mark.parametrize('text,field,value', [('/work แก้ไฟล์', 'mode', 'work'),
    ('/daily 09:00 ตรวจ TODO', 'daily', '09:00'), ('/after 10 ตรวจ TODO', 'next_at', 2000000600)])
def test_schedule_commands(bridge, text, field, value):
    bridge.process(update(text))
    assert bridge.store.list()[0][field] == value


def test_result_delivery_is_owner_only_and_once(bridge):
    bridge.process(update())
    run = bridge.store.claim(now=2000000010)
    bridge.store.checkpoint(run['run_id'], 'complete', 'done', now=2000000010)
    bridge.store.finish(run['run_id'], {'status': 'answered', 'summary': 'เสร็จแล้ว'}, now=2000000011)
    bridge.deliver()
    bridge.deliver()
    assert len(bridge.api.sent) == 2
    assert all(o == 123 for o, _ in bridge.api.sent)
    assert 'เสร็จแล้ว' in bridge.api.sent[-1][1]
    bridge.process(update('/result ' + run['id'], uid=2))
    assert bridge.api.sent[-1][1] == 'เสร็จแล้ว'


def test_uncertain_send_not_retried(bridge):
    bridge.process(update())
    run = bridge.store.claim(now=2000000010)
    bridge.store.finish(run['run_id'], {'status': 'failed'}, now=2000000011)
    calls = []
    def fail(*args):
        calls.append(args)
        raise TelegramError('timeout')
    bridge.api.send = fail
    bridge.deliver()
    bridge.deliver()
    assert len(calls) == 1


def test_cannot_manage_non_telegram_jobs(bridge):
    job = bridge.store.create('local', 'g', interval_minutes=5, project=bridge.project)
    bridge.process(update('/cancel ' + job['id']))
    assert bridge.store.get(job['id'])['state'] == 'scheduled'


def test_stop_cancels_own_queue_only(bridge):
    bridge.process(update())
    bridge.process(update('/stop', uid=2))
    assert bridge.store.list()[0]['state'] == 'cancelled'


def test_backpressure(bridge):
    for i in range(6):
        bridge.process(update(uid=i))
    assert len(bridge.store.list()) == 5
    assert '5' in bridge.api.sent[-1][1]


def test_empty_allowlist_fails_closed(tmp_path):
    with pytest.raises(ValueError):
        Bridge(0, API(), ScheduleStore(tmp_path), tmp_path, tmp_path, 1)


def test_api_redaction_and_no_markup():
    api = TelegramAPI('123:secret')
    calls = []
    api.call = lambda method, **kw: calls.append((method, kw))
    api.send(123, '123:secret <a>link</a>' + 'ก' * 6500)
    assert len(calls) == 3
    assert '123:secret' not in repr(calls)
    assert all('parse_mode' not in kw and kw['chat_id'] == 123 for _, kw in calls)
    api.client.close()


def test_crash_after_enqueue_keeps_owner_mapping(bridge):
    create = bridge.store.create
    def crash(**kwargs):
        create(**kwargs)
        raise RuntimeError('simulated crash')
    bridge.store.create = crash
    with pytest.raises(RuntimeError):
        bridge.process(update())
    assert bridge.store.list()[0]['id'] in bridge.owned()
    bridge.process(update())
    assert len(bridge.store.list()) == 1


def test_token_url_exception_is_sanitized():
    import httpx
    api = TelegramAPI('123:secret')
    api.client.close()
    def fail(request):
        raise httpx.ConnectError('https://api.telegram.org/bot123:secret/getMe')
    api.client = httpx.Client(transport=httpx.MockTransport(fail))
    with pytest.raises(TelegramError) as error:
        api.call('getMe')
    assert 'secret' not in str(error.value)
    api.client.close()
