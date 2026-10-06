"""Restart recovery, tool crash windows, isolation and persistence failures."""
import asyncio
import json
from pathlib import Path

import pytest

from judecode.agent.engine import AgentEngine
from judecode.agent.transcripts import TranscriptStore
from tests.test_reliability import ScriptedAPI, calls


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    agent = AgentEngine('system', ScriptedAPI())
    agent.autonomous.enabled = False
    return agent


def test_restart_restores_conversation_budget_and_forks(engine):
    asyncio.run(engine.chat('Remember ภาษาไทย'))
    source_id = engine.autonomous.session.session_id
    engine.autonomous.session.current_task_id = 42
    engine.save_transcript()
    original = TranscriptStore().path(source_id).read_bytes()
    fresh = AgentEngine('system', ScriptedAPI())
    fresh.resume_session(source_id)
    assert fresh.messages == engine.messages
    assert fresh.autonomous.session.current_task_id == 42
    assert fresh.autonomous.budget.total_input_tokens == engine.autonomous.budget.total_input_tokens
    assert fresh.autonomous.session.session_id != source_id
    assert fresh.checkpoint.session_id == fresh.autonomous.session.session_id
    assert fresh.decisions.session_id == fresh.autonomous.session.session_id
    assert fresh.autonomous.session.status == 'paused'
    assert fresh.api.requests == []
    assert TranscriptStore().path(source_id).read_bytes() == original


def test_completed_tools_not_replayed(engine, monkeypatch):
    engine.api = ScriptedAPI(calls(('read', {'path': 'sample.txt'})))
    Path('sample.txt').write_text('evidence')
    asyncio.run(engine.chat('Read sample'))
    source_id = engine.autonomous.session.session_id
    fresh = AgentEngine('system', ScriptedAPI())
    fresh.autonomous.enabled = False
    fresh.resume_session(source_id)
    monkeypatch.setattr(fresh, '_execute_tool_safe', lambda *args: pytest.fail('Replayed a tool'))
    asyncio.run(fresh.continue_task())
    assert any(m.get('role') == 'tool' and 'evidence' in m['content'] for m in fresh.messages)


def test_crash_in_tool_batch_is_unknown_and_continue_blocked(engine):
    engine.messages += [{'role': 'user', 'content': 'work'},
        {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': 'first', 'type': 'function', 'function': {'name': 'shell', 'arguments': '{}'}},
            {'id': 'second', 'type': 'function', 'function': {'name': 'shell', 'arguments': '{}'}}]},
        {'role': 'tool', 'tool_call_id': 'first', 'content': 'done'}]
    engine.save_transcript()
    engine.resume_session(engine.autonomous.session.session_id)
    assert engine.messages[-1]['tool_call_id'] == 'second'
    assert 'unknown' in engine.messages[-1]['content']
    asyncio.run(engine.continue_task())
    assert engine.api.requests == []
    engine.save_transcript()
    engine.resume_session(engine.autonomous.session.session_id)
    assert engine._resume_uncertain  # persists across repeated restarts


def test_wrong_project_and_invalid_id_preserve_current(engine, tmp_path, monkeypatch):
    asyncio.run(engine.chat('saved'))
    identifier = engine.autonomous.session.session_id
    original = list(engine.messages)
    other = tmp_path / 'other'; other.mkdir(); monkeypatch.chdir(other)
    assert 'No saved' in engine.sessions_summary()
    with pytest.raises(ValueError, match='another project'):
        engine.resume_session(identifier)
    with pytest.raises(ValueError):
        engine.resume_session('../outside')
    assert engine.messages == original


def test_atomic_write_keeps_previous_snapshot_on_failure(engine, monkeypatch):
    asyncio.run(engine.chat('saved'))
    path = TranscriptStore().path(engine.autonomous.session.session_id)
    before = path.read_bytes()
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr('judecode.agent.transcripts.os.replace', fail)
    with pytest.raises(OSError):
        engine.save_transcript()
    assert path.read_bytes() == before
    assert path.stat().st_mode & 0o077 == 0


def test_save_failure_prevents_tool_execution(engine, monkeypatch):
    engine.api = ScriptedAPI(calls(('shell', {'command': 'echo must-not-run'})))
    monkeypatch.setattr(engine, '_execute_tool_safe', lambda *args: pytest.fail('Unsafe execution'))
    save = engine.save_transcript
    def fail_with_tools():
        if any(m.get('tool_calls') for m in engine.messages):
            raise OSError('disk full')
        save()
    monkeypatch.setattr(engine, 'save_transcript', fail_with_tools)
    with pytest.raises(OSError):
        asyncio.run(engine.chat('work'))


def test_clear_keeps_previous_and_uses_new_id(engine):
    asyncio.run(engine.chat('save me'))
    identifier = engine.autonomous.session.session_id
    engine.new_conversation()
    assert engine.messages == [{'role': 'system', 'content': 'system'}]
    assert engine.autonomous.session.session_id != identifier
    engine.resume_session('latest')
    assert any(m.get('content') == 'save me' for m in engine.messages)


def test_corrupt_snapshot_does_not_replace_active_conversation(engine):
    asyncio.run(engine.chat('keep me'))
    identifier = engine.autonomous.session.session_id
    TranscriptStore().path(identifier).write_text('{bad json')
    original = list(engine.messages)
    with pytest.raises(ValueError):
        engine.resume_session(identifier)
    assert engine.messages == original
    assert 'No saved' in engine.sessions_summary()


def test_verification_invalidated_and_failures_restored(engine):
    engine._needs_verification = True
    engine._verification = {'passed': True}
    engine._tool_failures[('shell', 'example')] = 'failure'
    engine.save_transcript()
    engine.resume_session(engine.autonomous.session.session_id)
    assert engine._needs_verification
    assert engine._verification is None
    assert engine._tool_failures == {('shell', 'example'): 'failure'}


@pytest.mark.asyncio
async def test_tui_session_commands_restore_without_running(engine, monkeypatch):
    from judecode.ui.tui_app import JudeCodeTUI
    app = JudeCodeTUI()
    app.agent = engine
    await engine.chat('a saved conversation')
    identifier = engine.autonomous.session.session_id
    engine.new_conversation()
    async with app.run_test(size=(100, 30)):
        await app._handle_command('/sessions')
        await app._handle_command('/resume ' + identifier)
        assert any(m.get('content') == 'a saved conversation' for m in app.agent.messages)
        assert not app.ai_busy


@pytest.mark.parametrize('field,value', [
    ('state', {}), ('budget', {'tokens': []}), ('messages', [None]),
    ('failures', [[[], 'bad']]), ('version', 999),
])
def test_malformed_snapshots_are_skipped(engine, field, value):
    engine.save_transcript()
    path = TranscriptStore().path(engine.autonomous.session.session_id)
    data = json.loads(path.read_text()); data[field] = value
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        engine.resume_session(engine.autonomous.session.session_id)
    assert 'No saved' in engine.sessions_summary()


def test_new_instruction_after_restore_rechecks_work(engine, monkeypatch):
    engine._needs_verification = True
    engine.save_transcript()
    engine.resume_session(engine.autonomous.session.session_id)
    checks = []
    def verify():
        checks.append(True)
        return {'passed': False, 'status': 'unverified', 'summary': 'No checks available', 'results': []}
    monkeypatch.setattr(engine.autonomous.evaluator, 'run_verification', verify)
    asyncio.run(engine.chat('Continue checking the restored work'))
    assert checks
    assert engine.autonomous.session.status == 'unverified'


def test_staged_snapshot_is_rejected(engine):
    engine.save_transcript()
    path = TranscriptStore().path(engine.autonomous.session.session_id)
    data = json.loads(path.read_text()); data['staged'] = True
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='staged'):
        engine.resume_session(engine.autonomous.session.session_id)
