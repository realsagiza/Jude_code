"""Media contracts, durable video jobs, safe downloads and a real local film."""
import base64
import io
import json
import subprocess
from pathlib import Path

import httpx
import pytest
from PIL import Image

from judecode import config
from judecode.utils import media_tools as media


@pytest.fixture(autouse=True)
def settings(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'OPENROUTER_API_KEY', 'test-secret')
    monkeypatch.setattr(config, 'OPENROUTER_BASE_URL', 'https://openrouter.ai/api/v1')
    monkeypatch.setattr(config, 'MEDIA_OUTPUT_DIR', str(tmp_path / 'media'))
    monkeypatch.setattr(config, 'MEDIA_IMAGE_MODEL', '')
    monkeypatch.setattr(config, 'MEDIA_VIDEO_MODEL', '')
    monkeypatch.setattr(config, 'MEDIA_AUDIO_MODEL', '')


def transport(monkeypatch, handler):
    original = media.MediaClient
    monkeypatch.setattr(media, 'MediaClient', lambda: original(transport=httpx.MockTransport(handler)))


def png():
    out = io.BytesIO()
    Image.new('RGB', (32, 32), 'blue').save(out, format='PNG')
    return out.getvalue()


def test_image_reference_usage_and_valid_file(monkeypatch, tmp_path):
    reference = tmp_path / 'reference.png'
    reference.write_bytes(png())
    def handler(request):
        body = json.loads(request.content)
        assert request.url.path == '/api/v1/images'
        assert body['n'] == 1
        assert body['input_references'][0]['image_url']['url'].startswith('data:image/png;base64,')
        return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(png()).decode()}], 'usage': {'cost': .01}})
    transport(monkeypatch, handler)
    result = media.media_image('test', model='test/image', references=[str(reference)])
    assert Path(result['files'][0]).read_bytes() == png()
    assert result['usage']['cost'] == .01
    assert 'b64_json' not in Path(result['receipt']).read_text()


@pytest.mark.parametrize('result', [{}, {'data': []}, {'data': [{'b64_json': 'bad'}]}, {'data': [{'b64_json': base64.b64encode(b'not-image').decode()}]}])
def test_no_false_image_success(monkeypatch, result):
    transport(monkeypatch, lambda r: httpx.Response(200, json=result))
    assert media.execute_media_tool('media_image', {'prompt': 'x', 'model': 'test/image'}).startswith('Error executing tool')


def test_audio_voice_and_mp3(monkeypatch):
    def handler(request):
        body = json.loads(request.content)
        assert body == {'model': 'custom/tts', 'input': 'สวัสดี', 'response_format': 'mp3', 'voice': 'custom', 'instructions': 'warm'}
        return httpx.Response(200, content=b'ID3test-audio', headers={'content-type': 'audio/mpeg'})
    transport(monkeypatch, handler)
    result = media.media_audio('สวัสดี', model='custom/tts', voice='custom', instructions='warm')
    assert Path(result['files'][0]).suffix == '.mp3'


@pytest.mark.parametrize('content_type,body', [('application/json', b'{}'), ('audio/mpeg', b'not audio')])
def test_audio_invalid(monkeypatch, content_type, body):
    transport(monkeypatch, lambda r: httpx.Response(200, content=body, headers={'content-type': content_type}))
    with pytest.raises(media.MediaError):
        media.media_audio('test')


def test_video_receipt_resume_download_and_no_key_on_cdn(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        if request.method == 'POST':
            assert json.loads(request.content)['generate_audio'] is False
            return httpx.Response(202, json={'id': 'job-123', 'status': 'pending'})
        if request.url.host == 'cdn.example.com':
            assert 'authorization' not in request.headers
            return httpx.Response(200, content=b'video fixture', headers={'content-type': 'video/mp4'})
        assert request.headers['authorization'] == 'Bearer test-secret'
        if request.url.path.endswith('/content'):
            return httpx.Response(302, headers={'location': 'https://cdn.example.com/video.mp4'})
        # No unsigned_urls: documented content endpoint still works.
        return httpx.Response(200, json={'status': 'completed', 'usage': {'cost': .3}})
    transport(monkeypatch, handler)
    submitted = media.media_video('forest', model='test/video', generate_audio=False)
    receipt = Path(submitted['receipt'])
    assert json.loads(receipt.read_text())['job_id'] == 'job-123'
    result = media.media_video_status('job-123')
    assert result['receipt'] == str(receipt)
    assert Path(result['files'][0]).read_bytes() == b'video fixture'
    count = len(calls)
    assert media.media_video_status('job-123')['files'] == result['files']
    assert len(calls) == count
    assert sum(r.method == 'POST' for r in calls) == 1


@pytest.mark.parametrize('status', ['pending', 'in_progress', 'failed', 'cancelled', 'expired'])
def test_video_status_does_not_download_or_resubmit(monkeypatch, status):
    def handler(request):
        assert request.method == 'GET' and not request.url.path.endswith('/content')
        return httpx.Response(200, json={'status': status, 'error': 'do not echo secret'})
    transport(monkeypatch, handler)
    result = media.media_video_status('abc')
    assert result['status'] == status
    assert 'files' not in result
    assert 'secret' not in json.dumps(result)


@pytest.mark.parametrize('status', [401, 402, 429, 500])
def test_no_paid_retry_no_sensitive_error(monkeypatch, status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text='test-secret body')
    transport(monkeypatch, handler)
    result = media.execute_media_tool('media_video', {'prompt': 'test', 'model': 'test/video'})
    assert len(calls) == 1
    assert f'HTTP {status}' in result
    assert 'test-secret' not in result


def test_timeout_not_retried(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout('private URL or credential', request=request)
    transport(monkeypatch, handler)
    result = media.execute_media_tool('media_image', {'prompt': 'test', 'model': 'test/image'})
    assert len(calls) == 1 and 'do not automatically resubmit' in result
    assert 'private' not in result


def test_no_key_no_request(monkeypatch):
    monkeypatch.setattr(config, 'OPENROUTER_API_KEY', '')
    transport(monkeypatch, lambda r: pytest.fail('must not make request'))
    assert 'API_KEY' in media.execute_media_tool('media_audio', {'text': 'test'})


def test_models_discovery(monkeypatch):
    def handler(request):
        assert request.url.path.endswith('/videos/models')
        return httpx.Response(200, json={'data': [{'id': 'test/video', 'supported_durations': [5, 8]}, {'id': 'other/video'}]})
    transport(monkeypatch, handler)
    result = media.media_models('video', 'test')
    assert result['models'][0]['supported_durations'] == [5, 8]
    assert result['matches'] == 1


@pytest.mark.parametrize('params', [{'scenes': []}, {'scenes': [{'path': '/missing', 'duration': 5}]}, {'scenes': [{'path': '/missing', 'duration': float('nan')}]}, {'scenes': [{}], 'width': 127}])
def test_movie_invalid(params):
    assert media.execute_media_tool('media_movie', params).startswith('Error executing tool')


def test_tool_registration_and_dispatch(monkeypatch):
    from judecode.agent.tools import TOOL_DEFINITIONS, execute_tool
    names = [t['function']['name'] for t in TOOL_DEFINITIONS]
    for name in media.MEDIA_FUNCTIONS:
        assert names.count(name) == 1
    monkeypatch.setitem(media.MEDIA_FUNCTIONS, 'media_models', lambda **kw: {'models': []})
    assert json.loads(execute_tool('media_models', {'kind': 'image'})) == {'models': []}


def test_real_movie_still_clip_and_narration(tmp_path):
    try:
        binary = media._ffmpeg()
    except media.MediaError:
        pytest.skip('optional FFmpeg not installed')
    still = tmp_path / "ภาพ with ' quote.png"
    still.write_bytes(png())
    clip = tmp_path / 'clip.mp4'
    audio = tmp_path / 'voice.mp3'
    media._run_ffmpeg(binary, ['-f', 'lavfi', '-i', 'color=red:s=160x120:d=0.5', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.5', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(clip)])
    media._run_ffmpeg(binary, ['-f', 'lavfi', '-i', 'sine=frequency=220:duration=0.4', str(audio)])
    result = media.media_movie([{'path': str(still), 'duration': .5}, {'path': str(clip), 'duration': .75}], narration=str(audio), width=320, height=240)
    output = Path(result['files'][0])
    assert output.stat().st_size > 1000
    decode = subprocess.run([binary, '-i', str(output), '-f', 'null', '-'], capture_output=True)
    assert decode.returncode == 0
    assert b'320x240' in decode.stderr and b'Audio: aac' in decode.stderr
    assert b'00:00:01.' in decode.stderr  # Short narration must not truncate the film.
    assert still.is_file() and clip.is_file()


def test_no_spending_when_storage_unavailable(monkeypatch):
    def deny(*args, **kwargs):
        raise PermissionError('private path')
    monkeypatch.setattr(media.tempfile, 'TemporaryFile', deny)
    transport(monkeypatch, lambda r: pytest.fail('must not spend credits'))
    assert media.execute_media_tool('media_audio', {'text': 'test'}).startswith('Error executing tool')


def test_completed_without_download_and_unrelated_json(monkeypatch):
    root = media._directory()
    (root / 'unrelated.json').write_text('[]')
    def handler(request):
        assert not request.url.path.endswith('/content')
        return httpx.Response(200, json={'status': 'completed'})
    transport(monkeypatch, handler)
    assert 'files' not in media.media_video_status('job-1', download=False)


def test_video_download_failure_preserves_resumable_receipt(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        if request.method == 'POST':
            return httpx.Response(202, json={'id': 'job-1', 'status': 'pending'})
        if request.url.path.endswith('/content'):
            return httpx.Response(503)
        return httpx.Response(200, json={'status': 'completed'})
    transport(monkeypatch, handler)
    receipt = media.media_video('x', model='test/video')['receipt']
    with pytest.raises(media.MediaError, match='existing job ID'):
        media.media_video_status('job-1')
    assert json.loads(Path(receipt).read_text())['job_id'] == 'job-1'
    assert sum(r.method == 'POST' for r in calls) == 1


def test_video_download_rejects_insecure_redirect(monkeypatch):
    def handler(request):
        assert request.url.scheme == 'https'
        if request.url.path.endswith('/content'):
            return httpx.Response(302, headers={'location': 'http://cdn.example.com/test.mp4'})
        return httpx.Response(200, json={'status': 'completed'})
    transport(monkeypatch, handler)
    with pytest.raises(media.MediaError, match='non-HTTPS'):
        media.media_video_status('job-1')
