"""
Safety & Control for JudeCode — Phase 3

3.1 Permission Levels:
  - auto: execute without asking
  - ask: prompt user before executing
  - deny: never allow

  Categories: read, write, delete, shell, deploy, network

3.2 Sandbox Mode:
  - Write changes to /tmp sandbox first
  - Preview changes before applying
  - Apply when user confirms

3.3 Automatic Backups:
  - Backup file before every write/edit/delete
  - Auto-restore on failure
  - Cleanup old backups after N days
"""

import os
import shutil
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from judecode.utils.logger import get_logger

logger = get_logger("judecode.safety")


# ═══════════════════════════════════════════════════════════════
#  3.1 Permission Levels
# ═══════════════════════════════════════════════════════════════

class Permission(str, Enum):
    """Permission levels for tool execution."""
    AUTO = "auto"    # Execute without asking
    ASK = "ask"      # Prompt user before executing
    DENY = "deny"    # Never allow


class PermissionCategory(str, Enum):
    """Categories of operations that can be permission-controlled."""
    READ = "read"          # Reading files, searching
    WRITE = "write"        # Writing/editing files
    DELETE = "delete"      # Deleting files
    SHELL = "shell"        # Running shell commands
    DEPLOY = "deploy"      # Deployment operations
    NETWORK = "network"    # Web requests, API calls
    SYSTEM = "system"      # System-level operations


# Default permission profile
DEFAULT_PERMISSIONS: dict[str, str] = {
    "read": "auto",
    "write": "auto",
    "delete": "ask",
    "shell": "auto",
    "deploy": "ask",
    "network": "auto",
    "system": "deny",
}

# Dangerous shell command patterns that should always require ASK
DANGEROUS_SHELL_PATTERNS = [
    "rm -rf /",
    "rm -rf ~",
    "rm -rf *",
    "format ",
    "mkfs.",
    "dd if=",
    "> /dev/sd",
    "shutdown",
    "reboot",
    "init 0",
    "init 6",
    ":(){ :|:& };:",   # fork bomb
    "chmod -R 777 /",
    "chown -R ",
    "pip install --force",
    "npm publish",
    "git push --force",
    "git reset --hard",
    "DROP TABLE",
    "DROP DATABASE",
    "DELETE FROM",
    "TRUNCATE",
]

# Tool name → permission category mapping
TOOL_CATEGORY_MAP: dict[str, str] = {
    "read": "read",
    "write": "write",
    "edit": "write",
    "delete": "delete",
    "shell": "shell",
    "glob": "read",
    "grep": "read",
    "ls": "read",
    "web_fetch": "network",
    "web_search": "network",
    "codebase_index": "read",
    "codebase_search": "read",
    "codebase_summary": "read",
    "screenshot": "read",
    "vault_create_note": "write",
    "vault_update_note": "write",
    "vault_delete_note": "delete",
    "batch_rename": "write",
    "batch_copy": "write",
    "batch_delete": "delete",
    "mouse_click": "system",
    "keyboard_type": "system",
}


class PermissionManager:
    """Manage permission levels for tool execution.

    Supports:
    - Per-category permission levels (auto/ask/deny)
    - Dangerous command detection
    - User confirmation prompts
    - Configurable via env vars or config file
    """

    def __init__(self, permissions: Optional[dict[str, str]] = None):
        self.permissions = permissions or dict(DEFAULT_PERMISSIONS)
        self._pending_approvals: dict[str, dict] = {}

    def check_permission(self, tool_name: str, tool_params: dict) -> tuple[bool, str, str]:
        """Check if a tool execution is allowed.

        Returns:
            (allowed, permission_level, reason)
        """
        # Get category for this tool
        category = TOOL_CATEGORY_MAP.get(tool_name, "shell")
        permission = self.permissions.get(category, "ask")

        # Special check for dangerous shell commands
        if tool_name == "shell":
            command = tool_params.get("command", "")
            for pattern in DANGEROUS_SHELL_PATTERNS:
                if pattern.lower() in command.lower():
                    return (
                        False,
                        "deny",
                        f"🚫 Dangerous command detected: '{pattern}'. "
                        f"This operation is blocked for safety."
                    )

        # Special check for delete operations on critical paths
        if tool_name == "delete":
            path = tool_params.get("path", "")
            critical_paths = ["/etc", "/usr", "/bin", "/sbin", "/System", "/boot", "/lib"]
            abs_path = os.path.abspath(path)
            for cp in critical_paths:
                if abs_path.startswith(cp + "/") or abs_path == cp:
                    return (
                        False,
                        "deny",
                        f"🚫 Cannot delete system path: {path}"
                    )

        if permission == "deny":
            return (False, "deny", f"🚫 Permission denied for {category} operations")
        elif permission == "ask":
            return (True, "ask", f"⚠️ This {category} operation requires your approval")
        else:
            return (True, "auto", "✅ Auto-approved")

    def set_permission(self, category: str, level: str) -> None:
        """Set permission level for a category."""
        if level not in ("auto", "ask", "deny"):
            raise ValueError(f"Invalid permission level: {level}")
        self.permissions[category] = level

    def get_permissions_summary(self) -> str:
        """Get a summary of all permission levels."""
        lines = ["🔐 Permission Levels:"]
        icons = {"auto": "✅", "ask": "⚠️", "deny": "🚫"}
        for cat, level in self.permissions.items():
            icon = icons.get(level, "?")
            lines.append(f"  {icon} {cat}: {level}")
        return "\n".join(lines)

    def load_from_env(self) -> None:
        """Load permissions from environment variables.

        Format: JUDECODE_PERM_<CATEGORY>=auto|ask|deny
        Example: JUDECODE_PERM_DELETE=ask
        """
        import os
        for category in PermissionCategory:
            env_key = f"JUDECODE_PERM_{category.value.upper()}"
            val = os.environ.get(env_key, "").lower()
            if val in ("auto", "ask", "deny"):
                self.permissions[category.value] = val

    def load_from_config(self, config_path: Optional[str] = None) -> None:
        """Load permissions from a JSON config file."""
        import json
        if config_path is None:
            config_path = str(Path.home() / ".judecode" / "permissions.json")

        path = Path(config_path)
        if not path.exists():
            return

        try:
            data = json.loads(path.read_text())
            for cat, level in data.items():
                if level in ("auto", "ask", "deny"):
                    self.permissions[cat] = level
        except Exception as e:
            logger.warning(f"Failed to load permissions config: {e}")


# ═══════════════════════════════════════════════════════════════
#  3.2 Sandbox Mode
# ═══════════════════════════════════════════════════════════════

class SandboxManager:
    """Sandbox mode: write changes to a temporary directory first.

    Flow:
    1. Agent writes to sandbox instead of real files
    2. User reviews changes (diff)
    3. User approves → apply to real files
    4. User rejects → discard changes

    The sandbox mirrors the project directory structure.
    """

    def __init__(self, project_root: str = "."):
        self.project_root = os.path.abspath(project_root)
        self.sandbox_dir = Path.home() / ".judecode" / "sandbox"
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        self._active = False
        self._changes: list[dict[str, Any]] = []

    @property
    def is_active(self) -> bool:
        return self._active

    def activate(self) -> str:
        """Activate sandbox mode."""
        self._active = True
        self._changes = []
        return "🧪 Sandbox mode activated. Changes will be previewed before applying."

    def deactivate(self) -> str:
        """Deactivate sandbox mode."""
        self._active = False
        self._changes = []
        return "🧪 Sandbox mode deactivated. Changes will be applied directly."

    def sandbox_path(self, real_path: str) -> str:
        """Get the sandbox equivalent of a real path."""
        rel = os.path.relpath(os.path.abspath(real_path), self.project_root)
        return str(self.sandbox_dir / rel)

    def stage_change(
        self,
        operation: str,
        path: str,
        content: Optional[str] = None,
        old_content: Optional[str] = None,
    ) -> str:
        """Stage a change in the sandbox.

        Args:
            operation: write, edit, delete
            path: Target file path
            content: New content (for write/edit)
            old_content: Original content (for diff)

        Returns:
            Staging result message
        """
        sbox_path = self.sandbox_path(path)

        if operation in ("write", "edit") and content is not None:
            # Write to sandbox
            os.makedirs(os.path.dirname(sbox_path), exist_ok=True)
            with open(sbox_path, "w", encoding="utf-8") as f:
                f.write(content)

        self._changes.append({
            "operation": operation,
            "path": path,
            "sandbox_path": sbox_path,
            "timestamp": datetime.now().isoformat(),
        })

        return f"🧪 Staged: {operation} {path} (in sandbox)"

    def get_pending_changes(self) -> list[dict[str, Any]]:
        """Get all pending changes."""
        return self._changes

    def get_diff_summary(self) -> str:
        """Get a summary of all pending changes."""
        if not self._changes:
            return "No pending changes in sandbox."

        lines = [f"🧪 Sandbox: {len(self._changes)} pending change(s):"]
        for i, change in enumerate(self._changes, 1):
            icon = {"write": "📝", "edit": "✏️", "delete": "🗑️"}.get(
                change["operation"], "?"
            )
            lines.append(f"  {i}. {icon} {change['operation']} {change['path']}")
        return "\n".join(lines)

    def apply_all(self) -> dict[str, Any]:
        """Apply all sandboxed changes to real files."""
        applied = []
        errors = []

        for change in self._changes:
            try:
                if change["operation"] in ("write", "edit"):
                    sbox_path = change["sandbox_path"]
                    real_path = change["path"]
                    if os.path.exists(sbox_path):
                        os.makedirs(os.path.dirname(real_path), exist_ok=True)
                        shutil.copy2(sbox_path, real_path)
                        applied.append(change)
                elif change["operation"] == "delete":
                    real_path = change["path"]
                    if os.path.exists(real_path):
                        os.remove(real_path)
                        applied.append(change)
            except Exception as e:
                errors.append({"change": change, "error": str(e)})

        # Clear sandbox
        self._changes = []
        self._cleanup_sandbox()

        return {
            "applied": len(applied),
            "errors": len(errors),
            "details": applied,
            "error_details": errors,
        }

    def discard_all(self) -> str:
        """Discard all sandboxed changes."""
        count = len(self._changes)
        self._changes = []
        self._cleanup_sandbox()
        return f"🧪 Discarded {count} change(s)"

    def _cleanup_sandbox(self) -> None:
        """Remove sandbox files."""
        if self.sandbox_dir.exists():
            shutil.rmtree(self.sandbox_dir, ignore_errors=True)
            self.sandbox_dir.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
#  3.3 Automatic Backups
# ═══════════════════════════════════════════════════════════════

class BackupManager:
    """Automatically backup files before modifications.

    Before any write/edit/delete operation:
    1. Copy the file to ~/.judecode/backups/<timestamp>/
    2. Perform the operation
    3. If operation fails → auto-restore from backup
    4. If operation succeeds → keep backup for N days

    This provides a safety net for all file operations.
    """

    def __init__(self, max_backup_age_days: int = 7):
        self.backup_dir = Path.home() / ".judecode" / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.max_backup_age_days = max_backup_age_days
        self._recent_backups: list[dict[str, Any]] = []

    def backup_file(self, file_path: str, reason: str = "edit") -> Optional[str]:
        """Create a backup of a file before modification.

        Args:
            file_path: Path to the file to backup
            reason: Why the backup was created

        Returns:
            Backup path, or None if file doesn't exist
        """
        abs_path = os.path.abspath(file_path)
        if not os.path.exists(abs_path):
            return None

        # Create timestamped backup directory
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_subdir = self.backup_dir / timestamp
        backup_subdir.mkdir(exist_ok=True)

        # Preserve directory structure
        rel_path = os.path.relpath(abs_path, "/")
        backup_path = backup_subdir / rel_path
        backup_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            shutil.copy2(abs_path, backup_path)

            # Record backup metadata
            meta = {
                "original_path": abs_path,
                "backup_path": str(backup_path),
                "timestamp": timestamp,
                "reason": reason,
                "size": os.path.getsize(abs_path),
            }
            meta_file = backup_subdir / "_metadata.json"
            meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

            self._recent_backups.append(meta)
            return str(backup_path)

        except Exception as e:
            logger.warning(f"Failed to backup {abs_path}: {e}")
            return None

    def restore_file(self, file_path: str, backup_path: Optional[str] = None) -> bool:
        """Restore a file from backup.

        Args:
            file_path: Path to restore to
            backup_path: Specific backup to restore from (default: most recent)

        Returns:
            True if restore succeeded
        """
        abs_path = os.path.abspath(file_path)

        if backup_path:
            # Restore from specific backup
            if os.path.exists(backup_path):
                try:
                    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                    shutil.copy2(backup_path, abs_path)
                    return True
                except Exception as e:
                    logger.error(f"Failed to restore {abs_path}: {e}")
                    return False

        # Find most recent backup for this file
        for backup in reversed(self._recent_backups):
            if backup["original_path"] == abs_path:
                bp = backup["backup_path"]
                if os.path.exists(bp):
                    try:
                        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                        shutil.copy2(bp, abs_path)
                        return True
                    except Exception as e:
                        logger.error(f"Failed to restore {abs_path}: {e}")
                        return False

        # Search backup directories
        for backup_subdir in sorted(self.backup_dir.iterdir(), reverse=True):
            if not backup_subdir.is_dir():
                continue
            meta_file = backup_subdir / "_metadata.json"
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text())
                    if meta.get("original_path") == abs_path:
                        bp = meta["backup_path"]
                        if os.path.exists(bp):
                            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                            shutil.copy2(bp, abs_path)
                            return True
                except Exception:
                    continue

        return False

    def auto_backup_and_execute(
        self,
        file_path: str,
        operation: callable,
        reason: str = "edit",
    ) -> tuple[Any, bool]:
        """Backup a file, execute an operation, auto-restore on failure.

        Args:
            file_path: File to backup
            operation: Callable that performs the modification
            reason: Why the backup was created

        Returns:
            (result, success) — result of operation, whether it succeeded
        """
        backup_path = self.backup_file(file_path, reason)

        try:
            result = operation()
            return (result, True)
        except Exception as e:
            # Auto-restore from backup
            if backup_path:
                restored = self.restore_file(file_path, backup_path)
                if restored:
                    logger.info(f"Auto-restored {file_path} after failed {reason}")
                else:
                    logger.warning(f"Failed to auto-restore {file_path}")
            return (str(e), False)

    def cleanup_old_backups(self) -> int:
        """Remove backups older than max_backup_age_days."""
        if not self.backup_dir.exists():
            return 0

        removed = 0
        now = datetime.now().timestamp()
        max_age_seconds = self.max_backup_age_days * 86400

        for backup_subdir in self.backup_dir.iterdir():
            if not backup_subdir.is_dir():
                continue
            try:
                # Parse timestamp from directory name
                dir_name = backup_subdir.name
                # Format: YYYYMMDD_HHMMSS_ffffff
                dt = datetime.strptime(dir_name.split("_")[0] + dir_name.split("_")[1], "%Y%m%d%H%M%S")
                age = now - dt.timestamp()
                if age > max_age_seconds:
                    shutil.rmtree(backup_subdir)
                    removed += 1
            except Exception:
                # If we can't parse the timestamp, check mtime
                try:
                    mtime = backup_subdir.stat().st_mtime
                    if now - mtime > max_age_seconds:
                        shutil.rmtree(backup_subdir)
                        removed += 1
                except Exception:
                    continue

        return removed

    def get_recent_backups(self, limit: int = 10) -> list[dict]:
        """Get recent backup entries."""
        return self._recent_backups[-limit:]

    def get_summary(self) -> str:
        """Get backup system summary."""
        backup_count = len(list(self.backup_dir.iterdir())) if self.backup_dir.exists() else 0
        return (
            f"💾 Backup System:\n"
            f"   Total backup sets: {backup_count}\n"
            f"   Max age: {self.max_backup_age_days} days\n"
            f"   Location: {self.backup_dir}"
        )


# Need json import for BackupManager
import json
