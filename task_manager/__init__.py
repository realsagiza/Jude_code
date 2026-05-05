"""
Task Manager Package

A complete task management system for JudeCode.
Provides models, storage, helpers, and a TaskManager facade
for integrating with the JudeCode agent engine.
"""

from .models.task import Task, TaskStatus, TaskPriority
from .utils.storage import TaskStorage
from .utils.helpers import (
    filter_by_status,
    filter_by_priority,
    filter_by_tag,
    sort_tasks,
    get_bangkok_now,
    truncate_text,
)

__all__ = [
    "Task",
    "TaskStatus",
    "TaskPriority",
    "TaskStorage",
    "filter_by_status",
    "filter_by_priority",
    "filter_by_tag",
    "sort_tasks",
    "get_bangkok_now",
    "truncate_text",
]
