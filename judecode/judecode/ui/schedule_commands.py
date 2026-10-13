"""Shared immediate scheduler controls; never start a blocking service from chat."""
import json
from rich.markup import escape
from judecode.agent.scheduler import ScheduleStore


def is_schedule_command(text):
    return text.strip().split(maxsplit=1)[0].lower() == '/schedule' if text.strip() else False


def render_schedule_command(text):
    from judecode.agent.schedule_service import service_status
    parts = text.strip().split()[1:]
    command = parts[0].lower() if parts else 'status'
    try:
        store = ScheduleStore()
        if command == 'status' and len(parts) <= 1:
            result = service_status()
        elif command == 'list' and len(parts) == 1:
            result = store.list()
        elif command == 'history' and len(parts) <= 2:
            result = store.history(parts[1] if len(parts) == 2 else None)
        elif command in ('pause', 'resume', 'cancel') and len(parts) == 2:
            result = store.manage(parts[1], command)
        else:
            return 'ใช้ /schedule [status|list|history [id]|pause id|resume id|cancel id]'
        return escape(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError) as exc:
        return escape(f'Scheduler: {exc}')
