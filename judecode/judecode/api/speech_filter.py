"""Local RNNoise suppression; preserves speech samples rather than hard-muting them."""
import io
import wave

from judecode.api.voice import VoiceError


class SpeechFilter:
    sample_rate = 48000
    block_samples = 1440  # Three RNNoise 10 ms frames, one WebRTC VAD frame.

    def __init__(self):
        try:
            import numpy as np
            from pyrnnoise import RNNoise
            self.np = np
            self.model = RNNoise(sample_rate=self.sample_rate)
        except (ImportError, OSError, RuntimeError):
            raise VoiceError('เปิดลดเสียงรบกวนไม่ได้ ติดตั้ง: python -m pip install -r requirements-voice.txt') from None

    def process(self, frame):
        if len(frame) != self.block_samples * 2:
            raise VoiceError('ขนาดข้อมูลไมค์ไม่ตรงกับตัวลดเสียงรบกวน')
        data = self.np.frombuffer(frame, dtype='<i2').reshape(1, -1)
        try:
            parts = list(self.model.denoise_chunk(data))
            audio = self.np.concatenate([part[1] for part in parts], axis=1)
            probability = float(self.np.mean([float(self.np.asarray(part[0]).mean()) for part in parts]))
            if audio.size != self.block_samples:
                raise ValueError('Unexpected filter frame size')
            return audio.astype('<i2').tobytes(), probability
        except Exception:
            raise VoiceError('ตัวลดเสียงรบกวนประมวลผลไม่สำเร็จ ยังไม่ส่งเสียงไปถอดข้อความ') from None

    def close(self):
        self.model.reset()


def clean_recording(audio):
    """Clean /listen recordings too; reject clips with no sustained speech."""
    try:
        import numpy as np
        import soxr
        import webrtcvad
    except (ImportError, OSError):
        raise VoiceError('ติดตั้งส่วนลดเสียงรบกวน: python -m pip install -r requirements-voice.txt') from None
    with wave.open(io.BytesIO(audio)) as wav:
        rate = wav.getframerate()
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2')
    samples = soxr.resample(samples, rate, 48000).astype('<i2') if rate != 48000 else samples
    processor = SpeechFilter()
    vad = webrtcvad.Vad(1)
    chunks = []
    speech_frames = 0
    try:
        for offset in range(0, len(samples), 1440):
            block = samples[offset:offset + 1440]
            padded = np.pad(block, (0, 1440 - len(block)))
            clean, probability = processor.process(padded.astype('<i2').tobytes())
            if probability >= 0.35 and vad.is_speech(clean, 48000):
                speech_frames += 1
            chunks.append(clean[:len(block) * 2])
    finally:
        processor.close()
    if speech_frames < 8:
        raise VoiceError('ไม่พบเสียงพูดต่อเนื่อง จึงไม่ได้ส่งไป OpenRouter กรุณาพูดใหม่ครับ')
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(b''.join(chunks))
    return stt_wav(output.getvalue())


def stt_wav(audio):
    """Keep STT uploads at 16 kHz even though RNNoise operates at 48 kHz."""
    import numpy as np
    import soxr
    with wave.open(io.BytesIO(audio)) as wav:
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2')
        rate = wav.getframerate()
    if rate == 16000:
        return audio
    samples = soxr.resample(samples, rate, 16000).astype('<i2')
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(samples.tobytes())
    return output.getvalue()
