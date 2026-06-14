"""
Checkpoint System for JudeCode — Phase 2: Persistence & Recovery

Saves file snapshots and diffs before modifications, enabling:
  - Rollback to any previous checkpoint
  - View history of changes
  - Auto-restore on failure
  - Diff viewing between checkpoints

Storage: ~/.judecode/checkpoints/<session_id>/
  - state.json          ← checkpoint metadata
  - goal.md             ← original goal
  - decisions.log       ← decision log entries
  - snapshots/<step>/   ← file snapshots at each step
"""

import difflib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from judecode.utils.logger import get_logger

logger = get_logger("judecode.checkpoint")


class CheckpointManager:
    """Manages file checkpoints for a session.

    Before any file modification (write, edit, delete), create a checkpoint
    that saves the current state of the file. This enables rollback.
    """

    def __init__(self, session_id: Optional[str] = None):
        self.session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.checkpoint_dir = (
            Path.home() / ".judecode" / "checkpoints" / self.session_id
        )
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir = self.checkpoint_dir / "snapshots"
        self.snapshots_dir.mkdir(exist_ok=True)
        self._step_counter = 0
        self._metadata: dict[str, Any] = {
            "session_id": self.session_id,
            "created_at": datetime.now().isoformat(),
            "goal": "",
            "checkpoints": [],
        }
        self._load_metadata()

    def _meta_file(self) -> Path:
        return self.checkpoint_dir / "state.json"

    def _load_metadata(self) -> None:
        meta = self._meta_file()
        if meta.exists():
            try:
                self._metadata = json.loads(meta.read_text())
                # Restore step counter from existing checkpoints
                if self._metadata.get("checkpoints"):
                    self._step_counter = max(
                        c.get("step", 0) for c in self._metadata["checkpoints"]
                    )
            except Exception:
                pass

    def _save_metadata(self) -> None:
        self._metadata["updated_at"] = datetime.now().isoformat()
        self._meta_file().write_text(
            json.dumps(self._metadata, ensure_ascii=False, indent=2)
        )

    def set_goal(self, goal: str) -> None:
        """Set the session goal."""
        self._metadata["goal"] = goal
        goal_file = self.checkpoint_dir / "goal.md"
        goal_file.write_text(f"# Goal\n\n{goal}\n")
        self._save_metadata()

    def create_checkpoint(
        self,
        file_paths: list[str],
        reason: str = "manual",
        task_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """Create a checkpoint before modifying files.

        Args:
            file_paths: List of file paths to snapshot
            reason: Why the checkpoint was created (edit, write, delete, manual)
            task_id: Associated task ID if any

        Returns:
            Checkpoint metadata dict
        """
        self._step_counter += 1
        step = self._step_counter

        step_dir = self.snapshots_dir / f"step_{step:04d}"
        step_dir.mkdir(exist_ok=True)

        saved_files = []
        for fpath in file_paths:
            fpath = os.path.abspath(fpath)
            if not os.path.exists(fpath):
                # File doesn't exist yet — record that it's a new file
                saved_files.append({
                    "path": fpath,
                    "existed": False,
                    "size": 0,
                })
                continue

            # Copy file to snapshot directory
            # Preserve directory structure in snapshot
            rel_path = os.path.relpath(fpath, "/")
            snap_path = step_dir / rel_path
            snap_path.parent.mkdir(parents=True, exist_ok=True)

            try:
                shutil.copy2(fpath, snap_path)
                saved_files.append({
                    "path": fpath,
                    "existed": True,
                    "size": os.path.getsize(fpath),
                })
            except Exception as e:
                logger.warning(f"Failed to snapshot {fpath}: {e}")
                saved_files.append({
                    "path": fpath,
                    "existed": True,
                    "size": 0,
                    "error": str(e),
                })

        checkpoint = {
            "step": step,
            "timestamp": datetime.now().isoformat(),
            "reason": reason,
            "task_id": task_id,
            "files": saved_files,
            "snapshot_dir": str(step_dir),
        }

        self._metadata["checkpoints"].append(checkpoint)
        self._save_metadata()

        logger.info(
            f"Checkpoint #{step} created: {len(saved_files)} files, reason={reason}"
        )
        return checkpoint

    def rollback(self, step: Optional[int] = None) -> dict[str, Any]:
        """Rollback to a specific checkpoint step.

        Args:
            step: Step number to rollback to. If None, rollback to last checkpoint.

        Returns:
            Result dict with status and details
        """
        if not self._metadata["checkpoints"]:
            return {"success": False, "error": "No checkpoints available"}

        if step is None:
            checkpoint = self._metadata["checkpoints"][-1]
        else:
            checkpoint = None
            for cp in self._metadata["checkpoints"]:
                if cp["step"] == step:
                    checkpoint = cp
                    break
            if checkpoint is None:
                return {"success": False, "error": f"Checkpoint step {step} not found"}

        step_dir = Path(checkpoint["snapshot_dir"])
        if not step_dir.exists():
            return {"success": False, "error": f"Snapshot directory not found: {step_dir}"}

        restored = []
        errors = []

        for file_info in checkpoint["files"]:
            fpath = file_info["path"]
            if not file_info.get("existed", True):
                # File didn't exist before — delete it if it exists now
                if os.path.exists(fpath):
                    try:
                        os.remove(fpath)
                        restored.append({"path": fpath, "action": "deleted"})
                    except Exception as e:
                        errors.append({"path": fpath, "error": str(e)})
                continue

            # Restore file from snapshot
            rel_path = os.path.relpath(fpath, "/")
            snap_path = step_dir / rel_path

            if snap_path.exists():
                try:
                    shutil.copy2(snap_path, fpath)
                    restored.append({"path": fpath, "action": "restored"})
                except Exception as e:
                    errors.append({"path": fpath, "error": str(e)})
            else:
                errors.append({"path": fpath, "error": "Snapshot file not found"})

        # Remove checkpoints after the rollback point
        self._metadata["checkpoints"] = [
            cp for cp in self._metadata["checkpoints"]
            if cp["step"] <= checkpoint["step"]
        ]
        self._step_counter = checkpoint["step"]
        self._save_metadata()

        return {
            "success": True,
            "rolled_back_to": checkpoint["step"],
            "restored": restored,
            "errors": errors,
            "timestamp": datetime.now().isoformat(),
        }

    def get_diff(self, step: Optional[int] = None, file_path: Optional[str] = None) -> str:
        """Get diff between a checkpoint and current file state.

        Args:
            step: Checkpoint step (default: last)
            file_path: Specific file to diff (default: all files in checkpoint)

        Returns:
            Unified diff string
        """
        if not self._metadata["checkpoints"]:
            return "No checkpoints available"

        if step is None:
            checkpoint = self._metadata["checkpoints"][-1]
        else:
            checkpoint = None
            for cp in self._metadata["checkpoints"]:
                if cp["step"] == step:
                    checkpoint = cp
                    break
            if checkpoint is None:
                return f"Checkpoint step {step} not found"

        step_dir = Path(checkpoint["snapshot_dir"])
        diffs = []

        for file_info in checkpoint["files"]:
            if file_path and file_info["path"] != os.path.abspath(file_path):
                continue

            fpath = file_info["path"]
            rel_path = os.path.relpath(fpath, "/")
            snap_path = step_dir / rel_path

            # Read snapshot content
            snap_lines = []
            if snap_path.exists():
                try:
                    snap_lines = snap_path.read_text().splitlines(keepends=True)
                except Exception:
                    snap_lines = [f"(binary file, {snap_path.stat().st_size} bytes)\n"]

            # Read current content
            curr_lines = []
            if os.path.exists(fpath):
                try:
                    curr_lines = open(fpath).readlines()
                except Exception:
                    curr_lines = [f"(binary file, {os.path.getsize(fpath)} bytes)\n"]

            # Generate unified diff
            diff = difflib.unified_diff(
                snap_lines,
                curr_lines,
                fromfile=f"snapshots/step_{checkpoint['step']:04d}/{rel_path}",
                tofile=fpath,
            )
            diff_text = "".join(diff)
            if diff_text:
                diffs.append(diff_text)

        if not diffs:
            return "No differences found"
        return "\n".join(diffs)

    def list_checkpoints(self) -> list[dict[str, Any]]:
        """List all checkpoints for this session."""
        return self._metadata.get("checkpoints", [])

    def get_summary(self) -> str:
        """Get a human-readable summary of all checkpoints."""
        cps = self._metadata.get("checkpoints", [])
        if not cps:
            return "No checkpoints yet."

        lines = [
            f"📋 Session: {self.session_id}",
            f"🎯 Goal: {self._metadata.get('goal', '(not set)')}",
            f"📊 Checkpoints: {len(cps)}",
            "",
        ]
        for cp in cps:
            file_count = len(cp.get("files", []))
            lines.append(
                f"  Step #{cp['step']:04d} | {cp['timestamp'][:19]} | "
                f"{cp['reason']} | {file_count} files"
                + (f" | task #{cp['task_id']}" if cp.get("task_id") else "")
            )
        return "\n".join(lines)

    def cleanup_old_sessions(self, max_age_days: int = 30) -> int:
        """Remove checkpoint sessions older than max_age_days."""
        checkpoints_root = Path.home() / ".judecode" / "checkpoints"
        if not checkpoints_root.exists():
            return 0

        removed = 0
        now = datetime.now().timestamp()
        max_age_seconds = max_age_days * 86400

        for session_dir in checkpoints_root.iterdir():
            if not session_dir.is_dir():
                continue
            try:
                mtime = session_dir.stat().st_mtime
                if now - mtime > max_age_seconds:
                    shutil.rmtree(session_dir)
                    removed += 1
            except Exception:
                continue

        return removed
