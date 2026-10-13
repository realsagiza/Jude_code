"""Model-facing scheduling tools, shared by all chat interfaces."""
import json
import os
from judecode.agent.scheduler import ScheduleStore


def definition(name, description, properties, required=()):
    return {'type': 'function', 'function': {'name': name, 'description': description,
            'parameters': {'type': 'object', 'properties': properties, 'required': list(required),
                           'additionalProperties': False}}}


S = {'type': 'string'}
SCHEDULE_DEFINITIONS = [
    definition('schedule_create',
               'Schedule an authorized goal for a future wake-up. The local scheduler service must be running. '
               'The agent can plan bounded work and choose its next wake-up with schedule_checkpoint. '
               'Use observe for file inspection, work only when the user authorized code/shell changes. '
               'No automatic emails, publishing or purchases. Never create unrelated goals.',
               dict(name=S, goal=S, run_at={'type': 'string', 'description': 'ISO timestamp with UTC offset'},
                    interval_minutes={'type': 'number', 'minimum': 1},
                    daily={'type': 'string', 'description': 'HH:MM local wall time'},
                    timezone={'type': 'string', 'default': 'Asia/Bangkok'}, project=S,
                    max_runs={'type': 'integer', 'default': 20}, budget={'type': 'number', 'default': 1},
                    timeout_minutes={'type': 'number', 'default': 15},
                    mode={'type': 'string', 'enum': ['observe', 'work'], 'default': 'observe'}), ('name', 'goal')),
    definition('schedule_list', 'List durable goals, progress, next local wake-ups and service requirement.', {}),
    definition('schedule_manage', 'Pause, cancel or resume a schedule. Resume of uncertain work requires user instruction.',
               dict(job_id=S, action={'type': 'string', 'enum': ['pause', 'resume', 'cancel']}, run_at=S),
               ('job_id', 'action')),
    definition('schedule_history', 'Read recent wake-up outcomes and saved checkpoints.',
               dict(job_id=S, limit={'type': 'integer'})),
    definition('schedule_checkpoint',
               'Only during a scheduled wake-up: persist progress, evidence and remaining plan; '
               'continue chooses next_at (ISO time with offset, at least 60 seconds ahead); '
               'complete stops the entire goal including recurring schedules; blocked requests user attention. '
               'Call after finishing this wake-up, then give a final response. '
               'A checkpoint takes effect only after the worker exits successfully.',
               dict(outcome={'type': 'string', 'enum': ['continue', 'complete', 'blocked']},
                    progress=S, next_at=S), ('outcome', 'progress')),
]
SCHEDULE_NAMES = {d['function']['name'] for d in SCHEDULE_DEFINITIONS}


def execute_schedule_tool(name, params):
    store = ScheduleStore()
    run_id = os.environ.get('JUDECODE_SCHEDULE_RUN_ID')
    if run_id and name in ('schedule_create', 'schedule_manage'):
        raise ValueError('A scheduled worker may only plan its own next wake-up with schedule_checkpoint')
    if name == 'schedule_create':
        result = store.create(**params)
    elif name == 'schedule_list':
        result = {'jobs': store.list(), 'service': 'Requires judecode schedule serve or macOS install'}
    elif name == 'schedule_manage':
        result = store.manage(**params)
    elif name == 'schedule_history':
        result = store.history(**params)
    elif name == 'schedule_checkpoint':
        if not run_id:
            raise ValueError('This tool is only available inside a scheduled wake-up')
        result = store.checkpoint(run_id, **params)
    else:
        raise ValueError('Unknown scheduling tool')
    return json.dumps(result, ensure_ascii=False)
