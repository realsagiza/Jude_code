"""OpenRouter media generation and local movie assembly.

Paid POSTs are never retried automatically. Video receipts survive restarts.
Only our configured API origin receives the API key; downloads follow redirects
without credentials. Tool results contain paths/usage, never media base64.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import httpx

from judecode import config

MAX_MEDIA_BYTES = 256 * 1024 * 1024


class MediaError(Exception):
    """Safe, actionable error suitable for tool output."""


def _directory():
    root = Path(config.MEDIA_OUTPUT_DIR).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _save(data: bytes, suffix: str) -> str:
    if not data or len(data) > MAX_MEDIA_BYTES:
        raise MediaError('Media is empty or exceeds 256 MiB')
    target = _directory() / (uuid.uuid4().hex + suffix)
    with target.open('xb') as stream:
        stream.write(data)
    return str(target)


def _receipt(data: dict, path: Path | None = None) -> str:
    path = path or (_directory() / (uuid.uuid4().hex + '.json'))
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as f:
        temp = Path(f.name)
        json.dump(data, f, ensure_ascii=False, indent=2)
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    return str(path)


def _reference(value: str) -> dict:
    """Explicitly selected local image or public HTTPS reference."""
    if value.startswith('https://'):
        url = value
    else:
        path = Path(value).expanduser().resolve()
        mime = mimetypes.guess_type(path.name)[0]
        if mime not in ('image/png', 'image/jpeg', 'image/webp'):
            raise MediaError('References must be PNG, JPEG or WebP images')
        if not path.is_file() or not 0 < path.stat().st_size <= 20 * 1024 * 1024:
            raise MediaError('Reference image missing, empty or larger than 20 MiB')
        url = f'data:{mime};base64,' + base64.b64encode(path.read_bytes()).decode('ascii')
    return {'type': 'image_url', 'image_url': {'url': url}}


class MediaClient:
    def __init__(self, transport=None):
        self.base = config.OPENROUTER_BASE_URL.rstrip('/')
        self.client = httpx.Client(timeout=300, transport=transport, follow_redirects=False)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.client.close()

    def request(self, method, endpoint, payload=None):
        if not config.OPENROUTER_API_KEY:
            raise MediaError('Set JUDECODE_OPENROUTER_API_KEY or OPENROUTER_API_KEY first')
        try:
            response = self.client.request(method, self.base + endpoint,
                headers={'Authorization': 'Bearer ' + config.OPENROUTER_API_KEY}, json=payload)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            raise MediaError(f'OpenRouter media HTTP {exc.response.status_code}; check model, parameters, key and credits. Do not automatically resubmit paid requests.') from None
        except httpx.RequestError:
            raise MediaError('OpenRouter media connection failed/timed out. A submitted generation may still be billed; do not automatically resubmit.') from None

    def json(self, method, endpoint, payload=None):
        try:
            result = self.request(method, endpoint, payload).json()
            if not isinstance(result, dict) or (result.get('error') and 'status' not in result):
                raise ValueError()
            return result
        except (ValueError, TypeError):
            raise MediaError('OpenRouter returned an invalid response or provider error; do not automatically resubmit') from None

    def download_video(self, job_id, index=0):
        url = self.base + '/videos/' + quote(job_id, safe='') + f'/content?index={index}'
        headers = {'Authorization': 'Bearer ' + config.OPENROUTER_API_KEY}
        chunks, total = [], 0
        try:
            for _ in range(6):
                with self.client.stream('GET', url, headers=headers) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers['location'])
                        if urlsplit(url).scheme != 'https':
                            raise MediaError('Refusing non-HTTPS video redirect')
                        headers = {}  # Never forward key to a CDN, even on later redirects.
                        continue
                    response.raise_for_status()
                    kind = response.headers.get('content-type', '').split(';')[0]
                    if kind not in ('video/mp4', 'application/octet-stream'):
                        raise MediaError('Video endpoint did not return MP4 content')
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > MAX_MEDIA_BYTES:
                            raise MediaError('Video exceeds 256 MiB download limit')
                        chunks.append(chunk)
                    return _save(b''.join(chunks), '.mp4')
            raise MediaError('Too many video download redirects')
        except (httpx.HTTPError, KeyError):
            raise MediaError('Video download failed; retry media_video_status with the existing job ID') from None


def media_models(kind: str, query: str = '') -> dict:
    endpoints = {'image': '/images/models', 'video': '/videos/models', 'audio': '/models?output_modalities=speech'}
    if kind not in endpoints:
        raise MediaError('kind must be image, video or audio')
    with MediaClient() as client:
        rows = client.json('GET', endpoints[kind]).get('data', [])
    selected = [r for r in rows if query.lower() in (r.get('id', '') + ' ' + r.get('name', '')).lower()]
    fields = ('id', 'name', 'supported_parameters', 'supported_durations', 'supported_resolutions',
              'supported_aspect_ratios', 'generate_audio', 'pricing', 'pricing_skus', 'endpoints',
              'supported_voices', 'supported_languages')
    return {'models': [{k: r[k] for k in fields if k in r} for r in selected[:30]],
            'matches': len(selected), 'hint': 'Refine query if truncated; choose a model and supported options before generating.'}


def media_image(prompt: str, model: str = '', aspect_ratio: str = '', references: list[str] | None = None) -> dict:
    model = model or config.MEDIA_IMAGE_MODEL
    if not model or not prompt.strip():
        raise MediaError('Provide prompt and model; discover models with media_models(kind="image")')
    payload = {'model': model, 'prompt': prompt, 'n': 1}
    if aspect_ratio:
        payload['aspect_ratio'] = aspect_ratio
    if references:
        payload['input_references'] = [_reference(p) for p in references]
    with MediaClient() as client:
        result = client.json('POST', '/images', payload)
    paths = []
    for item in result.get('data', []):
        try:
            data = base64.b64decode(item['b64_json'], validate=True)
            # Verify actual format rather than trust the response MIME or filename.
            import io
            from PIL import Image
            with Image.open(io.BytesIO(data)) as img:
                suffix = {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp'}[img.format]
                img.verify()
            paths.append(_save(data, suffix))
        except (ValueError, KeyError, OSError):
            raise MediaError('Image response missing valid PNG/JPEG/WebP data') from None
    if not paths:
        raise MediaError('No image returned (possibly filtered); no image was saved')
    record = {'kind': 'image', 'model': model, 'files': paths, 'usage': result.get('usage', {})}
    return {**record, 'receipt': _receipt(record)}


def media_audio(text: str, model: str = '', voice: str = '', instructions: str = '') -> dict:
    if not text.strip():
        raise MediaError('Audio text/prompt is empty')
    model = model or config.MEDIA_AUDIO_MODEL or config.VOICE_TTS_MODEL
    # A custom model must use its own voice rather than the unrelated default.
    voice = voice or (config.VOICE_NAME if model == config.VOICE_TTS_MODEL else '')
    payload = {'model': model, 'input': text, 'response_format': 'mp3'}
    if voice:
        payload['voice'] = voice
    if instructions:
        payload['instructions'] = instructions
    with MediaClient() as client:
        response = client.request('POST', '/audio/speech', payload)
    if response.headers.get('content-type', '').split(';')[0] not in ('audio/mpeg', 'audio/mp3', 'application/octet-stream'):
        raise MediaError('Speech endpoint did not return MP3 audio')
    data = response.content
    if not (data.startswith(b'ID3') or (len(data) > 1 and data[0] == 255 and data[1] & 224 == 224)):
        raise MediaError('Speech endpoint returned invalid MP3 data')
    record = {'kind': 'audio', 'model': model, 'files': [_save(data, '.mp3')], 'usage': 'Check OpenRouter activity for audio cost'}
    return {**record, 'receipt': _receipt(record)}


def media_video(prompt: str, model: str = '', duration: int | None = None,
                aspect_ratio: str = '', resolution: str = '', generate_audio: bool | None = None,
                first_frame: str = '') -> dict:
    model = model or config.MEDIA_VIDEO_MODEL
    if not model or not prompt.strip():
        raise MediaError('Provide prompt and model; discover models with media_models(kind="video")')
    if model == 'x-ai/grok-imagine-video-1.5-lite':
        resolution = resolution or '480p'
        duration = 5 if duration is None else duration
        generate_audio = False if generate_audio is None else generate_audio
    payload = {'model': model, 'prompt': prompt}
    if duration is not None:
        if type(duration) is not int or not 1 <= duration <= 120:
            raise MediaError('duration must be an integer between 1 and 120 seconds supported by the model')
        payload['duration'] = duration
    for key, value in [('aspect_ratio', aspect_ratio), ('resolution', resolution), ('generate_audio', generate_audio)]:
        if value is not None and value != '':
            payload[key] = value
    if first_frame:
        payload['frame_images'] = [{**_reference(first_frame), 'frame_type': 'first_frame'}]
    # Preflight writable storage BEFORE spending credits.
    receipt = Path(_receipt({'kind': 'video', 'model': model, 'status': 'submitting'}))
    with MediaClient() as client:
        result = client.json('POST', '/videos', payload)
    job_id = result.get('id')
    if not isinstance(job_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', job_id):
        raise MediaError('Video submission returned no valid job ID; check OpenRouter activity before retrying')
    record = {'kind': 'video', 'model': model, 'job_id': job_id,
              'status': result.get('status', 'pending'), 'usage': result.get('usage', {})}
    try:
        _receipt(record, receipt)
    except OSError:
        raise MediaError(f'Video submitted: job_id={job_id}; could not save receipt. Poll this ID; do not resubmit.') from None
    return {**record, 'receipt': str(receipt), 'next': 'Use media_video_status after about 30 seconds. Do not submit again.'}


def media_video_status(job_id: str, download: bool = True) -> dict:
    if not re.fullmatch(r'[A-Za-z0-9_-]+', job_id):
        raise MediaError('Invalid video job ID')
    receipt = None
    record = {}
    for path in _directory().glob('*.json'):
        try:
            candidate = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(candidate, dict) and candidate.get('job_id') == job_id:
                receipt, record = path, candidate
                break
        except (ValueError, OSError):
            continue
    if record.get('files') and all(Path(p).is_file() for p in record['files']):
        return {**record, 'receipt': str(receipt)}
    with MediaClient() as client:
        result = client.json('GET', '/videos/' + quote(job_id, safe=''))
        record.update(kind='video', job_id=job_id, status=result.get('status', 'unknown'), usage=result.get('usage', {}))
        if result.get('status') == 'completed' and download:
            record['files'] = [client.download_video(job_id)]
        elif result.get('status') in ('failed', 'cancelled', 'expired'):
            record['message'] = 'Generation did not complete; check OpenRouter activity. No automatic resubmission.'
    return {**record, 'receipt': _receipt(record, receipt)}


def _ffmpeg():
    binary = shutil.which('ffmpeg')
    if binary:
        return binary
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        raise MediaError('Install FFmpeg or run: python -m pip install -r requirements-media.txt') from None


def _run_ffmpeg(binary, args):
    try:
        result = subprocess.run([binary, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', *args],
                                capture_output=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise MediaError('Movie render timed out after 10 minutes') from None
    if result.returncode:
        raise MediaError('FFmpeg could not render the movie; check that each input is a valid local image/video/audio file')


def media_movie(scenes: list[dict], narration: str = '', width: int = 1280, height: int = 720) -> dict:
    """Normalize scenes, concatenate and optionally mix a narration track."""
    if not scenes or len(scenes) > 30:
        raise MediaError('Provide 1–30 scenes with path and duration (seconds)')
    if any(type(n) is not int or n < 128 or n > 3840 or n % 2 for n in (width, height)):
        raise MediaError('Movie width/height must be even integers between 128 and 3840')
    prepared = []
    for scene in scenes:
        path = Path(scene['path']).expanduser().resolve()
        duration = scene.get('duration', 5)
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 0.1 <= duration <= 120:
            raise MediaError('Scene duration must be 0.1–120 seconds')
        if not path.is_file() or path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp', '.mp4', '.mov', '.mkv', '.webm'):
            raise MediaError('Scene must be an existing local image or video file')
        prepared.append((path, duration))
    voice = Path(narration).expanduser().resolve() if narration else None
    if voice and not voice.is_file():
        raise MediaError('Narration audio file not found')
    binary = _ffmpeg()
    target = _directory() / (uuid.uuid4().hex + '.mp4')
    with tempfile.TemporaryDirectory(prefix='jude-movie-') as temp:
        folder = Path(temp)
        for i, (path, duration) in enumerate(prepared):
            still = path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp')
            # FFmpeg's demuxer reports stream types; avoid an extra ffprobe dependency.
            probe = subprocess.run([binary, '-nostdin', '-hide_banner', '-i', str(path)], capture_output=True, timeout=30)
            has_audio = not still and b'Audio:' in probe.stderr
            args = (['-loop', '1'] if still else []) + ['-i', str(path)]
            if not has_audio:
                args += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
            args += ['-map', '0:v:0', '-map', '0:a:0' if has_audio else '1:a:0',
                     '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,tpad=stop_mode=clone:stop_duration={duration}',
                     '-af', 'apad', '-t', str(duration), '-r', '24', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                     '-c:a', 'aac', '-ar', '48000', '-ac', '2', str(folder / f'{i}.mp4')]
            _run_ffmpeg(binary, args)
        playlist = folder / 'scenes.txt'
        playlist.write_text(''.join(f"file '{i}.mp4'\n" for i in range(len(prepared))), encoding='utf-8')
        assembled = folder / 'assembled.mp4'
        _run_ffmpeg(binary, ['-f', 'concat', '-safe', '1', '-i', str(playlist), '-c', 'copy', str(assembled)])
        output = folder / 'final.mp4'
        args = ['-i', str(assembled)]
        if voice:
            args += ['-i', str(voice), '-filter_complex', '[0:a][1:a]amix=inputs=2:duration=first:normalize=0[a]', '-map', '0:v', '-map', '[a]', '-c:a', 'aac']
        else:
            args += ['-map', '0', '-c:a', 'copy']
        _run_ffmpeg(binary, args + ['-c:v', 'copy', '-movflags', '+faststart', str(output)])
        shutil.copyfile(output, target)
    record = {'kind': 'movie', 'files': [str(target)], 'scenes': len(scenes),
              'duration_seconds': sum(d for _, d in prepared), 'resolution': f'{width}x{height}'}
    return {**record, 'receipt': _receipt(record)}


def _tool(name, description, properties, required):
    return {'type': 'function', 'function': {'name': name, 'description': description,
            'parameters': {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}}}


_STRING = {'type': 'string'}
MEDIA_TOOL_DEFINITIONS = [
    _tool('media_models', 'Discover current OpenRouter media models, supported options and available pricing. Call before choosing a model.',
          {'kind': {'type': 'string', 'enum': ['image', 'video', 'audio']}, 'query': _STRING}, ['kind']),
    _tool('media_image', 'Generate/edit one image via OpenRouter (paid). Saves image and receipt, returns local paths. Choose model using media_models. Never retry a failed paid submission automatically.',
          {'prompt': _STRING, 'model': _STRING, 'aspect_ratio': _STRING, 'references': {'type': 'array', 'items': _STRING}}, ['prompt']),
    _tool('media_audio', 'Generate MP3 narration/voice via OpenRouter (paid). text is spoken verbatim. Non-speech sounds only with a model documented to support them. Custom models need compatible voice. Never retry automatically.',
          {'text': _STRING, 'model': _STRING, 'voice': _STRING, 'instructions': _STRING}, ['text']),
    _tool('media_video', 'Submit ONE paid OpenRouter video job; returns job_id and durable receipt, NOT a finished clip. Use media_video_status to resume; never resubmit to poll. Select supported options with media_models. first_frame is a local image or HTTPS URL.',
          {'prompt': _STRING, 'model': _STRING, 'duration': {'type': 'integer'}, 'aspect_ratio': _STRING,
           'resolution': _STRING, 'generate_audio': {'type': 'boolean'}, 'first_frame': _STRING}, ['prompt']),
    _tool('media_video_status', 'Check an existing video job once and download completed MP4. Wait about 30 seconds between checks. Reuse job_id across restarts. Terminal failures must be reported, not automatically resubmitted.',
          {'job_id': _STRING, 'download': {'type': 'boolean'}}, ['job_id']),
    _tool('media_movie', 'Assemble a local MP4 film from ordered images/clips; preserves scene audio and optionally mixes narration. Create scenes with media_image/media_video, download clips first. Each scene needs path and duration in seconds; clips are trimmed or last frame held. Requires FFmpeg. No API charge.',
          {'scenes': {'type': 'array', 'items': {'type': 'object', 'properties': {'path': _STRING, 'duration': {'type': 'number'}}, 'required': ['path', 'duration']}},
           'narration': _STRING, 'width': {'type': 'integer'}, 'height': {'type': 'integer'}}, ['scenes']),
]
MEDIA_FUNCTIONS = {item['function']['name']: globals()[item['function']['name']] for item in MEDIA_TOOL_DEFINITIONS}


def execute_media_tool(name, params):
    try:
        # Fail before a paid call if local output storage is unavailable.
        if name != 'media_models':
            with tempfile.TemporaryFile(dir=_directory()):
                pass
        return json.dumps(MEDIA_FUNCTIONS[name](**params), ensure_ascii=False)
    except (MediaError, OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as exc:
        message = str(exc) if isinstance(exc, MediaError) else 'Invalid media arguments, response, or local file operation failed'
        return f"Error executing tool '{name}': {message}"
