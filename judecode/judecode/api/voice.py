"""Thai speech via OpenRouter; optional local microphone and playback support."""
from __future__ import annotations

import asyncio
import base64
import io
import re
import shutil
import tempfile
import wave
from pathlib import Path

import httpx

from judecode import config


class VoiceError(Exception):
    """A user-facing voice failure (never includes credentials or HTTP bodies)."""


def spoken_text(text: str, limit: int | None = 3000) -> str:
    """Read prose rather than fenced code, URLs, and Markdown decoration."""
    text = re.sub(r"```.*?(?:```|\Z)", " โค้ดแสดงอยู่ในหน้าจอ ", text, flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*#`>|]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if limit is not None and len(text) > limit:
        text = text[:limit] + " อ่านรายละเอียดที่เหลือได้ในหน้าจอครับ"
    return text


def final_reply(messages: list[dict]) -> str:
    """Only speak a terminal assistant answer, never tool output or old turns."""
    if not messages:
        return ""
    last = messages[-1]
    if last.get("role") != "assistant" or last.get("tool_calls"):
        return ""
    content = last.get("content")
    return content if isinstance(content, str) else ""


class VoiceClient:
    def __init__(self, *, transport=None):
        self._client = httpx.AsyncClient(timeout=90, transport=transport)

    def check_key(self):
        if not config.OPENROUTER_API_KEY:
            raise VoiceError("ตั้งค่า JUDECODE_OPENROUTER_API_KEY หรือ OPENROUTER_API_KEY ก่อนใช้เสียง")

    async def close(self):
        await self._client.aclose()

    async def _post(self, endpoint, payload):
        self.check_key()
        try:
            response = await self._client.post(
                config.OPENROUTER_BASE_URL.rstrip('/') + endpoint,
                headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"},
                json=payload,
            )
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            raise VoiceError(f"OpenRouter เสียง: HTTP {exc.response.status_code} — ตรวจเครดิต คีย์ และชื่อโมเดล") from None
        except httpx.RequestError:
            raise VoiceError("เชื่อมต่อ OpenRouter เสียงไม่ได้หรือหมดเวลา กรุณาลองใหม่") from None

    async def transcribe(self, audio: bytes) -> str:
        if not audio or len(audio) > 25 * 1024 * 1024:
            raise VoiceError("เสียงว่างหรือใหญ่เกิน 25 MB")
        payload = {
            'model': config.VOICE_STT_MODEL,
            'input_audio': {'data': base64.b64encode(audio).decode('ascii'), 'format': 'wav'},
            'language': 'th',
        }
        if config.VOICE_STT_MODEL.startswith('openai/') and config.VOICE_STT_PROMPT:
            payload['provider'] = {'options': {'openai': {'prompt': config.VOICE_STT_PROMPT}}}
        response = await self._post('/audio/transcriptions', payload)
        try:
            text = response.json()['text']
        except (ValueError, KeyError, TypeError):
            raise VoiceError("OpenRouter ส่งผลถอดเสียงที่ไม่ถูกต้อง") from None
        if not isinstance(text, str) or not text.strip():
            raise VoiceError("ไม่ได้ยินคำพูด กรุณาลองอีกครั้ง")
        return text.strip()

    async def stream_speech(self, text: str):
        """Yield mono int16 PCM as it arrives, without buffering a whole reply."""
        text = spoken_text(text)
        if not text:
            return
        self.check_key()
        if not 8000 <= config.VOICE_SAMPLE_RATE <= 48000:
            raise VoiceError("VOICE_SAMPLE_RATE ต้องอยู่ระหว่าง 8000–48000 Hz")
        received = 0
        try:
            async with self._client.stream(
                'POST', config.OPENROUTER_BASE_URL.rstrip('/') + '/audio/speech',
                headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"},
                json={'model': config.VOICE_TTS_MODEL, 'input': text,
                      'voice': config.VOICE_NAME, 'response_format': 'pcm'},
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get('content-type', '').lower().split(';')[0]
                if content_type not in ('audio/pcm', 'audio/l16', 'application/octet-stream'):
                    raise VoiceError("OpenRouter ไม่ได้ส่งข้อมูลเสียง PCM กลับมา")
                async for chunk in response.aiter_bytes(chunk_size=4800):
                    received += len(chunk)
                    if len(chunk) % 2:
                        raise VoiceError("ข้อมูลเสียง PCM ไม่ครบ กรุณาลองใหม่")
                    if chunk:
                        yield chunk
        except httpx.HTTPStatusError as exc:
            raise VoiceError(f"OpenRouter เสียง: HTTP {exc.response.status_code} — ตรวจเครดิต คีย์ และชื่อโมเดล") from None
        except httpx.RequestError:
            raise VoiceError("เชื่อมต่อ OpenRouter เสียงไม่ได้หรือหมดเวลา กรุณาลองใหม่") from None
        if not received:
            raise VoiceError("OpenRouter ไม่ได้ส่งไฟล์เสียงกลับมา")

    async def synthesize(self, text: str) -> bytes:
        chunks = [chunk async for chunk in self.stream_speech(text)]
        if not chunks:
            return b''
        output = io.BytesIO()
        with wave.open(output, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(config.VOICE_SAMPLE_RATE)
            wav.writeframes(b''.join(chunks))
        return output.getvalue()


async def play_pcm_stream(chunks, sample_rate, on_first_audio=lambda: None):
    """Play small network chunks immediately; cancellation closes both resources."""
    try:
        import sounddevice as sd
    except (ImportError, OSError):
        raise VoiceError('ติดตั้ง sounddevice เพื่อเล่นเสียงแบบสตรีม') from None
    first = True
    try:
        with sd.RawOutputStream(samplerate=sample_rate, channels=1, dtype='int16') as output:
            async for chunk in chunks:
                if first:
                    on_first_audio()
                    first = False
                # Each write is <=100 ms at 24 kHz. Keep the UI loop responsive.
                write = asyncio.create_task(asyncio.to_thread(output.write, chunk))
                try:
                    await asyncio.shield(write)
                except asyncio.CancelledError:
                    output.abort()
                    await asyncio.gather(write, return_exceptions=True)
                    raise
    except VoiceError:
        raise
    except Exception:
        raise VoiceError('เล่นเสียงสตรีมไม่ได้ ตรวจอุปกรณ์ลำโพงและ sample rate') from None
    finally:
        await chunks.aclose()


async def record_wav(seconds: int) -> bytes:
    """Capture only on explicit request. Cancellation always releases the mic."""
    if not 1 <= seconds <= 60:
        raise VoiceError("ระยะเวลาอัดต้องอยู่ระหว่าง 1–60 วินาที")
    try:
        import sounddevice as sd
    except (ImportError, OSError):
        raise VoiceError("ติดตั้งไมค์: python -m pip install sounddevice (Linux ต้องมี PortAudio)") from None
    chunks = []
    errors = []
    max_bytes = seconds * 16000 * 2
    captured = 0

    def callback(data, frames, time, status):
        nonlocal captured
        if status:
            errors.append(str(status))
        chunk = bytes(data)[:max(0, max_bytes - captured)]
        chunks.append(chunk)
        captured += len(chunk)

    try:
        with sd.RawInputStream(samplerate=16000, channels=1, dtype='int16', callback=callback):
            await asyncio.sleep(seconds)
    except Exception:
        raise VoiceError("เปิดไมค์ไม่ได้ ตรวจสิทธิ์ Microphone ของ Terminal และอุปกรณ์เสียง") from None
    if errors or not captured:
        raise VoiceError("รับเสียงไม่ครบหรือไมค์ไม่มีข้อมูล กรุณาลองใหม่")
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b''.join(chunks))
    if config.VOICE_NOISE_SUPPRESSION:
        from judecode.api.speech_filter import clean_recording
        return await asyncio.to_thread(clean_recording, output.getvalue())
    return output.getvalue()


def player_command() -> list[str]:
    if shutil.which('afplay'):
        return ['afplay']
    if shutil.which('ffplay'):
        return ['ffplay', '-nodisp', '-autoexit', '-loglevel', 'quiet']
    raise VoiceError("ไม่พบตัวเล่นเสียง: macOS ใช้ afplay; Windows/Linux ติดตั้ง FFmpeg ให้มี ffplay")


async def play_audio(audio: bytes):
    if not audio:
        return
    command = player_command()
    # The private temporary directory is removed on success, failure and cancellation.
    with tempfile.TemporaryDirectory(prefix='judecode-voice-') as directory:
        path = Path(directory) / 'reply.wav'
        path.write_bytes(audio)
        try:
            process = await asyncio.create_subprocess_exec(
                *command, str(path), stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError:
            raise VoiceError("เปิดโปรแกรมเล่นเสียงไม่สำเร็จ") from None
        try:
            code = await process.wait()
            if code:
                raise VoiceError("เล่นเสียงไม่สำเร็จ ตรวจอุปกรณ์เสียง")
        finally:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
