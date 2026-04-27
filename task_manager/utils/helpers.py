"""
Helper Utilities
"""
from datetime import datetime
from typing import List
from ..models.task import Task


def filter_by_status(tasks: List[Task], status: str) -> List[Task]:
    if not status:
        return tasks
    return [t for t in tasks if t.status.value.lower() == status.lower()]


def filter_by_priority(tasks: List[Task], priority: str) -> List[Task]:
    if not priority:
        return tasks
    return [t for t in tasks if t.priority.value.lower() == priority.lower()]


def filter_by_tag(tasks: List[Task], tag: str) -> List[Task]:
    if not tag:
        return tasks
    return [t for t in tasks if tag.lower() in [tg.lower() for tg in t.tags]]


def sort_tasks(tasks: List[Task], by: str = "priority", reverse: bool = False) -> List[Task]:
    priority_order = {"urgent": 4, "high": 3, "medium": 2, "low": 1}
    status_order = {"pending": 1, "in_progress": 2, "done": 3, "cancelled": 4}

    if by == "priority":
        return sorted(tasks, key=lambda t: priority_order.get(t.priority.value, 0), reverse=not reverse)
    elif by == "status":
        return sorted(tasks, key=lambda t: status_order.get(t.status.value, 0), reverse=not reverse)
    elif by == "created":
        return sorted(tasks, key=lambda t: t.created_at, reverse=reverse)
    elif by == "updated":
        return sorted(tasks, key=lambda t: t.updated_at, reverse=reverse)
    else:
        return tasks


def get_bangkok_now() -> str:
    from datetime import timezone, timedelta
    bangkok_tz = timezone(timedelta(hours=7))
    return datetime.now(tz=bangkok_tz).strftime("%Y-%m-%d %H:%M:%S")


def truncate_text(text: str, max_length: int = 50) -> str:
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."
