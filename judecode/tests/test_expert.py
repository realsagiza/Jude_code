"""Offline routing integration tests: real engine, scripted providers, isolated files."""
import asyncio
import json
from pathlib import Path

import pytest
from judecode.agent import expert as mod
from judecode.agent.engine import AgentEngine


def response(text):
    return {"choices": [{"delta": {"content": text}, "finish_reason": "stop"}]}


def calls(*items):
    return {"choices": [{"delta": {"tool_calls": [
        {"index": i, "id": 'call' + str(i), "function": {
            "name": name, "arguments": json.dumps(args)}}
        for i, (name, args) in enumerate(items)]}, "finish_reason": "tool_calls"}]}


BRIEF = dict(reason="Cross-file bug", goal="Fix calculation", constraints="Preserve APIs", acceptance_checks="Run regression test")


class FakeAPI:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []
        self.closed = 0

    async def chat_completion(self, messages, tools=None):
        self.requests.append((list(messages), tools))
        yield next(self.replies)

    def _extract_reasoning(self, chunk):
        return ""

    async def close(self):
        self.closed += 1


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    monkeypatch.setattr(mod.config, 'OPENROUTER_API_KEY', 'fake-key')
    monkeypatch.setattr(mod.config, 'EXPERT_ENABLED', True)
    def make(primary_replies, expert_replies):
        primary = FakeAPI(primary_replies)
        specialist = FakeAPI(expert_replies)
        kwargs = []
        def factory(**kw):
            kwargs.append(kw)
            return specialist
        monkeypatch.setattr(mod, 'ApiClient', factory)
        agent = AgentEngine('system', primary)
        agent.autonomous.enabled = False
        agent.notifications.notify_session_complete = lambda **kw: None
        return agent, primary, specialist, kwargs
    return make


def test_handoff_executes_tools_and_returns_to_primary(setup, tmp_path):
    agent, primary, specialist, kwargs = setup(
        [calls(('expert_help', BRIEF)), response('Summary of expert findings'),
         response('Checks unavailable'), response('Still unverified')],
        [calls(('write', {'path': 'result.txt', 'content': 'fixed'})),
         calls(('read', {'path': 'result.txt'})),
         calls(('expert_return', {'summary': 'File written and read; tests not run'}))])
    asyncio.run(agent.chat('Implement fix'))
    assert (tmp_path / 'result.txt').read_text() == 'fixed'
    assert len(primary.requests) == 4 and len(specialist.requests) == 3
    assert agent.autonomous.session.status == "unverified"
    assert specialist.closed == 3
    assert kwargs[0]['model'] == 'z-ai/glm-5.3'
    assert kwargs[0]['max_retries'] == 1
    assert not agent.expert.active
    assert 'expert_return' in str(specialist.requests[0][1])
    assert 'expert_help' not in str(specialist.requests[0][1])
    assert any(m.get('content') == 'Implement fix' for m in specialist.requests[0][0])


def test_plain_expert_report_returns_to_primary(setup):
    agent, primary, expert, _ = setup([calls(('expert_help', BRIEF)), response('Unverified summary')], [response('Unable to finish')])
    asyncio.run(agent.chat('Fix'))
    assert len(primary.requests) == 2
    assert not agent.expert.active
    assert agent.autonomous.session.status != 'completed'


def test_mixed_handoff_batch_cannot_write(setup, tmp_path):
    agent, _, expert, _ = setup([calls(('expert_help', BRIEF), ('write', {'path': 'bad', 'content': 'bad'})), response('Stopped')], [])
    asyncio.run(agent.chat('Fix'))
    assert not (tmp_path / 'bad').exists()
    assert not expert.requests


def test_permissions_and_sandbox_apply_to_routing(setup):
    agent, _, _, _ = setup([], [])
    agent.permissions.check_permission = lambda *args: (False, None, 'denied')
    assert 'denied' in agent._execute_tool_safe('expert_help', BRIEF)
    assert not agent.expert.active


def test_limits_validation_and_restore(monkeypatch):
    monkeypatch.setattr(mod.config, 'OPENROUTER_API_KEY', 'fake')
    routing = mod.ExpertRouting()
    assert 'Error' in routing.execute('expert_help', {})
    assert 'now owns' in routing.execute('expert_help', BRIEF)
    assert 'nested' in routing.execute('expert_help', BRIEF)
    routing.turns = routing.max_turns
    saved = routing.snapshot()
    restored = mod.ExpertRouting()
    restored.restore(saved)
    assert restored.active and restored.turns == routing.max_turns
    async def run():
        return [x async for x in restored.completion(None, [], [])]
    assert 'error' in asyncio.run(run())[0]
    routing.execute('expert_return', {'summary': 'Not finished'})
    assert 'limit' in routing.execute('expert_help', BRIEF)


def test_new_request_resets_to_primary(setup):
    agent, primary, _, _ = setup([response('Hello')], [])
    agent.expert.active = True
    agent.expert.turns = 9
    asyncio.run(agent.chat('New request'))
    assert len(primary.requests) == 1
    assert agent.expert.turns == 0


def test_disabled_has_no_tools(monkeypatch):
    monkeypatch.setattr(mod.config, 'EXPERT_ENABLED', False)
    routing = mod.ExpertRouting()
    assert routing.tools([]) == []
    assert 'Error' in routing.execute('expert_help', BRIEF)


def test_pause_and_resume_preserve_expert_route(setup):
    agent, _, _, _ = setup([], [])
    agent.autonomous.session.original_goal = 'Fix'
    agent.expert.execute('expert_help', BRIEF)
    agent.expert.turns = 3
    agent.save_transcript()
    source = agent.autonomous.session.session_id
    agent.resume_session(source)
    assert agent.expert.active
    assert agent.expert.turns == 3


def test_stop_during_expert_stream_does_not_execute_tools(setup, tmp_path):
    agent, _, specialist, _ = setup([], [])
    agent.expert.active = True
    async def stopped(messages, tools=None):
        agent.cancel_requested = True
        yield calls(('write', {'path': 'bad', 'content': 'bad'}))
    specialist.chat_completion = stopped
    asyncio.run(agent._process_turn())
    assert not (tmp_path / 'bad').exists()
    assert agent._stop_outcome[0] == 'paused'
    assert specialist.closed == 1


def test_expert_cannot_bypass_write_permission(setup, tmp_path):
    agent, _, _, _ = setup([calls(('expert_help', BRIEF)), response('Blocked')],
        [calls(('write', {'path': 'denied', 'content': 'bad'})), response('Write denied')])
    original = agent.permissions.check_permission
    agent.permissions.check_permission = lambda name, args: (False, None, 'denied') if name == 'write' else original(name, args)
    asyncio.run(agent.chat('Fix'))
    assert not (tmp_path / 'denied').exists()
    assert agent.autonomous.session.status != 'completed'


def test_expert_http_request_uses_configured_model_and_preserves_reasoning(monkeypatch):
    import httpx
    from judecode.api.client import ApiClient
    monkeypatch.setattr(mod.config, 'OPENROUTER_API_KEY', 'fake')
    sent = []
    def transport(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, text='data: ' + json.dumps(response('Report')) + '\n\ndata: [DONE]\n\n')
    class Client(ApiClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    monkeypatch.setattr(mod, 'ApiClient', Client)
    routing = mod.ExpertRouting()
    routing.execute('expert_help', BRIEF)
    history = [{'role': 'user', 'content': 'Fix'}, {'role': 'assistant', 'content': 'Previous finding', 'reasoning_content': 'Preserved reasoning'}]
    async def run():
        return [x async for x in routing.completion(None, history, routing.tools([]))]
    asyncio.run(run())
    assert sent[0]['model'] == 'z-ai/glm-5.3'
    assert sent[0]['messages'][1]['reasoning_content'] == 'Preserved reasoning'
    assert sent[0]['max_tokens'] == mod.config.EXPERT_MAX_TOKENS
