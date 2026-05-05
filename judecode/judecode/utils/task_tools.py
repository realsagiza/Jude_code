"""
Task Tools - Bridge between agent tools and TaskManager.

These functions are called by the agent's execute_tool() dispatcher.
Each function returns a string result that the LLM can read.
"""

import json
import os
from typing import Optional

# ── Resolve task_manager package path ──
# task_manager/ lives at ../task_manager/ relative to judecode project root
# __file__ = judecode/judecode/utils/task_tools.py
# We go up: utils/ -> judecode/ -> judecode/ (project root) -> ../ = judCode/
import sys
_task_tools_dir = os.path.dirname(os.path.abspath(__file__))  # .../judecode/judecode/utils/
_judecode_pkg_dir = os.path.dirname(_task_tools_dir)           # .../judecode/judecode/
_judecode_project_root = os.path.dirname(_judecode_pkg_dir)     # .../judecode/
_judcode_root = os.path.dirname(_judecode_project_root)          # .../judCode/
if _judcode_root not in sys.path:
    sys.path.insert(0, _judcode_root)

from task_manager.manager import TaskManager
from task_manager.models.task import TaskStatus, TaskPriority

# ── Global singleton (lazy-init) ──

_task_manager: Optional[TaskManager] = None


def _get_manager() -> TaskManager:
    global _task_manager
    if _task_manager is None:
        # Use default path: ~/.task_manager/tasks.json
        _task_manager = TaskManager()
    return _task_manager


def _safe(maybe_list_or_dict):
    """Convert to pretty JSON string."""
    if isinstance(maybe_list_or_dict, (list, dict)):
        return json.dumps(maybe_list_or_dict, ensure_ascii=False, indent=2)
    return str(maybe_list_or_dict)


# ═══════════════════════════════════════════════════════════════
#  Tool Functions (one per tool_name, matching the LLM tool def)
# ═══════════════════════════════════════════════════════════════


def task_add(title: str, description: str = "", priority: str = "medium", tags: list = None) -> str:
    """Add a new task."""
    mgr = _get_manager()
    try:
        p = TaskPriority(priority.lower())
    except ValueError:
        p = TaskPriority.MEDIUM
    task = mgr.add_task(title=title, description=description, priority=p, tags=tags or [])
    return f"✅ Task added: [{task.id}] {task.title} ({task.priority.value})"


def task_list(
    status: str = "",
    priority: str = "",
    tag: str = "",
    sort_by: str = "priority",
    reverse: bool = False,
) -> str:
    """List tasks with optional filters."""
    mgr = _get_manager()
    tasks = mgr.list_tasks(
        status=status or None,
        priority=priority or None,
        tag=tag or None,
        sort_by=sort_by,
        reverse=reverse,
    )
    if not tasks:
        return "📭 No tasks found matching those criteria."

    lines = [f"📋 Tasks ({len(tasks)}):"]
    for t in tasks:
        status_icon = {"pending": "📋", "in_progress": "⚡", "done": "✅", "cancelled": "❌"}
        priority_icon = {"urgent": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
        icon = status_icon.get(t.status.value, "📋")
        picon = priority_icon.get(t.priority.value, "🟡")
        tags_str = f" [{', '.join(t.tags)}]" if t.tags else ""
        lines.append(f"  {icon} [{t.id}] {t.title} {picon}{tags_str}")
    return "\n".join(lines)


def task_get(task_id: int) -> str:
    """Get details of a specific task."""
    mgr = _get_manager()
    task = mgr.get_task(task_id)
    if not task:
        return f"❌ Task [{task_id}] not found."
    return (
        f"📋 **Task [{task.id}]**\n"
        f"  Title: {task.title}\n"
        f"  Description: {task.description or '(none)'}\n"
        f"  Status: {task.status.value}\n"
        f"  Priority: {task.priority.value}\n"
        f"  Tags: {', '.join(task.tags) if task.tags else '(none)'}\n"
        f"  Created: {task.created_at}\n"
        f"  Updated: {task.updated_at}\n"
        f"  Finished: {task.finished_at or '(not done)'}\n"
        f"  Pomodoros: {task.pomodoro_count}"
    )


def task_update(
    task_id: int,
    title: str = "",
    description: str = "",
    priority: str = "",
    tags: list = None,
) -> str:
    """Update a task's fields."""
    mgr = _get_manager()
    p = None
    if priority:
        try:
            p = TaskPriority(priority.lower())
        except ValueError:
            pass
    task = mgr.update_task(
        task_id=task_id,
        title=title or None,
        description=description or None,
        priority=p,
        tags=tags if tags else None,
    )
    if not task:
        return f"❌ Task [{task_id}] not found."
    return f"✅ Task [{task.id}] updated: {task.title}"


def task_delete(task_id: int) -> str:
    """Delete a task."""
    mgr = _get_manager()
    if mgr.delete_task(task_id):
        return f"✅ Task [{task_id}] deleted."
    return f"❌ Task [{task_id}] not found."


def task_start(task_id: int) -> str:
    """Start a task (mark as in_progress)."""
    mgr = _get_manager()
    task = mgr.start_task(task_id)
    if not task:
        return f"❌ Task [{task_id}] not found."
    return f"⚡ Task [{task.id}] started: {task.title}"


def task_complete(task_id: int) -> str:
    """Complete a task."""
    mgr = _get_manager()
    task = mgr.complete_task(task_id)
    if not task:
        return f"❌ Task [{task_id}] not found."
    return f"✅ Task [{task.id}] completed: {task.title} 🎉"


def task_cancel(task_id: int) -> str:
    """Cancel a task."""
    mgr = _get_manager()
    task = mgr.cancel_task(task_id)
    if not task:
        return f"❌ Task [{task_id}] not found."
    return f"❌ Task [{task.id}] cancelled: {task.title}"


def task_next() -> str:
    """Get the next task in the queue."""
    mgr = _get_manager()
    task = mgr.next_task()
    if not task:
        return "✅ No pending tasks! All done."
    return (
        f"➡️ **Next Task**: [{task.id}] {task.title}\n"
        f"   Priority: {task.priority.value}  |  Tags: {', '.join(task.tags) if task.tags else '(none)'}"
    )


def task_queue(status: str = "pending", sort_by: str = "priority") -> str:
    """Show the full task execution queue."""
    mgr = _get_manager()
    queue = mgr.build_queue(status_filter=status or None, sort_by=sort_by)
    if not queue:
        return "📭 Queue is empty."
    lines = [f"📋 **Task Queue** ({len(queue)} items)]:"]
    for i, t in enumerate(queue, 1):
        status_icon = {"pending": "📋", "in_progress": "⚡", "done": "✅", "cancelled": "❌"}
        priority_icon = {"urgent": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
        s_icon = status_icon.get(t.status.value, "📋")
        p_icon = priority_icon.get(t.priority.value, "🟡")
        lines.append(f"  {i}. {s_icon} [{t.id}] {t.title} {p_icon}")
    return "\n".join(lines)


def task_advance() -> str:
    """Complete current task and advance to next."""
    mgr = _get_manager()
    current = mgr.next_task()
    if current:
        current.complete()
        mgr._save()
    next_t = mgr.next_task()
    if next_t:
        return (
            f"✅ Task [{current.id}] completed! 🎉\n"
            f"➡️ Next: [{next_t.id}] {next_t.title} ({next_t.priority.value})"
        )
    return "✅ All tasks completed! 🎉"


def task_summary() -> str:
    """Show task statistics summary."""
    mgr = _get_manager()
    return mgr.summary_text()


def task_clear_done() -> str:
    """Remove all completed tasks."""
    mgr = _get_manager()
    count = mgr.clear_done()
    return f"✅ Removed {count} completed task(s)."


def task_reset_queue() -> str:
    """Reset all in_progress tasks back to pending."""
    mgr = _get_manager()
    mgr.reset_queue()
    return "🔄 Queue reset: all in_progress tasks moved back to pending."


def task_import(path: str) -> str:
    """Import tasks from a JSON file."""
    mgr = _get_manager()
    try:
        mgr.import_tasks(path)
        return f"✅ Tasks imported from: {path}"
    except Exception as e:
        return f"❌ Import failed: {e}"


def task_export(path: str) -> str:
    """Export tasks to a JSON file."""
    mgr = _get_manager()
    try:
        mgr.export_tasks(path)
        return f"✅ Tasks exported to: {path}"
    except Exception as e:
        return f"❌ Export failed: {e}"


def task_add_pomodoro(task_id: int) -> str:
    """Add a pomodoro session to a task."""
    mgr = _get_manager()
    task = mgr.add_pomodoro(task_id)
    if not task:
        return f"❌ Task [{task_id}] not found."
    return f"🍅 Pomodoro #{task.pomodoro_count} added to [{task.id}] {task.title}"


# ═══════════════════════════════════════════════════════════════
#  Master Dispatch Table (used by tools.py execute_tool)
# ═══════════════════════════════════════════════════════════════

TASK_TOOL_FUNCTIONS = {
    "task_add": task_add,
    "task_list": task_list,
    "task_get": task_get,
    "task_update": task_update,
    "task_delete": task_delete,
    "task_start": task_start,
    "task_complete": task_complete,
    "task_cancel": task_cancel,
    "task_next": task_next,
    "task_queue": task_queue,
    "task_advance": task_advance,
    "task_summary": task_summary,
    "task_clear_done": task_clear_done,
    "task_reset_queue": task_reset_queue,
    "task_import": task_import,
    "task_export": task_export,
    "task_add_pomodoro": task_add_pomodoro,
}


def execute_task_tool(tool_name: str, params: dict) -> str:
    """Dispatch a task tool call to the right function."""
    func = TASK_TOOL_FUNCTIONS.get(tool_name)
    if not func:
        return f"Unknown task tool: {tool_name}"
    try:
        return func(**params)
    except Exception as e:
        return f"Error executing '{tool_name}': {type(e).__name__}: {e}"
