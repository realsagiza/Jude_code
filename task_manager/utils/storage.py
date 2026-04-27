"""
Storage Utility Module
"""
import json
import os
from typing import List
from ..models.task import Task


class TaskStorage:
    def __init__(self, filepath: str = None):
        if filepath is None:
            home = os.path.expanduser("~")
            self.filepath = os.path.join(home, ".task_manager", "tasks.json")
        else:
            self.filepath = filepath
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)

    def load(self) -> List[Task]:
        if not os.path.exists(self.filepath):
            return []
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [Task.from_dict(item) for item in data]
        except (json.JSONDecodeError, KeyError, TypeError):
            return []

    def save(self, tasks: List[Task]):
        data = [task.to_dict() for task in tasks]
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def export(self, tasks: List[Task], export_path: str):
        data = [task.to_dict() for task in tasks]
        with open(export_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def import_(self, import_path: str) -> List[Task]:
        with open(import_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [Task.from_dict(item) for item in data]
