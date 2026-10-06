"""Private, atomic conversation snapshots; restoration never executes tools."""
import json
import math
import os
from pathlib import Path
import re
import tempfile
from datetime import datetime, timezone


class TranscriptStore:
    def __init__(self):
        self.directory = Path.home() / '.judecode' / 'transcripts'

    def path(self, session_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', session_id):
            raise ValueError('Invalid session ID')
        return self.directory / (session_id + '.json')

    def save(self, session_id, data):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination = self.path(session_id)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                    dir=self.directory, delete=False) as stream:
                temporary = stream.name
                json.dump(dict(data, version=1, session_id=session_id,
                    saved_at=datetime.now(timezone.utc).isoformat()), stream,
                    ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def load(self, session_id, cwd):
        try:
            data = json.loads(self.path(session_id).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise ValueError('Cannot read session snapshot: ' + str(exc)) from exc
        if not isinstance(data, dict) or data.get('version') != 1:
            raise ValueError('Unsupported session snapshot')
        if data.get('session_id') != session_id or data.get('cwd') != str(Path(cwd).resolve()):
            raise ValueError('Session belongs to another project; open its original directory first')
        state = data.get('state')
        budget = data.get('budget')
        if not isinstance(state, dict) or not isinstance(budget, dict):
            raise ValueError('Invalid saved state')
        if not all(isinstance(state.get(key), str) for key in ('original_goal', 'status', 'outcome_reason', 'started_at')):
            raise ValueError('Invalid saved state text')
        if not isinstance(state.get('errors'), list) or any(not isinstance(item, dict) for item in state['errors']):
            raise ValueError('Invalid saved errors')
        if not isinstance(state.get('completed_tasks'), list) or not all(type(item) is int for item in state['completed_tasks']):
            raise ValueError('Invalid saved task list')
        if state.get('current_task_id') is not None and type(state['current_task_id']) is not int:
            raise ValueError('Invalid current task')
        for key in ('total_turns', 'total_tool_calls'):
            if type(state.get(key)) is not int or state[key] < 0:
                raise ValueError('Invalid session counter')
        for key in ('total_input_tokens', 'total_output_tokens', 'total_cost'):
            value = budget.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError('Invalid budget counter')
        from judecode.agent.autonomous import BudgetTracker
        tokens = budget.get('tokens')
        if not isinstance(tokens, dict) or set(tokens) != set(BudgetTracker.CATEGORIES) or any(type(v) is not int or v < 0 for v in tokens.values()):
            raise ValueError('Invalid budget categories')
        failures = data.get('failures', [])
        if not isinstance(failures, list) or any(
            not isinstance(item, list) or len(item) != 2 or
            not isinstance(item[0], list) or len(item[0]) != 2 or
            not all(isinstance(value, str) for value in item[0]) or
            not isinstance(item[1], str) for item in failures):
            raise ValueError('Invalid saved failures')
        if not isinstance(data.get('saved_at'), str):
            raise ValueError('Invalid saved timestamp')
        messages = data.get('messages')
        if not isinstance(messages, list) or not messages:
            raise ValueError('Snapshot has no conversation')
        if not isinstance(messages[0], dict) or messages[0].get('role') != 'system':
            raise ValueError('Missing system message')
        # Only accept structurally valid conversations, including unfinished tool batches.
        pending = set()
        for message in messages:
            if not isinstance(message, dict) or message.get('role') not in ('system', 'user', 'assistant', 'tool'):
                raise ValueError('Invalid conversation message')
            if message.get('content') is not None and not isinstance(message['content'], (str, list)):
                raise ValueError('Invalid message content')
            if message['role'] == 'tool':
                identifier = message.get('tool_call_id')
                if not isinstance(identifier, str) or identifier not in pending:
                    raise ValueError('Unpaired tool result')
                pending.remove(identifier)
            else:
                if pending:
                    raise ValueError('Unfinished tool batch inside conversation')
                calls = message.get('tool_calls', [])
                if not isinstance(calls, list):
                    raise ValueError('Invalid tool calls')
                for call in calls:
                    if not isinstance(call, dict) or not isinstance(call.get('id'), str) or not call['id'] or call['id'] in pending:
                        raise ValueError('Invalid tool call ID')
                    function = call.get('function')
                    if not isinstance(function, dict) or not isinstance(function.get('name'), str) or not isinstance(function.get('arguments'), str):
                        raise ValueError('Invalid tool function')
                    pending.add(call['id'])
        return data, pending

    def list(self, cwd):
        rows = []
        for path in self.directory.glob('*.json'):
            try:
                data, _ = self.load(path.stem, cwd)
                rows.append(data)
            except (ValueError, TypeError, KeyError):
                continue
        return sorted(rows, key=lambda item: item['saved_at'], reverse=True)
