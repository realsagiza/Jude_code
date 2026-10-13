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
    polls = iter([{'job_id': 'job1', 'status': 'pending'}, {'job_id': 'job1', 'status': 'completed', 'files': ['clip.mp4']}])
    monkeypatch.setattr(m, 'media_video_status', lambda job: next(polls))
    monkeypatch.setattr(d, '_last_frame', lambda p: 'last-frame.png')
    monkeypatch.setattr(m, 'media_audio', lambda text, **kw: calls.append(('audio', text)) or {'files': ['thai.mp3']})
    monkeypatch.setattr(d, '_audio_duration', lambda p: 7.25)
    def movie(scenes, **kw):
        calls.append(('movie', scenes, kw))
        return {'files': [f'render{len(calls)}.mp4'], 'duration_seconds': sum(s['duration'] for s in scenes)}
    monkeypatch.setattr(m, 'media_movie', movie)
    pid = plan([{'prompt': 'เดินไปประตู', 'mode': 'video', 'narration': 'กลับบ้านกันครับ'},
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
    assert sum(c[0] == 'video' for c in calls) == 1
    assert ('audio', 'กลับบ้านกันครับ') in calls
    renders = [c for c in calls if c[0] == 'movie']
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


def test_failed_video_stops_pipeline():
    pid = plan([{'prompt': 'cat', 'mode': 'video'}])
    path = d._path(pid)
    p = json.loads(path.read_text())
    p['scenes'][0]['assets'] = {'image': {'files': ['cat.png']}, 'video': {'status': 'failed', 'job_id': 'x'}}
    m._receipt(p, path)
    assert d.director_step(pid)['next']['action'] == 'blocked'


@pytest.mark.parametrize('scene', [{'prompt': ''}, {'prompt': 'x', 'duration': True}, {'prompt': 'x', 'duration': 16}, {'prompt': 'x', 'mode': 'bad'}, {'prompt': 'x', 'narration': 3}])
def test_plan_validation(scene):
    with pytest.raises(m.MediaError):
        plan([scene])


def test_registration():
    from judecode.agent.tools import TOOL_DEFINITIONS, execute_tool
    names = [t['function']['name'] for t in TOOL_DEFINITIONS]
    assert all(names.count(n) == 1 for n in d.DIRECTOR_FUNCTIONS)
    assert 'Error executing tool' in execute_tool('director_status', {'project_id': '../escape'})
    assert 'director_plan' in config.SYSTEM_PROMPT
    assert 'director_plan' in config.SYSTEM_PROMPT_COMPACT


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
