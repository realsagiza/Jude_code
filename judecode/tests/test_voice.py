"""Speech contracts and cancellation without microphone, credentials or paid API."""
import asyncio
import base64
import json
import sys
from types import SimpleNamespace

import httpx
import pytest

from judecode.api import voice
from judecode.ui.tui_app import JudeCodeTUI


def test_openrouter_contract(monkeypatch):
    monkeypatch.setattr(voice.config, 'OPENROUTER_API_KEY', 'test-key')
    requests = []
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert request.headers['Authorization'] == 'Bearer test-key'
        if request.url.path.endswith('transcriptions'):
            assert payload['language'] == 'th'
            assert base64.b64decode(payload['input_audio']['data']) == b'wav'
            return httpx.Response(200, json={'text': ' สวัสดีครับ '})
        assert payload['response_format'] == 'pcm'
        assert 'secret_code' not in payload['input']
        return httpx.Response(200, content=b'\x00\x00', headers={'Content-Type': 'audio/pcm'})
    async def run():
        client = voice.VoiceClient(transport=httpx.MockTransport(handler))
        try:
            assert await client.transcribe(b'wav') == 'สวัสดีครับ'
            assert (await client.synthesize('เสร็จแล้ว ```secret_code```')).startswith(b'RIFF')
        finally:
            await client.close()
    asyncio.run(run())
    assert len(requests) == 2


@pytest.mark.parametrize('status', [401, 402, 429, 500])
def test_http_errors_do_not_expose_body_or_key(monkeypatch, status):
    monkeypatch.setattr(voice.config, 'OPENROUTER_API_KEY', 'secret-key')
    async def run():
        client = voice.VoiceClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(status, text='secret-key private-transcript')))
        try:
            with pytest.raises(voice.VoiceError) as error:
                await client.transcribe(b'wav')
            assert str(status) in str(error.value)
            assert 'secret' not in str(error.value)
        finally:
            await client.close()
    asyncio.run(run())


@pytest.mark.parametrize('payload', [{}, {'text': ''}, {'text': None}, []])
def test_invalid_transcription(monkeypatch, payload):
    monkeypatch.setattr(voice.config, 'OPENROUTER_API_KEY', 'test')
    async def run():
        client = voice.VoiceClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
        try:
            with pytest.raises(voice.VoiceError):
                await client.transcribe(b'wav')
        finally:
            await client.close()
    asyncio.run(run())


def test_missing_key_no_network(monkeypatch):
    monkeypatch.setattr(voice.config, 'OPENROUTER_API_KEY', '')
    async def run():
        client = voice.VoiceClient(transport=httpx.MockTransport(lambda r: pytest.fail('network called')))
        try:
            with pytest.raises(voice.VoiceError, match='API_KEY'):
                await client.transcribe(b'wav')
        finally:
            await client.close()
    asyncio.run(run())


def test_prose_and_reply_selection():
    assert 'hidden' not in voice.spoken_text('สวัสดี ```hidden')
    assert voice.spoken_text('[คู่มือ](https://example.com)') == 'คู่มือ'
    assert len(voice.spoken_text('ก' * 5000)) < 3100
    assert voice.final_reply([{'role': 'assistant', 'content': 'เก่า'}, {'role': 'user', 'content': 'ใหม่'}]) == ''
    assert voice.final_reply([{'role': 'assistant', 'content': 'เรียก', 'tool_calls': [{}]}]) == ''
    assert voice.final_reply([{'role': 'assistant', 'content': 'สำเร็จ'}]) == 'สำเร็จ'


def test_record_cancel_closes_mic(monkeypatch):
    events = []
    class Stream:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            events.append('open')
        def __exit__(self, *args):
            events.append('closed')
    monkeypatch.setitem(sys.modules, 'sounddevice', SimpleNamespace(RawInputStream=Stream))
    async def run():
        task = asyncio.create_task(voice.record_wav(8))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert events == ['open', 'closed']


def test_playback_cancel_kills_process_and_deletes_file(monkeypatch):
    paths = []
    class Process:
        returncode = None
        async def wait(self):
            if self.returncode is None:
                await asyncio.Event().wait()
            return self.returncode
        def kill(self):
            self.returncode = -9
    process = Process()
    async def create(*args, **kwargs):
        from pathlib import Path
        paths.append(Path(args[-1]))
        assert paths[-1].read_bytes() == b'mp3'
        return process
    monkeypatch.setattr(voice, 'player_command', lambda: ['fake-player'])
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', create)
    async def run():
        task = asyncio.create_task(voice.play_audio(b'mp3'))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert process.returncode == -9
    assert not paths[0].exists()


def test_listen_preserves_typed_draft(monkeypatch):
    import judecode.ui.tui_app as tui
    output = []
    draft = SimpleNamespace(value='ข้อความที่กำลังพิมพ์')
    async def record(seconds):
        return b'wav'
    async def transcribe(audio):
        return 'ข้อความจากเสียง'
    monkeypatch.setattr(tui, 'record_wav', record)
    ui = SimpleNamespace(
        _voice_client=SimpleNamespace(check_key=lambda: None, transcribe=transcribe),
        _console_sink=output.append,
        query_one=lambda selector, cls: draft if selector == '#message' else SimpleNamespace(write=output.append),
    )
    asyncio.run(JudeCodeTUI._listen(ui, 8))
    assert draft.value == 'ข้อความที่กำลังพิมพ์'
    assert 'ข้อความจากเสียง' in output[-1].plain


@pytest.mark.parametrize('command', ['/listen 0', '/listen 61', '/listen NaN', '/listen 2 extra'])
def test_bad_duration_does_not_record(command):
    output = []
    ui = SimpleNamespace(ai_busy=False, _console_sink=output.append)
    asyncio.run(JudeCodeTUI._handle_command(ui, command))
    assert '1–60' in output[0]


def test_tui_voice_roundtrip(monkeypatch, tmp_path):
    import judecode.ui.tui_app as tui
    from pathlib import Path
    from textual.widgets import Input
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(voice.config, 'OPENROUTER_API_KEY', 'test')
    monkeypatch.setattr(tui, 'player_command', lambda: ['fake'])
    spoken = []
    async def record(seconds):
        return b'wav'
    monkeypatch.setattr(tui, 'record_wav', record)
    async def run():
        app = JudeCodeTUI()
        async def transcribe(audio):
            return 'ช่วยตอบภาษาไทย'
        async def synthesize(text):
            spoken.append(text)
            return b'wav'
        async def playback(audio):
            assert audio == b'wav'
        async def chat(text):
            assert text == 'ช่วยตอบภาษาไทย'
            app.agent.messages.append({'role': 'assistant', 'content': 'ได้ครับ'})
        monkeypatch.setattr(app._voice_client, 'transcribe', transcribe)
        monkeypatch.setattr(app._voice_client, 'synthesize', synthesize)
        monkeypatch.setattr(tui, 'play_audio', playback)
        monkeypatch.setattr(app.agent, 'chat', chat)
        async with app.run_test() as pilot:
            await app._handle_command('/voice read')
            await app._handle_command('/listen 1')
            await app._voice_task
            assert app.query_one('#message', Input).value == 'ช่วยตอบภาษาไทย'
            await pilot.press('enter')
            await pilot.pause()
            assert spoken == ['ได้ครับ']
            await app._handle_command('/voice off')
            assert not app._voice_enabled
    asyncio.run(run())


@pytest.mark.parametrize('content, content_type', [(b'{}', 'application/json'), (b'', 'audio/pcm'), (b'x', 'audio/pcm')])
def test_rejects_invalid_audio(monkeypatch, content, content_type):
    monkeypatch.setattr(voice.config, 'OPENROUTER_API_KEY', 'test')
    async def run():
        client = voice.VoiceClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=content, headers={'Content-Type': content_type})))
        try:
            with pytest.raises(voice.VoiceError):
                await client.synthesize('สวัสดี')
        finally:
            await client.close()
    asyncio.run(run())


def test_timeout_is_recoverable(monkeypatch):
    monkeypatch.setattr(voice.config, 'OPENROUTER_API_KEY', 'test')
    def handler(request):
        raise httpx.ReadTimeout('internal connection details')
    async def run():
        client = voice.VoiceClient(transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(voice.VoiceError, match='หมดเวลา'):
                await client.transcribe(b'wav')
        finally:
            await client.close()
    asyncio.run(run())
