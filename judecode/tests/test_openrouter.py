"""OpenRouter configuration and OpenAI-compatible transport regression tests."""
import asyncio
import json
import os
from pathlib import Path
import runpy

import httpx
import pytest

CONFIG = Path(__file__).parents[1] / 'judecode' / 'config.py'


def load_config(monkeypatch, **settings):
    # Never read the developer's .env or use real credentials in unit tests.
    monkeypatch.setattr('dotenv.load_dotenv', lambda *a, **kw: None)
    for key in list(os.environ):
        if key.startswith('JUDECODE_') or key == 'OPENROUTER_API_KEY':
            monkeypatch.delenv(key)
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    return runpy.run_path(str(CONFIG))


@pytest.mark.parametrize('key_name', ['JUDECODE_OPENROUTER_API_KEY', 'OPENROUTER_API_KEY'])
def test_openrouter_config(monkeypatch, key_name):
    config = load_config(monkeypatch, JUDECODE_PROVIDER=' OpenRouter ', **{key_name: 'test-key'})
    assert config['PROVIDER'] == 'openrouter'
    assert config['API_KEY'] == config['_ACTIVE_KEY'] == 'test-key'
    assert config['BASE_URL'] == 'https://openrouter.ai/api/v1'
    assert config['MODEL'] == config['FALLBACK_MODEL'] == 'deepseek/deepseek-v3.2'


def test_openrouter_overrides(monkeypatch):
    config = load_config(monkeypatch, JUDECODE_PROVIDER='openrouter',
                         OPENROUTER_API_KEY='ignored', JUDECODE_OPENROUTER_API_KEY='preferred',
                         JUDECODE_OPENROUTER_MODEL='author/primary',
                         JUDECODE_FALLBACK_MODEL='author/fallback',
                         JUDECODE_OPENROUTER_BASE_URL='https://example.test/v1')
    assert config['API_KEY'] == 'preferred'
    assert config['MODEL'] == 'author/primary'
    assert config['FALLBACK_MODEL'] == 'author/fallback'
    assert config['BASE_URL'] == 'https://example.test/v1'


@pytest.mark.parametrize('provider, model', [('deepseek', 'deepseek-chat'), ('zai', 'glm-4.6'),
                                            ('anthropic', 'claude-sonnet-4-20250514'), ('custom', 'custom-model')])
def test_existing_provider_selection(monkeypatch, provider, model):
    config = load_config(monkeypatch, JUDECODE_PROVIDER=provider,
                         **{f'JUDECODE_{provider.upper()}_API_KEY': 'existing-key',
                            f'JUDECODE_{provider.upper()}_MODEL': model})
    assert config['API_KEY'] == 'existing-key'
    assert config['MODEL'] == config['FALLBACK_MODEL'] == model


def test_factory_streaming_and_tool_roundtrip(monkeypatch):
    import judecode.api as api
    from judecode.api.client import ApiClient

    config = load_config(monkeypatch, JUDECODE_PROVIDER='openrouter', JUDECODE_OPENROUTER_API_KEY='test-key')
    monkeypatch.setattr(api, 'PROVIDER', config['PROVIDER'])
    # The factory's generic client uses these config-derived constructor defaults.
    monkeypatch.setattr(ApiClient.__init__, '__defaults__', (
        config['BASE_URL'], config['API_KEY'], config['MODEL'], config['FALLBACK_MODEL'], 64, 0, 1))
    tool_call = {'id': 'call_1', 'type': 'function', 'function': {'name': 'echo', 'arguments': '{"text":"OK"}'}}
    tool = {'type': 'function', 'function': {'name': 'echo', 'parameters': {'type': 'object', 'properties': {'text': {'type': 'string'}}}}}
    requests = []

    def handler(request):
        assert str(request.url) == 'https://openrouter.ai/api/v1/chat/completions'
        assert request.headers['Authorization'] == 'Bearer test-key'
        payload = json.loads(request.content)
        assert payload['model'] == 'deepseek/deepseek-v3.2'
        assert payload['tools'] == [tool]
        requests.append(payload)
        if payload['stream']:
            chunk = {'choices': [{'delta': {'tool_calls': [{'index': 0, **tool_call}]}, 'finish_reason': 'tool_calls'}]}
            return httpx.Response(200, text=': OPENROUTER PROCESSING\n\ndata: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n')
        assert payload['messages'][-1] == {'role': 'tool', 'tool_call_id': 'call_1', 'content': 'OK'}
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': 'OK'}}]})

    async def run():
        client = api.create_api_client()
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            messages = [{'role': 'user', 'content': 'Echo OK'}]
            chunks = [c async for c in client.chat_completion(messages, tools=[tool])]
            assert chunks[0]['choices'][0]['delta']['tool_calls'][0]['id'] == 'call_1'
            messages += [{'role': 'assistant', 'content': None, 'tool_calls': [tool_call]},
                         {'role': 'tool', 'tool_call_id': 'call_1', 'content': 'OK'}]
            response = await client.chat_completion_sync(messages, tools=[tool])
            assert response['choices'][0]['message']['content'] == 'OK'
            assert len(requests) == 2
        finally:
            await client.close()

    asyncio.run(run())
