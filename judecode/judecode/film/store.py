"""Private, crash-recoverable state for disposable film runs."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from judecode import config
from judecode.film.errors import FilmError

PROJECT_ID = re.compile(r"^[a-f0-9]{32}$")


class FilmStore:
    """Persist plans and checkpoints without storing cloud/API credentials."""

    def __init__(self, state_dir: str | None = None, output_dir: str | None = None):
        self.root = Path(state_dir or config.FILM_STATE_DIR).expanduser().resolve()
        self.output_root = Path(output_dir or config.FILM_OUTPUT_DIR).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.output_root.mkdir(parents=True, exist_ok=True)

    def _project_dir(self, project_id: str) -> Path:
        if not isinstance(project_id, str) or not PROJECT_ID.fullmatch(project_id):
            raise FilmError("Invalid film project ID")
        folder = (self.root / project_id).resolve()
        if not folder.is_relative_to(self.root):
            raise FilmError("Film project path escaped the private state directory")
        return folder

    def path(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "project.json"

    def create(self, project: dict) -> str:
        project_id = project.get("id")
        folder = self._project_dir(project_id)
        folder.mkdir(parents=True, exist_ok=False, mode=0o700)
        os.chmod(folder, 0o700)
        (folder / "tokens").mkdir(mode=0o700)
        (self.output_root / project_id / "assets").mkdir(parents=True, mode=0o700)
        self.save(project)
        return str(self.path(project_id))

    def load(self, project_id: str) -> dict:
        path = self.path(project_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise FilmError("Film project was not found") from None
        except (OSError, ValueError):
            raise FilmError("Film project state is unreadable; no new Pod was created") from None
        if not isinstance(value, dict) or value.get("id") != project_id:
            raise FilmError("Film project state is invalid")
        return value

    def save(self, project: dict) -> str:
        project_id = project.get("id")
        path = self.path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        fd, temporary = tempfile.mkstemp(prefix=".project-", suffix=".json", dir=path.parent)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(project, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return str(path)

    def asset_dir(self, project_id: str) -> Path:
        root = (self.output_root / project_id / "assets").resolve()
        if not root.is_relative_to(self.output_root):
            raise FilmError("Film output path escaped the output directory")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        return root

    def final_path(self, project_id: str) -> Path:
        path = (self.output_root / project_id / "final.mp4").resolve()
        if not path.is_relative_to(self.output_root):
            raise FilmError("Film output path escaped the output directory")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def write_worker_token(self, project_id: str, run_id: str, token: str) -> str:
        if not re.fullmatch(r"[a-f0-9]{32}", run_id) or not isinstance(token, str) or len(token) < 40:
            raise FilmError("Invalid Runpod worker lease credentials")
        folder = self._project_dir(project_id) / "tokens"
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(folder, 0o700)
        path = folder / (run_id + ".token")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            raise FilmError("A worker token already exists for this Runpod lease") from None
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            stream.write(token)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(path, 0o600)
        return str(path)

    def read_worker_token(self, project_id: str, run_id: str) -> str:
        if not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise FilmError("Invalid Runpod worker lease ID")
        path = self._project_dir(project_id) / "tokens" / (run_id + ".token")
        try:
            token = path.read_text(encoding="ascii").strip()
        except (FileNotFoundError, OSError, UnicodeError):
            raise FilmError("Private worker token is unavailable; the Pod cannot be resumed safely") from None
        if len(token) < 40:
            raise FilmError("Private worker token is invalid")
        return token

    def remove_worker_token(self, project_id: str, run_id: str) -> None:
        if re.fullmatch(r"[a-f0-9]{32}", run_id):
            (self._project_dir(project_id) / "tokens" / (run_id + ".token")).unlink(missing_ok=True)
