"""Hands-free turn-taking, VAD endpointing and cancellation; no hardware/API."""
import asyncio
import io
import wave
from types import SimpleNamespace

import pytest

from judecode.api import conversation_voice as cv


def test_detector_ignores_silence_and_short_clicks():
    detector = cv.UtteranceDetector(lambda frame, rate: frame == b'v' * 960)
    for frame in [b'\0' * 960] * 100 + [b'v' * 960] * 3 + [b'\0' * 960] * 100:
        assert detector.feed(frame) is None
    assert not detector.started
    assert len(detector.pre) <= 10


def test_detector_keeps_preroll_and_waits_for_pause():
    detector = cv.UtteranceDetector(lambda frame, rate: frame == b'v' * 960, silence_ms=1200)
    for frame in [b'\0' * 960] * 4 + [b'v' * 960] * 15 + [b'\0' * 960] * 39:
        assert detector.feed(frame) is None
    audio = detector.feed(b'\0' * 960)
    with wave.open(io.BytesIO(audio)) as wav:
        assert wav.getframerate() == 16000
        assert wav.getnchannels() == 1
        assert wav.getnframes() == 59 * 480


def test_detector_caps_unbroken_speech():
    detector = cv.UtteranceDetector(lambda *args: True, max_seconds=30)
    for _ in range(999):
        assert detector.feed(b'v' * 960) is None
    with pytest.raises(cv.VoiceError, match='ยังไม่ได้ส่ง'):
        detector.feed(b'v' * 960)


@pytest.mark.parametrize('text, expected', [('หยุดงานครับ', 'stop'), ('ปิดโหมดเสียงค่ะ', 'off'),
    ('ช่วยเพิ่มปุ่มหยุดงาน', None), ('อย่าปิดโหมดเสียง', None), ('เลิกคุย', 'off')])
def test_voice_command_whole_utterance(text, expected):
    assert cv.voice_command(text) == expected


def test_conversation_submits_without_enter_and_stops_by_voice(monkeypatch):
    transcript = iter(['ช่วยตรวจงาน', 'หยุดงานครับ', 'ช่วยทำต่อ', 'ปิดโหมดเสียง'])
    submitted, stopped, spoken = [], [], []
    async def record(on_start):
        on_start()
        return b'wav'
    async def transcribe(audio):
        return next(transcript)
    async def say(self, text):
        spoken.append(text)
    async def submit(text):
        submitted.append(text)
    monkeypatch.setattr(cv, 'record_utterance', record)
    monkeypatch.setattr(cv.VoiceConversation, 'say', say)
    session = cv.VoiceConversation(SimpleNamespace(transcribe=transcribe), submit,
                                  lambda: stopped.append(True), lambda text: None)
    monkeypatch.setattr(session, '_transcribe_turn', transcribe)
    asyncio.run(session.run())
    assert submitted == ['ช่วยตรวจงาน', 'ช่วยทำต่อ']
    assert len(stopped) == 2
    assert not session.active
    assert spoken[-1] == 'ปิดโหมดสนทนาแล้วครับ'


def test_reply_cancels_idle_mic_before_playback_and_returns_to_listening(monkeypatch):
    events = []
    async def record(on_start):
        events.append('mic-open')
        try:
            await asyncio.Event().wait()
        finally:
            events.append('mic-closed')
    async def say(self, text):
        if text == 'เสร็จแล้ว':
            assert events[-1] == 'mic-closed'
            events.append('reply')
    monkeypatch.setattr(cv, 'record_utterance', record)
    monkeypatch.setattr(cv.VoiceConversation, 'say', say)
    async def run():
        session = cv.VoiceConversation(None, None, None, lambda text: None)
        task = asyncio.create_task(session.run())
        for _ in range(5):
            await asyncio.sleep(0)
        session.reply('เสร็จแล้ว')
        for _ in range(20):
            await asyncio.sleep(0)
        assert events == ['mic-open', 'mic-closed', 'reply', 'mic-open']
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert events[-1] == 'mic-closed'
        assert not session.active
    asyncio.run(run())


def test_reply_arriving_during_transcription_is_not_lost(monkeypatch):
    spoken = []
    count = 0
    session = None
    async def record(on_start):
        nonlocal count
        count += 1
        if count == 1:
            return b'wav'
        await asyncio.Event().wait()
    async def transcribe(audio):
        session.reply('ผลลัพธ์จากงานก่อน')
        return 'ทำต่อ'
    async def say(self, text):
        spoken.append(text)
    async def submit(text):
        pass
    monkeypatch.setattr(cv, 'record_utterance', record)
    monkeypatch.setattr(cv.VoiceConversation, 'say', say)
    async def run():
        nonlocal session
        session = cv.VoiceConversation(SimpleNamespace(transcribe=transcribe), submit, lambda: None, lambda t: None)
        task = asyncio.create_task(session.run())
        for _ in range(40):
            await asyncio.sleep(0)
        assert 'ผลลัพธ์จากงานก่อน' in spoken
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


def test_record_cancellation_releases_hardware(monkeypatch):
    import sys
    events = []
    class Stream:
        def __init__(self, **kwargs):
            assert kwargs['blocksize'] == (1440 if cv.config.VOICE_NOISE_SUPPRESSION else 480)
        def __enter__(self):
            events.append('open')
        def __exit__(self, *args):
            events.append('close')
    monkeypatch.setitem(sys.modules, 'sounddevice', SimpleNamespace(RawInputStream=Stream))
    monkeypatch.setitem(sys.modules, 'webrtcvad', SimpleNamespace(Vad=lambda mode: SimpleNamespace(is_speech=lambda *args: False)))
    async def run():
        task = asyncio.create_task(cv.record_utterance())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert events == ['open', 'close']


def test_tui_startup_two_spoken_turns_without_keyboard(monkeypatch, tmp_path):
    from pathlib import Path
    from judecode.api import voice
    from judecode.ui import tui_app as tui
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(voice.config, 'OPENROUTER_API_KEY', 'test')
    monkeypatch.setattr(tui, 'player_command', lambda: ['fake'])
    monkeypatch.setattr(tui, 'check_microphone_support', lambda: None)
    async def run():
        audio = asyncio.Queue()
        answers = asyncio.Queue()
        submitted = []
        app = tui.JudeCodeTUI(voice_conversation=True)
        async def record(on_start):
            value = await audio.get()
            on_start()
            return value
        async def transcribe(data):
            return data
        async def say(self, text):
            await answers.put(text)
        async def chat(text, *, trusted_user=False):
            assert trusted_user is True
            submitted.append(text)
            app.agent.messages.append({'role': 'assistant', 'content': 'ผลลัพธ์ ' + str(len(submitted))})
        monkeypatch.setattr(cv, 'record_utterance', record)
        monkeypatch.setattr(cv.VoiceConversation, 'say', say)
        monkeypatch.setattr(app._voice_client, 'transcribe', transcribe)
        monkeypatch.setattr(app.agent, 'chat', chat)
        async def until(fragment):
            while True:
                text = await asyncio.wait_for(answers.get(), 3)
                if fragment in text:
                    return
        async with app.run_test():
            await until('เปิดโหมดสนทนา')
            for n in (1, 2):
                await audio.put('งาน ' + str(n))
                await until('ผลลัพธ์ ' + str(n))
            assert len(submitted) == 2
            assert submitted[0].startswith('งาน 1')
            assert 'โหมดสนทนาด้วยเสียง' in submitted[0]
            await audio.put('ปิดโหมดเสียง')
            await until('ปิดโหมดสนทนา')
            await asyncio.sleep(0)
            assert app._handsfree is None
    asyncio.run(run())


def test_long_reply_is_spoken_in_full(monkeypatch):
    chunks = []
    async def synthesize(text):
        chunks.append(text)
        return b'audio'
    async def play(audio):
        pass
    monkeypatch.setattr(cv, 'play_audio', play)
    session = cv.VoiceConversation(SimpleNamespace(synthesize=synthesize), None, None, lambda t: None)
    asyncio.run(session.say('ก' * 6000))
    assert ''.join(chunks) == 'ก' * 6000
    assert max(map(len, chunks)) <= 2500


def test_repeated_capture_failures_stop_without_submitting(monkeypatch):
    calls = []
    async def record(on_start):
        calls.append(True)
        raise cv.VoiceError('ไมค์หาย')
    async def say(self, text):
        pass
    monkeypatch.setattr(cv, 'record_utterance', record)
    monkeypatch.setattr(cv.VoiceConversation, 'say', say)
    session = cv.VoiceConversation(None, None, None, lambda t: None)
    with pytest.raises(cv.VoiceError, match='3 ครั้ง'):
        asyncio.run(session.run())
    assert len(calls) == 3
    assert not session.active


def test_default_tolerates_thinking_pause_then_speech_resumes():
    detector = cv.UtteranceDetector(lambda frame, rate: frame == b'v' * 960)
    for frame in [b'v' * 960] * 20 + [b'\0' * 960] * 60 + [b'v' * 960] * 20:
        assert detector.feed(frame) is None  # A 1.8s pause used to cut the request.
    for _ in range(69):
        assert detector.feed(b'\0' * 960) is None
    assert detector.feed(b'\0' * 960).startswith(b'RIFF')


def test_long_speech_is_not_submitted_at_old_30_second_limit():
    detector = cv.UtteranceDetector(lambda *args: True)
    for _ in range(1500):
        assert detector.feed(b'v' * 960) is None


def test_speech_during_transcription_is_merged_before_submission(monkeypatch):
    def wav(data):
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(data)
        return buf.getvalue()
    seen = []
    count = 0
    async def record(on_start):
        nonlocal count
        count += 1
        if count == 1:
            on_start()
            return wav(b'\x02\0' * 480)
        await asyncio.Event().wait()
    async def transcribe(audio):
        with wave.open(io.BytesIO(audio)) as w:
            seen.append(w.readframes(w.getnframes()))
        await asyncio.sleep(0)
        return 'แก้ระบบ แต่ยังไม่ต้องลบข้อมูล' if len(seen) == 2 else 'แก้ระบบ'
    monkeypatch.setattr(cv, 'record_utterance', record)
    session = cv.VoiceConversation(SimpleNamespace(transcribe=transcribe), None, None, lambda t: None)
    result = asyncio.run(session._transcribe_turn(wav(b'\x01\0' * 480)))
    assert result == 'แก้ระบบ แต่ยังไม่ต้องลบข้อมูล'
    assert seen == [b'\x01\0' * 480, b'\x01\0' * 480 + b'\x02\0' * 480]


def test_partial_transcription_cancellation_releases_followup_mic(monkeypatch):
    events = []
    async def record(on_start):
        events.append('open')
        try:
            await asyncio.Event().wait()
        finally:
            events.append('close')
    async def transcribe(audio):
        await asyncio.Event().wait()
    monkeypatch.setattr(cv, 'record_utterance', record)
    async def run():
        session = cv.VoiceConversation(SimpleNamespace(transcribe=transcribe), None, None, lambda t: None)
        task = asyncio.create_task(session._transcribe_turn(b'wav'))
        await asyncio.sleep(0); await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert events == ['open', 'close']


def test_speech_chunks_preserve_words_and_full_text():
    text = ('ตรวจสอบระบบภาษาไทย แล้วทำงานต่อครับ ' * 50).strip()
    chunks = list(cv.speech_chunks(text))
    assert ''.join(chunks) == text
    assert all(chunk.endswith(' ') for chunk in chunks[:-1])
    assert max(map(len, chunks)) <= 500
