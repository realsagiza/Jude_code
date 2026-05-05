"""
TaskManager - Main Facade

A high-level interface that connects:
  - Task model + storage
  - Task queue with ordering
  - Kanban board generator (via plugins/)
  - Progress tracking
  - Checkpoint automation

This is the "brain" that the agent engine calls to manage tasks.
"""

import os
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

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


class TaskManager:
    """
    Central facade for task operations.
    Integrates storage, filtering, queuing, and kanban tracking.
    """

    def __init__(self, storage_path: Optional[str] = None):
        self.storage = TaskStorage(filepath=storage_path)
        self._tasks: list[Task] = []
        self._current_index: int = 0  # pointer for sequential execution
        self._load()

    # ── Internal ──

    def _load(self):
        """Load tasks from storage."""
        self._tasks = self.storage.load()
        self._current_index = 0

    def _save(self):
        """Persist tasks to storage."""
        self.storage.save(self._tasks)

    def _next_id(self) -> int:
        if not self._tasks:
            return 1
        return max(t.id for t in self._tasks) + 1

    # ── CRUD ──

    def add_task(
        self,
        title: str,
        description: str = "",
        priority: TaskPriority = TaskPriority.MEDIUM,
        tags: Optional[list[str]] = None,
    ) -> Task:
        """Create and add a new task."""
        task = Task(
            id=self._next_id(),
            title=title,
            description=description,
            priority=priority,
            tags=tags or [],
        )
        self._tasks.append(task)
        self._save()
        return task

    def get_task(self, task_id: int) -> Optional[Task]:
        """Get a task by ID."""
        for t in self._tasks:
            if t.id == task_id:
                return t
        return None

    def get_all_tasks(self) -> list[Task]:
        """Return all tasks."""
        return list(self._tasks)

    def update_task(
        self,
        task_id: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        priority: Optional[TaskPriority] = None,
        tags: Optional[list[str]] = None,
    ) -> Optional[Task]:
        """Update fields of an existing task."""
        task = self.get_task(task_id)
        if not task:
            return None
        if title is not None:
            task.title = title
        if description is not None:
            task.description = description
        if priority is not None:
            task.update_priority(priority)
        if tags is not None:
            task.tags = tags
        task.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save()
        return task

    def delete_task(self, task_id: int) -> bool:
        """Delete a task by ID."""
        task = self.get_task(task_id)
        if not task:
            return False
        self._tasks = [t for t in self._tasks if t.id != task_id]
        self._save()
        return True

    # ── Status Transitions ──

    def start_task(self, task_id: int) -> Optional[Task]:
        """Mark a task as in_progress."""
        task = self.get_task(task_id)
        if task:
            task.start()
            self._save()
        return task

    def complete_task(self, task_id: int) -> Optional[Task]:
        """Mark a task as done."""
        task = self.get_task(task_id)
        if task:
            task.complete()
            self._save()
        return task

    def cancel_task(self, task_id: int) -> Optional[Task]:
        """Cancel a task."""
        task = self.get_task(task_id)
        if task:
            task.cancel()
            self._save()
        return task

    # ── Query / Filter / Sort ──

    def list_tasks(
        self,
        status: Optional[str] = None,
        priority: Optional[str] = None,
        tag: Optional[str] = None,
        sort_by: str = "priority",
        reverse: bool = False,
    ) -> list[Task]:
        """List tasks with optional filters and sorting."""
        tasks = self._tasks

        if status:
            tasks = filter_by_status(tasks, status)
        if priority:
            tasks = filter_by_priority(tasks, priority)
        if tag:
            tasks = filter_by_tag(tasks, tag)

        tasks = sort_tasks(tasks, by=sort_by, reverse=reverse)
        return tasks

    def count(self, status: Optional[str] = None) -> int:
        """Count tasks, optionally filtered by status."""
        if status:
            return len(filter_by_status(self._tasks, status))
        return len(self._tasks)

    # ── Task Queue (Sequential Execution) ──

    def build_queue(
        self,
        status_filter: Optional[str] = None,
        priority_filter: Optional[str] = None,
        sort_by: str = "priority",
    ) -> list[Task]:
        """
        Build a prioritized task queue.
        Returns tasks sorted for sequential execution.
        """
        queue = self.list_tasks(
            status=status_filter or "pending",
            priority=priority_filter,
            sort_by=sort_by,
            reverse=False,
        )
        # Also include in_progress tasks (to resume them first)
        in_progress = self.list_tasks(status="in_progress", sort_by=sort_by, reverse=False)
        # Deduplicate by id
        seen_ids = set(t.id for t in queue)
        for t in in_progress:
            if t.id not in seen_ids:
                queue.insert(0, t)  # in_progress first
                seen_ids.add(t.id)
        return queue

    def next_task(self) -> Optional[Task]:
        """
        Get the next pending task from the queue.
        If there's an in_progress task, return that first.
        """
        # First check for in_progress tasks (resume these first)
        in_progress = self.list_tasks(status="in_progress", sort_by="priority")
        if in_progress:
            return in_progress[0]

        # Otherwise get the next pending task
        pending = self.list_tasks(status="pending", sort_by="priority")
        if pending:
            return pending[0]

        return None

    def advance_queue(self) -> Optional[Task]:
        """Mark current task complete and move to next."""
        current = self.next_task()
        if current:
            current.complete()
            self._save()
        return self.next_task()

    def reset_queue(self):
        """Reset all in_progress tasks back to pending."""
        for t in self._tasks:
            if t.status == TaskStatus.IN_PROGRESS:
                t.status = TaskStatus.PENDING
                t.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save()

    # ── Batch Operations ──

    def clear_done(self) -> int:
        """Remove all completed tasks. Returns count removed."""
        before = len(self._tasks)
        self._tasks = [t for t in self._tasks if t.status != TaskStatus.DONE]
        self._save()
        return before - len(self._tasks)

    def clear_cancelled(self) -> int:
        """Remove all cancelled tasks."""
        before = len(self._tasks)
        self._tasks = [t for t in self._tasks if t.status != TaskStatus.CANCELLED]
        self._save()
        return before - len(self._tasks)

    def clear_all(self):
        """Remove all tasks."""
        self._tasks = []
        self._save()

    # ── Pomodoro ──

    def add_pomodoro(self, task_id: int) -> Optional[Task]:
        """Register a pomodoro session for a task."""
        task = self.get_task(task_id)
        if task:
            task.add_pomodoro()
            self._save()
        return task

    # ── Import / Export ──

    def export_tasks(self, path: str):
        """Export all tasks to a JSON file."""
        self.storage.export(self._tasks, path)

    def import_tasks(self, path: str):
        """Import tasks from a JSON file."""
        imported = self.storage.import_(path)
        # Re-assign IDs to avoid conflicts
        for t in imported:
            t.id = self._next_id()
            self._tasks.append(t)
        self._save()

    # ── Summary ──

    def summary(self) -> dict:
        """Return a summary of task statistics."""
        total = len(self._tasks)
        pending = len(filter_by_status(self._tasks, "pending"))
        in_progress = len(filter_by_status(self._tasks, "in_progress"))
        done = len(filter_by_status(self._tasks, "done"))
        cancelled = len(filter_by_status(self._tasks, "cancelled"))

        # Count by priority
        urgent = len(filter_by_priority(self._tasks, "urgent"))
        high = len(filter_by_priority(self._tasks, "high"))
        medium = len(filter_by_priority(self._tasks, "medium"))
        low = len(filter_by_priority(self._tasks, "low"))

        # Next task in queue
        next_t = self.next_task()

        return {
            "total": total,
            "pending": pending,
            "in_progress": in_progress,
            "done": done,
            "cancelled": cancelled,
            "urgent": urgent,
            "high": high,
            "medium": medium,
            "low": low,
            "next_task": {
                "id": next_t.id,
                "title": next_t.title,
                "priority": next_t.priority.value,
            } if next_t else None,
        }

    def summary_text(self) -> str:
        """Return a human-readable summary."""
        s = self.summary()
        lines = [
            "📊 **Task Summary**",
            f"  Total: {s['total']}",
            f"  📋 Pending: {s['pending']}  |  ⚡ In Progress: {s['in_progress']}",
            f"  ✅ Done: {s['done']}  |  ❌ Cancelled: {s['cancelled']}",
            f"  Priority: 🔴 Urgent={s['urgent']} 🟠 High={s['high']} 🟡 Med={s['medium']} 🟢 Low={s['low']}",
        ]
        if s["next_task"]:
            nt = s["next_task"]
            lines.append(f"  ➡️ Next: [{nt['id']}] {nt['title']} ({nt['priority']})")
        else:
            lines.append("  ✅ No pending tasks!")
        return "\n".join(lines)
