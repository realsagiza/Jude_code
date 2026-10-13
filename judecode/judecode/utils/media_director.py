"""Durable, sequential budget film production; one operation per step."""
from __future__ import annotations

import json
import re
import subprocess
import uuid
from pathlib import Path

from judecode import config
from judecode.utils import media_tools as media


def _path(project_id):
    if not re.fullmatch(r'[a-f0-9]{32}', project_id):
        raise media.MediaError('Invalid director project ID')
    root = media._directory() / 'projects'
    root.mkdir(exist_ok=True)
    return root / (project_id + '.json')


def director_plan(title: str, continuity: str, scenes: list[dict], aspect_ratio: str = '16:9',
                  allow_stills: bool = False):
    """The agent writes the creative plan; this tool validates and persists it."""
    if not title.strip() or not continuity.strip() or not 1 <= len(scenes) <= 30:
        raise media.MediaError('Provide title, continuity bible and 1–30 scenes')
    if aspect_ratio not in ('16:9', '9:16', '1:1'):
        raise media.MediaError('Use 16:9, 9:16 or 1:1')
    if type(allow_stills) is not bool:
        raise media.MediaError('allow_stills must be a boolean; enable only when the user explicitly requests still images or a slideshow')
    clean = []
    for i, scene in enumerate(scenes):
        prompt = scene.get('prompt', '')
        duration = scene.get('duration', 5)
        mode = scene.get('mode', 'video')
        link = scene.get('link', 'cut')
        if not isinstance(prompt, str) or not prompt.strip():
            raise media.MediaError('Each scene needs a visual/action prompt')
        if type(duration) is not int or not 1 <= duration <= 15:
            raise media.MediaError('Scene duration must be an integer from 1 to 15 seconds')
        if mode not in ('still', 'video') or link not in ('cut', 'continue'):
            raise media.MediaError('mode: still/video; link: cut/continue')
        if mode == 'still' and not allow_stills:
            raise media.MediaError('Video is required by default. Use mode=video; allow_stills=true is only for user-requested still images or slideshows, never a cost-saving or API-error fallback')
        narration = scene.get('narration', '')
        if not isinstance(narration, str):
            raise media.MediaError('Narration must be text')
        clean.append(dict(number=i + 1, prompt=prompt, duration=duration,
                          mode=mode, link=link, narration=narration, assets={}))
    project_id = uuid.uuid4().hex
    project = dict(id=project_id, title=title, continuity=continuity, scenes=clean,
                   aspect_ratio=aspect_ratio, models=dict(image=config.MEDIA_IMAGE_MODEL,
                   video=config.MEDIA_VIDEO_MODEL, audio=config.MEDIA_AUDIO_MODEL or config.VOICE_TTS_MODEL),
                   voice=config.VOICE_NAME, allow_stills=allow_stills, receipts=[], status='planned')
    if project['models']['audio'] != config.VOICE_TTS_MODEL:
        raise media.MediaError('Director narration uses VOICE_TTS_MODEL/VOICE_NAME; align MEDIA_AUDIO_MODEL first')
    media._receipt(project, _path(project_id))
    return director_status(project_id)


def _next(p):
    if p.get('pending'):
        return {'action': 'blocked', 'reason': 'Previous operation has uncertain outcome. Inspect receipts/Activity; never resubmit automatically.', **p['pending']}
    if p.get('final'):
        return {'action': 'done', 'files': p['final']['files']}
    if not p.get('allow_stills', False) and any(s['mode'] == 'still' for s in p['scenes']):
        return dict(action='blocked', reason='Storyboard contains still scenes without explicit still-image authorization (possibly a legacy plan). Review the saved storyboard against the user request before continuing; do not silently deliver a slideshow or resubmit existing paid jobs.')
    for i, s in enumerate(p['scenes']):
        a = s['assets']
        if 'image' not in a:
            return dict(action='image', scene=i)
        if s['mode'] == 'video':
            if 'video' not in a:
                return dict(action='video', scene=i)
            if a['video'].get('status') in ('failed', 'cancelled', 'expired'):
                return dict(action='blocked', scene=i, reason='Video failed; no automatic regeneration')
            if not a['video'].get('files'):
                return dict(action='poll', scene=i, wait_seconds=30)
        if s['narration'] and 'audio' not in a:
            return dict(action='audio', scene=i)
        if 'render' not in a:
            return dict(action='render', scene=i)
    return dict(action='assemble')


def director_status(project_id: str):
    p = json.loads(_path(project_id).read_text(encoding='utf-8'))
    return {**p, 'project_path': str(_path(project_id)), 'next': _next(p),
            'cost_note': 'Receipts contain reported usage only; total excludes unreported audio/provider charges. No automatic upgrades or retries.'}


def _last_frame(path):
    target = media._directory() / (uuid.uuid4().hex + '.png')
    media._run_ffmpeg(media._ffmpeg(), ['-sseof', '-0.1', '-i', path, '-frames:v', '1', str(target)])
    if not target.is_file():
        raise media.MediaError('Could not extract continuity frame')
    return str(target)


def _audio_duration(path):
    r = subprocess.run([media._ffmpeg(), '-nostdin', '-hide_banner', '-i', path], capture_output=True, timeout=30)
    match = re.search(rb'Duration: (\d+):(\d+):(\d+\.\d+)', r.stderr)
    if not match:
        raise media.MediaError('Cannot measure narration; refusing to truncate speech')
    h, m, s = map(float, match.groups())
    duration = h * 3600 + m * 60 + s + .25
    if duration > 120:
        raise media.MediaError('Narration exceeds 120 seconds; split the scene')
    return duration


def director_step(project_id: str):
    # OS lock releases on process death; pending marker survives for paid calls.
    import fcntl
    path = _path(project_id)
    with path.with_suffix('.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise media.MediaError('Director project is already running') from None
        p = json.loads(path.read_text(encoding='utf-8'))
        step = _next(p)
        action = step['action']
        if action in ('done', 'blocked'):
            return director_status(project_id)
        i = step.get('scene', 0)
        s = p['scenes'][i]
        a = s['assets']
        prompt = p['continuity'] + '\nScene: ' + s['prompt'] + '\nNo captions or written text. Preserve character identity, clothing, props and visual style from references.'
        refs = []
        if action == 'image' and i:
            master = p['scenes'][0]['assets']['image']['files'][0]
            previous = p['scenes'][i - 1]['assets']
            if s['link'] == 'continue':
                if previous.get('video', {}).get('files'):
                    refs.append(_last_frame(previous['video']['files'][0]))
                else:
                    refs.append(previous['image']['files'][0])
                prompt += '\nContinue from the first reference end state; preserve positions and action direction.'
            if master not in refs:
                refs.append(master)
        # Persist before entering a billable endpoint. Any exception remains blocked.
        if action in ('image', 'video', 'audio'):
            p['pending'] = dict(operation=action, scene=i)
            media._receipt(p, path)
        if action == 'image':
            result = media.media_image(prompt, model=p['models']['image'], aspect_ratio=p['aspect_ratio'], references=refs)
        elif action == 'video':
            result = media.media_video(prompt, model=p['models']['video'], duration=s['duration'],
                       aspect_ratio=p['aspect_ratio'], resolution='480p', generate_audio=False,
                       first_frame=a['image']['files'][0])
        elif action == 'poll':
            result = media.media_video_status(a['video']['job_id'])
        elif action == 'audio':
            result = media.media_audio(s['narration'], model=p['models']['audio'], voice=p['voice'])
        elif action == 'render':
            narration = a.get('audio', {}).get('files', [''])[0]
            duration = max(s['duration'], _audio_duration(narration)) if narration else s['duration']
            source = a['video' if s['mode'] == 'video' else 'image']['files'][0]
            width, height = {'16:9': (854, 480), '9:16': (480, 854), '1:1': (480, 480)}[p['aspect_ratio']]
            result = media.media_movie([dict(path=source, duration=duration)], narration=narration, width=width, height=height)
        else:
            width, height = {'16:9': (854, 480), '9:16': (480, 854), '1:1': (480, 480)}[p['aspect_ratio']]
            result = media.media_movie([dict(path=x['assets']['render']['files'][0],
                      duration=x['assets']['render']['duration_seconds']) for x in p['scenes']], width=width, height=height)
        if action == 'assemble':
            p['final'], p['status'] = result, 'completed'
        else:
            a['video' if action == 'poll' else action] = result
            p['status'] = 'producing'
        p.pop('pending', None)
        if result.get('receipt') and result['receipt'] not in p['receipts']:
            p['receipts'].append(result['receipt'])
        media._receipt(p, path)
        return director_status(project_id)


_STRING = {'type': 'string'}
DIRECTOR_DEFINITIONS = [
    media._tool('director_plan', 'Save a film storyboard BEFORE production. Agent authors ordered scene prompts and verbatim Thai narration plus continuity bible (characters, clothes, props, setting, lighting, camera/action direction). Defaults to generated moving video in every scene, including budget films. Still scenes require allow_stills=true and an explicit user request for still images/slideshows. Never replace requested video with stills after API errors or to save cost. link=continue uses previous end frame, cut uses master identity reference. No API charge. Discover configured models/options first. Return project_id for resume.',
        {'title': _STRING, 'continuity': _STRING, 'aspect_ratio': {'type': 'string', 'enum': ['16:9', '9:16', '1:1']},
         'allow_stills': {'type': 'boolean', 'default': False, 'description': 'True only when the user explicitly requests still images or a slideshow. Budget requests do not authorize this.'},
         'scenes': {'type': 'array', 'items': {'type': 'object', 'properties': {
             'prompt': _STRING, 'narration': _STRING, 'duration': {'type': 'integer', 'minimum': 1, 'maximum': 15},
             'mode': {'type': 'string', 'enum': ['still', 'video'], 'default': 'video'}, 'link': {'type': 'string', 'enum': ['cut', 'continue']}}, 'required': ['prompt']}}}, ['title', 'continuity', 'scenes']),
    media._tool('director_status', 'Read saved storyboard, assets, receipts and next operation after restart. No generation or spending.', {'project_id': _STRING}, ['project_id']),
    media._tool('director_step', 'Perform exactly the next storyboard operation: image references -> video job/poll -> Thai narration -> scene render -> final movie. May spend credits. Call sequentially until done; wait 30 seconds between pending polls. Blocked means inspect receipts/Activity, never recreate the project to retry. Continuity is guided, not guaranteed; inspect outputs before claiming visual quality.', {'project_id': _STRING}, ['project_id']),
]
DIRECTOR_FUNCTIONS = {d['function']['name']: globals()[d['function']['name']] for d in DIRECTOR_DEFINITIONS}


def execute_director_tool(name, params):
    try:
        return json.dumps(DIRECTOR_FUNCTIONS[name](**params), ensure_ascii=False)
    except (media.MediaError, OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as exc:
        message = str(exc) if isinstance(exc, media.MediaError) else 'Invalid director arguments or saved project/file operation failed'
        return f"Error executing tool '{name}': {message}"
