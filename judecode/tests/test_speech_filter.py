"""Exercise real local denoising with deterministic non-speech (no microphone/API)."""
import io
import wave

import numpy as np
import pytest

from judecode.api.speech_filter import SpeechFilter, clean_recording
from judecode.api.conversation_voice import UtteranceDetector
from judecode.api.voice import VoiceError


def test_stationary_noise_is_attenuated_and_does_not_trigger_speech():
    import webrtcvad
    rng = np.random.default_rng(42)
    processor = SpeechFilter()
    detector = UtteranceDetector(webrtcvad.Vad(1).is_speech, sample_rate=48000)
    raw_energy, clean_energy = [], []
    try:
        for i in range(200):
            t = (np.arange(1440) + i * 1440) / 48000
            noisy = (800 * np.sin(2 * np.pi * 60 * t) + rng.normal(0, 180, 1440)).astype('<i2')
            clean, probability = processor.process(noisy.tobytes())
            assert len(clean) == 2880
            assert detector.feed(clean, probability) is None
            if i > 30:
                raw_energy.append(np.mean(noisy.astype(float) ** 2))
                clean_energy.append(np.mean(np.frombuffer(clean, dtype='<i2').astype(float) ** 2))
    finally:
        processor.close()
    assert np.mean(clean_energy) < np.mean(raw_energy) * 0.5
    assert not detector.started


def test_probability_gate_preserves_quiet_endings_and_48k_wav():
    detector = UtteranceDetector(lambda *args: True, sample_rate=48000, silence_ms=600)
    frame = bytes(2880)
    for _ in range(20):
        assert detector.feed(frame, 0.05) is None
    for _ in range(8):
        assert detector.feed(frame, 0.8) is None
    for _ in range(30):
        assert detector.feed(frame, 0.2) is None  # keep listening to weak endings
    for _ in range(19):
        assert detector.feed(frame, 0.01) is None
    output = detector.feed(frame, 0.01)
    with wave.open(io.BytesIO(output)) as wav:
        assert wav.getframerate() == 48000
        assert wav.getnchannels() == 1
        assert wav.getnframes() >= (8 + 30 + 20) * 1440


def test_short_transient_is_not_a_command():
    detector = UtteranceDetector(lambda *args: True, silence_ms=600)
    for probability in [0.9] * 6 + [0.0] * 25:
        assert detector.feed(bytes(960), probability) is None
    assert not detector.started


def test_manual_silent_recording_rejected_before_stt():
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
        wav.writeframes(bytes(32000))
    with pytest.raises(VoiceError, match='ไม่พบเสียงพูด'):
        clean_recording(buffer.getvalue())


def test_bad_filter_frame_does_not_fall_back_to_raw_audio():
    processor = SpeechFilter()
    try:
        with pytest.raises(VoiceError):
            processor.process(bytes(100))
    finally:
        processor.close()


def test_stt_upload_stays_16khz_and_same_duration():
    from judecode.api.speech_filter import stt_wav
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(48000)
        wav.writeframes(bytes(48000 * 2))
    with wave.open(io.BytesIO(stt_wav(buffer.getvalue()))) as wav:
        assert wav.getframerate() == 16000
        assert wav.getnframes() == 16000
        assert wav.getsampwidth() == 2


def test_missing_native_dependency_is_actionable(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, 'pyrnnoise', None)
    with pytest.raises(VoiceError, match='requirements-voice'):
        SpeechFilter()


def test_microphone_filter_closes_on_cancel(monkeypatch):
    import asyncio
    import sys
    from types import SimpleNamespace
    from judecode.api import conversation_voice as cv
    import judecode.api.speech_filter as filtering
    events = []
    class Processor:
        def __init__(self):
            events.append('filter-open')
        def close(self):
            events.append('filter-close')
    class Input:
        def __init__(self, **kwargs):
            assert kwargs['samplerate'] == 48000
        def __enter__(self):
            events.append('mic-open')
        def __exit__(self, *args):
            events.append('mic-close')
    monkeypatch.setattr(cv.config, 'VOICE_NOISE_SUPPRESSION', True)
    monkeypatch.setattr(cv, 'check_microphone_support', lambda: None)
    monkeypatch.setattr(filtering, 'SpeechFilter', Processor)
    monkeypatch.setitem(sys.modules, 'sounddevice', SimpleNamespace(RawInputStream=Input))
    async def run():
        task = asyncio.create_task(cv.record_utterance())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert events == ['filter-open', 'mic-open', 'mic-close', 'filter-close']
