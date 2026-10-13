"""Hands-free, turn-taking conversation with local speech detection."""
import asyncio
from collections import deque
import io
import re
import wave
import math
import time

from judecode import config
from judecode.api.voice import VoiceError, play_audio, play_pcm_stream, spoken_text


class UtteranceDetector:
    """30 ms frames; tolerate thinking pauses, never submit a capped fragment."""
    def __init__(self, classify, *, silence_ms=None, max_seconds=None):
        self.classify = classify
        self.end_frames = math.ceil(max(600, min(5000, silence_ms if silence_ms is not None else config.VOICE_END_SILENCE_MS)) / 30)
        self.max_frames = math.ceil(max(10, min(120, max_seconds if max_seconds is not None else config.VOICE_MAX_UTTERANCE_SECONDS)) * 1000 / 30)
        self.pre = deque(maxlen=10)
        self.frames = []
        self.silent = 0
        self.started = False

    def feed(self, frame):
        voiced = self.classify(frame, 16000)
        if not self.started:
            self.pre.append((frame, voiced))
            if sum(v for _, v in self.pre) < 6:
                return None
            self.started = True
            self.frames = [f for f, _ in self.pre]
        else:
            self.frames.append(frame)
        self.silent = 0 if voiced else self.silent + 1
        if len(self.frames) >= self.max_frames:
            raise VoiceError('พูดยาวเกินช่วงรับเสียง ยังไม่ได้ส่งคำสั่งครับ กรุณาแบ่งคำสั่งแล้วพูดใหม่')
        if self.silent >= self.end_frames:
            output = io.BytesIO()
            with wave.open(output, 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(b''.join(self.frames))
            return output.getvalue()
        return None


def check_microphone_support():
    try:
        import sounddevice
        import webrtcvad
    except (ImportError, OSError):
        raise VoiceError('ติดตั้งโหมดสนทนา: python -m pip install sounddevice webrtcvad-wheels') from None


async def record_utterance(on_start=lambda: None):
    """Wait locally for speech. Silence never invokes the transcription API."""
    check_microphone_support()
    import sounddevice as sd
    import webrtcvad
    detector = UtteranceDetector(webrtcvad.Vad(max(0, min(3, config.VOICE_VAD_MODE))).is_speech)
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue(maxsize=100)
    active = True
    overflow = False

    def deliver(data, bad):
        nonlocal overflow
        if not active:
            return
        if bad or queue.full():
            overflow = True
        else:
            queue.put_nowait(data)

    def callback(data, frames, time, status):
        loop.call_soon_threadsafe(deliver, bytes(data), bool(status))

    try:
        with sd.RawInputStream(samplerate=16000, channels=1, dtype='int16',
                               blocksize=480, callback=callback):
            while True:
                frame = await asyncio.wait_for(queue.get(), timeout=3)
                if overflow:
                    raise VoiceError('เสียงไมค์ขาดช่วง กรุณาตรวจอุปกรณ์เสียง')
                was_started = detector.started
                result = detector.feed(frame)
                if detector.started and not was_started:
                    on_start()
                if result:
                    return result
    except VoiceError:
        raise
    except Exception:
        raise VoiceError('เปิดหรือรับเสียงไมค์ไม่ได้ ตรวจสิทธิ์ Microphone และอุปกรณ์เสียง') from None
    finally:
        active = False


def voice_command(text):
    normalized = re.sub(r'[\s.,!?。ๆ]', '', text.lower())
    # Match the entire utterance: quoted/embedded commands stay ordinary messages.
    for suffix in ('ครับ', 'ค่ะ', 'คะ', 'นะ'):
        if normalized.endswith(suffix):
            normalized = normalized[:-len(suffix)]
    if normalized in ('ปิดโหมดเสียง', 'ปิดระบบเสียง', 'เลิกคุย', 'จบการสนทนา', 'voiceoff'):
        return 'off'
    if normalized in ('หยุด', 'หยุดก่อน', 'หยุดทำงาน', 'หยุดงาน', 'พักก่อน', 'stop'):
        return 'stop'
    return None


def speech_chunks(text):
    """Prefer phrase boundaries instead of cutting Thai words at character 500."""
    while text:
        if len(text) <= 500:
            yield text
            return
        end = text.rfind(' ', 0, 501)
        if end < 100:
            end = text.find(' ', 500, 2500)
        end = end + 1 if end >= 100 else min(2500, len(text))
        yield text[:end]
        text = text[end:]


class VoiceConversation:
    """One microphone owner; never listen during playback (prevents self-trigger)."""
    def __init__(self, client, submit, stop, status):
        self.client, self.submit, self.stop, self.status = client, submit, stop, status
        self.replies = asyncio.Queue()
        self.active = True
        self.timings = {}

    def reply(self, text):
        if self.active and text:
            self.replies.put_nowait(text)

    async def say(self, text):
        self.status('🔊 Jude กำลังพูด')
        prose = spoken_text(text, limit=None)
        # Speak all prose, including long answers, without requiring screen reading.
        # Short opening chunk reduces time to first audio for long answers.
        for chunk in speech_chunks(prose):
            if hasattr(self.client, 'stream_speech'):
                began = time.monotonic()
                def first_audio():
                    self.timings['tts_first_audio_seconds'] = round(time.monotonic() - began, 3)
                await play_pcm_stream(self.client.stream_speech(chunk), config.VOICE_SAMPLE_RATE, first_audio)
            else:
                await play_audio(await self.client.synthesize(chunk))
        await asyncio.sleep(0.3)  # Let speaker reverberation settle before reopening the mic.

    async def _transcribe_turn(self, audio):
        """Keep listening during STT; resumed speech extends this same command."""
        while True:
            resumed = asyncio.Event()
            followup = asyncio.create_task(record_utterance(resumed.set))
            try:
                began = time.monotonic()
                text = await self.client.transcribe(audio)
                self.timings['stt_seconds'] = round(time.monotonic() - began, 3)
                if not resumed.is_set() and not followup.done():
                    return text
                extra = await followup
                # Re-transcribe the complete audio, preserving negations/context.
                frames = []
                for part in (audio, extra):
                    with wave.open(io.BytesIO(part)) as wav:
                        frames.append(wav.readframes(wav.getnframes()))
                if sum(map(len, frames)) > 16000 * 2 * 120:
                    raise VoiceError('คำสั่งยาวเกินสองนาที ยังไม่ได้ส่ง กรุณาแบ่งคำสั่งใหม่ครับ')
                merged = io.BytesIO()
                with wave.open(merged, 'wb') as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(16000)
                    wav.writeframes(b''.join(frames))
                audio = merged.getvalue()
                self.status('🎙 ได้ยินว่าพูดต่อ กำลังรวมคำสั่งให้ครบ')
            finally:
                followup.cancel()
                await asyncio.gather(followup, return_exceptions=True)

    async def run(self):
        failures = 0
        try:
            await self.say('เปิดโหมดสนทนาแล้วครับ พูดสั่งงานได้เลย หยุดพูดสักครู่เพื่อส่งคำสั่ง พูดว่าหยุดงานเพื่อพักงาน หรือปิดโหมดเสียงเพื่อจบครับ')
            while self.active:
                if not self.replies.empty():
                    await self.say(await self.replies.get())
                    continue
                self.status('🎙 กำลังฟัง — พูดได้เลย')
                speaking = asyncio.Event()
                capture = asyncio.create_task(record_utterance(speaking.set))
                reply = asyncio.create_task(self.replies.get())
                try:
                    done, _ = await asyncio.wait((capture, reply), return_when=asyncio.FIRST_COMPLETED)
                    if reply in done:
                        # Finish an utterance already underway before speaking over the user.
                        if speaking.is_set() or capture.done():
                            audio = await capture
                            self.replies.put_nowait(reply.result())
                        else:
                            capture.cancel()
                            await asyncio.gather(capture, return_exceptions=True)
                            await self.say(reply.result())
                            continue
                    else:
                        audio = capture.result()
                        reply.cancel()
                        await asyncio.gather(reply, return_exceptions=True)
                        if not reply.cancelled():
                            self.replies.put_nowait(reply.result())
                    self.status('กำลังฟังความหมาย…')
                    text = await self._transcribe_turn(audio)
                    failures = 0
                    command = voice_command(text)
                    if command == 'off':
                        self.stop()
                        self.active = False
                        await self.say('ปิดโหมดสนทนาแล้วครับ')
                    elif command == 'stop':
                        self.stop()
                        while not self.replies.empty():
                            self.replies.get_nowait()
                        await self.say('ขอหยุดงานแล้วครับ บอกคำสั่งใหม่ได้เลย')
                    else:
                        await self.submit(text)
                        self.status('รับคำสั่งแล้ว กำลังดำเนินการ…')
                except VoiceError as exc:
                    failures += 1
                    self.status(str(exc))
                    if failures >= 3:
                        raise VoiceError('โหมดสนทนาหยุดหลังเกิดข้อผิดพลาด 3 ครั้ง กรุณาตรวจไมค์หรือ OpenRouter') from exc
                    await self.say('รับเสียงไม่สำเร็จครับ ' + str(exc))
                    await asyncio.sleep(1)
                finally:
                    for task in (capture, reply):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(capture, reply, return_exceptions=True)
        finally:
            self.active = False
