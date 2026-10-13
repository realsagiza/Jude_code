"""Transport failures must never concatenate two model generations."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from judecode.api.client import ApiClient
from judecode.api.anthropic import AnthropicClient
from tests.test_reliability import agent, calls, ScriptedAPI


class BrokenStream(httpx.AsyncByteStream):
    def __init__(self, data, error):
        self.data, self.error = data, error

    async def __aiter__(self):
        if self.data:
            yield self.data
        if self.error:
            raise self.error('stream disconnected')


def sse(event):
    return ('data: ' + json.dumps(event) + '\n\n').encode()


@pytest.mark.parametrize('client_type', [ApiClient, AnthropicClient])
@pytest.mark.parametrize('error', [httpx.RemoteProtocolError, httpx.ReadTimeout, httpx.ReadError])
@pytest.mark.parametrize('partial', [True, False])
def test_retry_only_before_any_chunk(client_type, error, partial):
    async def run():
        client = client_type(api_key='test', max_retries=3)
        if client_type is ApiClient:
            first = calls(('write', '{"path":'), finish=None)
            done = {'choices': [{'delta': {'content': 'ok'}, 'finish_reason': 'stop'}]}
        else:
            first = {'type': 'content_block_start', 'index': 0,
                     'content_block': {'type': 'tool_use', 'id': 'call', 'name': 'write'}}
            done = {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}}
        requests = []
        def handler(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(200, stream=BrokenStream(sse(first) if partial else b'', error))
            return httpx.Response(200, content=sse(done))
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            chunks = [c async for c in client.chat_completion([{'role': 'user', 'content': 'work'}])]
            assert len(requests) == (1 if partial else 2)
            assert bool(chunks[-1].get('error')) == partial
        finally:
            await client.close()
    asyncio.run(run())


@pytest.mark.parametrize('ending', [b'', b'data: [DONE]\n\n', b'data: {bad}\n\n'])
def test_unfinished_stream_rejected_even_if_tool_json_is_valid(ending):
    async def run():
        client = ApiClient(api_key='test')
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, content=sse(calls(('write', {}), finish=None)) + ending)))
        try:
            chunks = [c async for c in client.chat_completion([])]
            assert chunks[-1].get('error')
        finally:
            await client.close()
    asyncio.run(run())


def test_engine_stops_after_three_invalid_generations(agent, monkeypatch):
    monkeypatch.setattr(agent, '_execute_tool_safe', lambda *a: pytest.fail('Executed invalid call'))
    agent.api = ScriptedAPI(*[calls(('write', '{'), finish='length') for _ in range(8)])
    asyncio.run(agent.chat('write a file'))
    assert len(agent.api.requests) == 3
    assert agent.autonomous.session.status == 'failed'
    assert '3 consecutive' in agent.autonomous.session.outcome_reason


def test_engine_never_executes_complete_json_from_unfinished_stream(agent, monkeypatch):
    monkeypatch.setattr(agent, '_execute_tool_safe', lambda *a: pytest.fail('Executed unfinished stream'))
    agent.continuation.continue_on_stream_error = True
    agent.api = ScriptedAPI(*[calls(('write', {}), finish=None) for _ in range(8)])
    asyncio.run(agent.chat('work'))
    assert len(agent.api.requests) == 3
    assert agent.autonomous.session.status == 'failed'


@pytest.mark.parametrize('bad', ['{"path":"a","path":"b"}', '{"nested":{"x":1,"x":2}}'])
def test_duplicate_json_keys_are_not_executed(agent, monkeypatch, bad):
    monkeypatch.setattr(agent, '_execute_tool_safe', lambda *a: pytest.fail('Ambiguous arguments executed'))
    agent.api = ScriptedAPI(calls(('write', bad)))
    asyncio.run(agent._process_turn())
    assert agent._tool_failures


def test_sparse_stream_index_is_valid(agent, monkeypatch):
    executed = []
    monkeypatch.setattr(agent, '_execute_tool_safe', lambda *a: executed.append(a) or 'ok')
    chunk = calls(('read', {'path': 'a'}))
    chunk['choices'][0]['delta']['tool_calls'][0]['index'] = 2
    agent.api = ScriptedAPI(chunk)
    asyncio.run(agent._process_turn())
    assert executed == [('read', {'path': 'a'})]


def test_fragmented_unicode_arguments_execute_once_after_finish(agent, monkeypatch):
    executed = []
    monkeypatch.setattr(agent, '_execute_tool_safe', lambda *a: executed.append(a) or 'ok')
    class FragmentedAPI(ScriptedAPI):
        async def chat_completion(self, *a, **kw):
            yield calls(('write', '{"path":"a",'), finish=None)
            assert not executed
            yield {'choices': [{'delta': {'tool_calls': [{'index': 0,
                'function': {'arguments': '"content":"ภาษาไทย"}'}}]}, 'finish_reason': None}]}
            assert not executed
            yield {'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}]}
    agent.api = FragmentedAPI()
    asyncio.run(agent._process_turn())
    assert executed == [('write', {'path': 'a', 'content': 'ภาษาไทย'})]


def test_cancel_mid_tool_stream_remains_paused(agent, monkeypatch):
    monkeypatch.setattr(agent, '_execute_tool_safe', lambda *a: pytest.fail('Executed after cancel'))
    class CancelAPI(ScriptedAPI):
        async def chat_completion(self, *a, **kw):
            yield calls(('write', '{'), finish=None)
            agent.cancel_requested = True
            yield {'choices': [{'delta': {}, 'finish_reason': None}]}
    agent.api = CancelAPI()
    assert asyncio.run(agent._process_turn()) is False
    assert agent._stop_outcome == ('paused', 'Stopped by user')
    assert agent._stream_error_rounds == 0


@pytest.mark.parametrize('kind', ['missing_id', 'duplicate_id', 'bad_index', 'missing_arguments'])
def test_malformed_call_structure_does_not_execute(agent, monkeypatch, kind):
    monkeypatch.setattr(agent, '_execute_tool_safe', lambda *a: pytest.fail('Malformed call executed'))
    chunk = calls(('write', {}), ('write', {}))
    items = chunk['choices'][0]['delta']['tool_calls']
    if kind == 'missing_id':
        items[0].pop('id')
    elif kind == 'duplicate_id':
        items[1]['id'] = items[0]['id']
    elif kind == 'bad_index':
        items[0]['index'] = -1
    else:
        for item in items:
            item['function'].pop('arguments')
    agent.api = ScriptedAPI(chunk)
    asyncio.run(agent._process_turn())


@pytest.mark.parametrize('stream', [True, False])
def test_api_boundary_repairs_legacy_history_without_mutation(tmp_path, monkeypatch, stream):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    messages = [{'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': 'bad', 'type': 'function', 'function': {'name': 'write', 'arguments': '{'}}]},
        {'role': 'tool', 'tool_call_id': 'bad', 'content': 'invalid JSON'}]
    async def run():
        client = ApiClient(api_key='test')
        def handler(request):
            payload = json.loads(request.content)
            assert payload['messages'][0]['tool_calls'][0]['function']['arguments'] == '{}'
            assert 'do not execute' in payload['messages'][0]['content']
            if stream:
                return httpx.Response(200, content=sse({'choices': [{'delta': {}, 'finish_reason': 'stop'}]}))
            return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            if stream:
                assert [c async for c in client.chat_completion(messages)]
            else:
                assert await client.chat_completion_sync(messages)
        finally:
            await client.close()
    asyncio.run(run())
    assert messages[0]['tool_calls'][0]['function']['arguments'] == '{'
