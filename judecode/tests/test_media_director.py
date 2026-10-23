import json
from pathlib import Path

import pytest

from judecode import config
from judecode.utils import media_director as d
from judecode.utils import media_tools as m


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'MEDIA_OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(config, 'MEDIA_IMAGE_MODEL', 'budget/image')
    monkeypatch.setattr(config, 'MEDIA_VIDEO_MODEL', 'budget/video')
    monkeypatch.setattr(config, 'MEDIA_AUDIO_MODEL', '')


def plan(scenes):
    return d.director_plan('แมวกลับบ้าน', 'แมวสีส้ม ผ้าพันคอแดง บ้านไม้ แสงเย็น', scenes)['id']


def test_resume_full_pipeline_and_reference_handoff(monkeypatch):
    calls = []
    def image(prompt, **kw):
        calls.append(('image', prompt, kw))
        return {'files': [f'image{len(calls)}.png'], 'receipt': f'image{len(calls)}.json'}
    monkeypatch.setattr(m, 'media_image', image)
    monkeypatch.setattr(m, 'media_video', lambda *a, **kw: calls.append(('video', kw)) or {'job_id': 'job1', 'status': 'pending'})
    polls = iter([{'job_id': 'job1', 'status': 'pending'}, {'job_id': 'job1', 'status': 'completed', 'files': ['clip.mp4']}] * 2)
    monkeypatch.setattr(m, 'media_video_status', lambda job: next(polls))
    monkeypatch.setattr(d, '_last_frame', lambda p: 'last-frame.png')
    monkeypatch.setattr(m, 'media_audio', lambda text, **kw: calls.append(('audio', text)) or {'files': ['thai.mp3']})
    monkeypatch.setattr(d, '_audio_duration', lambda p: 7.25)
    def movie(scenes, **kw):
        calls.append(('movie', scenes, kw))
        return {'files': [f'render{len(calls)}.mp4'], 'duration_seconds': sum(s['duration'] for s in scenes)}
    monkeypatch.setattr(m, 'media_movie', movie)
    pid = plan([{'prompt': 'เดินไปประตู', 'narration': 'กลับบ้านกันครับ'},
                {'prompt': 'เดินผ่านประตู', 'link': 'continue'}])
    for _ in range(15):
        result = d.director_step(pid)
        if result['next']['action'] == 'done':
            break
    assert result['status'] == 'completed'
    images = [c for c in calls if c[0] == 'image']
    assert images[1][2]['references'] == ['last-frame.png', 'image1.png']
    assert 'ผ้าพันคอแดง' in images[1][1]
    video = next(c for c in calls if c[0] == 'video')[1]
    assert video['first_frame'] == 'image1.png' and video['resolution'] == '480p'
    assert video['generate_audio'] is False
    assert sum(c[0] == 'video' for c in calls) == 2
    assert ('audio', 'กลับบ้านกันครับ') in calls
    renders = [c for c in calls if c[0] == 'movie']
    assert all(c[1][0]['path'] == 'clip.mp4' for c in renders[:-1])
    assert renders[0][1][0]['duration'] == 7.25
    assert renders[-1][1][0]['path'] == result['scenes'][0]['assets']['render']['files'][0]
    count = len(calls)
    assert d.director_step(pid)['next']['action'] == 'done'
    assert len(calls) == count


def test_uncertain_paid_call_never_repeated(monkeypatch):
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise m.MediaError('timeout')
    monkeypatch.setattr(m, 'media_image', fail)
    pid = plan([{'prompt': 'cat'}])
    with pytest.raises(m.MediaError):
        d.director_step(pid)
    assert d.director_step(pid)['next']['action'] == 'blocked'
    assert d.director_status(pid)['pending']['operation'] == 'image'
    assert len(calls) == 1


@pytest.mark.parametrize('status', ['failed', 'cancelled', 'expired'])
def test_failed_video_stops_pipeline(monkeypatch, status):
    pid = plan([{'prompt': 'cat', 'mode': 'video'}])
    path = d._path(pid)
    p = json.loads(path.read_text())
    p['scenes'][0]['assets'] = {'image': {'files': ['cat.png']}, 'video': {'status': status, 'job_id': 'x'}}
    m._receipt(p, path)
    monkeypatch.setattr(m, 'media_movie', lambda *a, **kw: pytest.fail('Must not render still fallback'))
    monkeypatch.setattr(m, 'media_video', lambda *a, **kw: pytest.fail('Must not resubmit'))
    assert d.director_step(pid)['next']['action'] == 'blocked'
    assert d.director_status(pid)['scenes'][0]['mode'] == 'video'


def test_still_plan_requires_explicit_opt_in(tmp_path):
    with pytest.raises(m.MediaError, match='Video is required'):
        plan([{'prompt': 'cat', 'mode': 'still'}])
    assert not list(tmp_path.rglob('*.json'))


def test_requested_still_scene_remains_supported(monkeypatch):
    p = d.director_plan('Slideshow', 'cat', [{'prompt': 'cat', 'mode': 'still'}], allow_stills=True)
    monkeypatch.setattr(m, 'media_image', lambda *a, **kw: {'files': ['cat.png']})
    monkeypatch.setattr(m, 'media_video', lambda *a, **kw: pytest.fail('Explicit slideshow needs no video'))
    assert d.director_step(p['id'])['next']['action'] == 'render'


@pytest.mark.parametrize('value', ['false', 'true', 1, None])
def test_still_opt_in_cannot_be_truthy_string(value):
    with pytest.raises(m.MediaError, match='boolean'):
        d.director_plan('film', 'cat', [{'prompt': 'cat', 'mode': 'still'}], allow_stills=value)


def test_legacy_still_plan_blocks_before_spending(monkeypatch):
    pid = plan([{'prompt': 'cat'}])
    path = d._path(pid)
    p = json.loads(path.read_text())
    p.pop('allow_stills')
    p['scenes'][0]['mode'] = 'still'
    m._receipt(p, path)
    monkeypatch.setattr(m, 'media_image', lambda *a, **kw: pytest.fail('Legacy still plan must be reviewed'))
    assert d.director_step(pid)['next']['action'] == 'blocked'
    assert json.loads(path.read_text()) == p


def test_uncertain_video_submission_blocks_without_still_fallback(monkeypatch):
    pid = plan([{'prompt': 'cat walking'}])
    monkeypatch.setattr(m, 'media_image', lambda *a, **kw: {'files': ['cat.png']})
    assert d.director_step(pid)['next']['action'] == 'video'
    calls = []
    def fail(*a, **kw):
        calls.append(1)
        raise m.MediaError('video API timeout')
    monkeypatch.setattr(m, 'media_video', fail)
    monkeypatch.setattr(m, 'media_movie', lambda *a, **kw: pytest.fail('Must not render still fallback'))
    with pytest.raises(m.MediaError, match='video API timeout'):
        d.director_step(pid)
    result = d.director_step(pid)
    assert result['next']['action'] == 'blocked'
    assert result['pending']['operation'] == 'video'
    assert result['scenes'][0]['mode'] == 'video'
    assert len(calls) == 1


@pytest.mark.parametrize('scene', [{'prompt': ''}, {'prompt': 'x', 'duration': True}, {'prompt': 'x', 'duration': 16}, {'prompt': 'x', 'mode': 'bad'}, {'prompt': 'x', 'narration': 3}])
def test_plan_validation(scene):
    with pytest.raises(m.MediaError):
        plan([scene])


def test_registration():
    from judecode.agent.tools import TOOL_DEFINITIONS, execute_tool
    names = [t['function']['name'] for t in TOOL_DEFINITIONS]
    assert not set(d.DIRECTOR_FUNCTIONS).intersection(names)
    from judecode.film.director import FILM_FUNCTIONS
    assert all(names.count(name) == 1 for name in FILM_FUNCTIONS)
    assert 'film_plan' in config.SYSTEM_PROMPT
    assert 'film_plan' in config.SYSTEM_PROMPT_COMPACT


def test_project_lock_blocks_concurrent_generation(monkeypatch):
    import fcntl
    pid = plan([{'prompt': 'cat'}])
    with d._path(pid).with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        monkeypatch.setattr(m, 'media_image', lambda *a, **kw: pytest.fail('Must not submit'))
        with pytest.raises(m.MediaError, match='already running'):
            d.director_step(pid)


def test_real_duration_and_continuity_frame(tmp_path):
    try:
        binary = m._ffmpeg()
    except m.MediaError:
        pytest.skip('FFmpeg unavailable')
    clip, audio = tmp_path / 'scene.mp4', tmp_path / 'voice.mp3'
    m._run_ffmpeg(binary, ['-f', 'lavfi', '-i', 'color=blue:s=160x120:d=0.5', '-c:v', 'libx264', str(clip)])
    m._run_ffmpeg(binary, ['-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.8', str(audio)])
    assert .8 < d._audio_duration(str(audio)) < 1.2
    from PIL import Image
    with Image.open(d._last_frame(str(clip))) as frame:
        assert frame.size == (160, 120)


def test_direction_survives_resume_and_reaches_generation(monkeypatch):
    seen = []
    pid = plan([dict(prompt='cat stops at river', beat='bridge collapses',
                     camera='close-up reaction', end_state='cat stranded on bank')])
    monkeypatch.setattr(m, 'media_image', lambda prompt, **kw: seen.append(prompt) or {'files': ['cat.png']})
    d.director_step(pid)
    assert all(value in seen[0] for value in ['bridge collapses', 'close-up reaction', 'cat stranded on bank'])
    assert d.director_status(pid)['quality']['warnings'] == []
    assert d.director_status(pid)['quality']['status'] == 'unreviewed'


def test_overrun_reported_without_regeneration_or_truncating_voice(monkeypatch):
    pid = plan([dict(prompt='cat walks', narration='long voice', duration=5)])
    path = d._path(pid)
    p = json.loads(path.read_text())
    p['scenes'][0]['assets'] = dict(image={'files': ['cat.png']},
        video={'files': ['cat.mp4']}, audio={'files': ['voice.mp3']})
    m._receipt(p, path)
    monkeypatch.setattr(d, '_audio_duration', lambda _: 12.25)
    rendered = []
    monkeypatch.setattr(m, 'media_movie', lambda scenes, **kw: rendered.append(scenes) or
                        dict(files=['render.mp4'], duration_seconds=12.25))
    result = d.director_step(pid)
    warning = next(w for w in result['quality']['warnings'] if w['code'] == 'narration_overrun')
    assert warning['extended_hold_seconds'] == 7.25
    assert rendered[0][0]['duration'] == 12.25
    assert d.director_status(pid)['quality'] == result['quality']


def test_invalid_direction_rejected_before_spending():
    with pytest.raises(m.MediaError, match='must be text'):
        plan([dict(prompt='cat', camera=['bad'])])
