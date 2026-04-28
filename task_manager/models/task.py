"""
Task Model Module
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
import json


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


@dataclass
class Task:
    id: int
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.MEDIUM
    created_at: str = ""
    updated_at: str = ""
    finished_at: str = ""
    tags: list = field(default_factory=list)
    pomodoro_count: int = 0

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not self.updated_at:
            self.updated_at = self.created_at

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "priority": self.priority.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
            "tags": self.tags,
            "pomodoro_count": self.pomodoro_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        return cls(
            id=data["id"],
            title=data["title"],
            description=data.get("description", ""),
            status=TaskStatus(data.get("status", "pending")),
            priority=TaskPriority(data.get("priority", "medium")),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            finished_at=data.get("finished_at", ""),
            tags=data.get("tags", []),
            pomodoro_count=data.get("pomodoro_count", 0),
        )

    def complete(self):
        self.status = TaskStatus.DONE
        self.finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.updated_at = self.finished_at

    def start(self):
        if self.status == TaskStatus.PENDING:
            self.status = TaskStatus.IN_PROGRESS
            self.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def cancel(self):
        self.status = TaskStatus.CANCELLED
        self.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def add_pomodoro(self):
        self.pomodoro_count += 1
        self.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def update_priority(self, priority: TaskPriority):
        self.priority = priority
        self.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
