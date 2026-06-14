"""
Background Daemon, Scheduler, Notifications & CI/CD — Phase 4: Full Autonomous

4.1 Background Daemon:
  - Run JudeCode as a background process
  - Accept goals via CLI or API
  - Stream logs to file

4.2 Scheduled Tasks:
  - Cron-like scheduling via YAML config
  - Run tasks at specified times
  - Recurring tasks (daily, weekly, etc.)

4.3 Notification System:
  - Desktop notifications (macOS/Linux/Windows)
  - Webhook notifications
  - Telegram integration
  - Extensible notification providers

4.4 CI/CD Integration:
  - GitHub Actions workflow generator
  - Webhook receiver for CI/CD events

4.5 Multi-Agent Orchestrator:
  - Coordinate multiple agents for complex tasks
  - Task distribution and result aggregation

4.6 Enhanced Budget Manager:
  - Per-task budget limits
  - Model switching on budget pressure
  - Daily/session budget tracking
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from judecode.utils.logger import get_logger

logger = get_logger("judecode.daemon")


# ═══════════════════════════════════════════════════════════════
#  4.1 Background Daemon
# ═══════════════════════════════════════════════════════════════

class DaemonManager:
    """Manage JudeCode as a background daemon process.

    Usage:
      judecode daemon start --goal "Build REST API"
      judecode daemon status
      judecode daemon logs [--follow]
      judecode daemon stop
    """

    def __init__(self):
        self.daemon_dir = Path.home() / ".judecode" / "daemon"
        self.daemon_dir.mkdir(parents=True, exist_ok=True)
        self.pid_file = self.daemon_dir / "daemon.pid"
        self.log_file = self.daemon_dir / "daemon.log"
        self.goal_file = self.daemon_dir / "current_goal.txt"
        self.status_file = self.daemon_dir / "status.json"

    def start(self, goal: str, budget: float = 10.0) -> str:
        """Start the daemon with a goal.

        Launches judecode as a background process that works autonomously.
        """
        # Check if already running
        if self.is_running():
            return "❌ Daemon is already running. Stop it first with 'daemon stop'."

        # Save goal
        self.goal_file.write_text(goal)

        # Build command to run judecode in daemon mode
        # This launches a subprocess that runs the autonomous loop
        cmd = [
            sys.executable, "-m", "judecode",
            "--daemon",
            "--goal", goal,
            "--budget", str(budget),
        ]

        # Start subprocess
        try:
            with open(self.log_file, "a") as log_f:
                proc = subprocess.Popen(
                    cmd,
                    stdout=log_f,
                    stderr=log_f,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,  # Detach from parent
                )

            # Save PID
            self.pid_file.write_text(str(proc.pid))

            # Save initial status
            self._save_status({
                "status": "running",
                "pid": proc.pid,
                "goal": goal,
                "budget": budget,
                "started_at": datetime.now().isoformat(),
            })

            return (
                f"🚀 Daemon started! (PID: {proc.pid})\n"
                f"   Goal: {goal}\n"
                f"   Budget: ${budget}\n"
                f"   Logs: {self.log_file}"
            )

        except Exception as e:
            return f"❌ Failed to start daemon: {e}"

    def stop(self) -> str:
        """Stop the daemon process."""
        if not self.is_running():
            return "⚠️ Daemon is not running."

        try:
            pid = int(self.pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)

            # Wait for process to stop
            for _ in range(10):
                time.sleep(0.5)
                try:
                    os.kill(pid, 0)  # Check if still running
                except ProcessLookupError:
                    break

            # Force kill if still running
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

            self._save_status({
                "status": "stopped",
                "stopped_at": datetime.now().isoformat(),
            })

            return "🛑 Daemon stopped."

        except Exception as e:
            return f"❌ Failed to stop daemon: {e}"

    def is_running(self) -> bool:
        """Check if the daemon is currently running."""
        if not self.pid_file.exists():
            return False

        try:
            pid = int(self.pid_file.read_text().strip())
            os.kill(pid, 0)  # Signal 0 just checks if process exists
            return True
        except (ProcessLookupError, ValueError, FileNotFoundError):
            # Process doesn't exist, clean up stale PID file
            self.pid_file.unlink(missing_ok=True)
            return False

    def get_status(self) -> dict[str, Any]:
        """Get daemon status."""
        status = {
            "running": self.is_running(),
            "pid_file": str(self.pid_file),
            "log_file": str(self.log_file),
        }

        if self.status_file.exists():
            try:
                saved = json.loads(self.status_file.read_text())
                status.update(saved)
            except Exception:
                pass

        if self.goal_file.exists():
            status["goal"] = self.goal_file.read_text().strip()

        return status

    def get_logs(self, lines: int = 50, follow: bool = False) -> str:
        """Get daemon logs.

        Args:
            lines: Number of lines to return
            follow: If True, tail the log file (blocking)
        """
        if not self.log_file.exists():
            return "No logs available."

        try:
            result = subprocess.run(
                ["tail", f"-n{lines}", str(self.log_file)],
                capture_output=True, text=True
            )
            return result.stdout
        except Exception as e:
            return f"Failed to read logs: {e}"

    def _save_status(self, status: dict) -> None:
        existing = {}
        if self.status_file.exists():
            try:
                existing = json.loads(self.status_file.read_text())
            except Exception:
                pass
        existing.update(status)
        self.status_file.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2)
        )


# ═══════════════════════════════════════════════════════════════
#  4.2 Scheduled Tasks
# ═══════════════════════════════════════════════════════════════

class TaskScheduler:
    """Cron-like task scheduler for JudeCode.

    Reads schedule from ~/.judecode/schedule.yaml:

    tasks:
      - name: "Run tests nightly"
        schedule: "0 2 * * *"     # cron format
        goal: "Run full test suite and fix any failures"
        budget: 2.0

      - name: "Check dependencies weekly"
        schedule: "0 8 * * 1"
        goal: "Check npm audit and fix high/critical issues"
        budget: 1.0
    """

    def __init__(self):
        self.schedule_dir = Path.home() / ".judecode" / "scheduler"
        self.schedule_dir.mkdir(parents=True, exist_ok=True)
        self.schedule_file = self.schedule_dir / "schedule.yaml"
        self.history_file = self.schedule_dir / "history.jsonl"

    def load_schedule(self) -> list[dict]:
        """Load scheduled tasks from YAML config."""
        if not self.schedule_file.exists():
            return []

        try:
            import yaml
            data = yaml.safe_load(self.schedule_file.read_text())
            return data.get("tasks", [])
        except ImportError:
            logger.warning("PyYAML not installed. Install with: pip install pyyaml")
            return []
        except Exception as e:
            logger.warning(f"Failed to load schedule: {e}")
            return []

    def save_schedule(self, tasks: list[dict]) -> None:
        """Save scheduled tasks to YAML config."""
        try:
            import yaml
            self.schedule_file.write_text(
                yaml.dump({"tasks": tasks}, default_flow_style=False)
            )
        except ImportError:
            # Fallback to JSON
            self.schedule_file.write_text(
                json.dumps({"tasks": tasks}, ensure_ascii=False, indent=2)
            )

    def should_run_now(self, schedule: str) -> bool:
        """Check if a cron schedule matches the current time.

        Simple cron matching: minute hour day month weekday
        Supports: * (any), specific values, ranges (1-5), lists (1,3,5)
        """
        now = datetime.now()
        parts = schedule.strip().split()

        if len(parts) != 5:
            return False

        def matches(cron_part: str, value: int, min_val: int, max_val: int) -> bool:
            if cron_part == "*":
                return True
            if cron_part.startswith("*/"):
                step = int(cron_part[2:])
                return value % step == 0
            if "-" in cron_part:
                start, end = cron_part.split("-")
                return int(start) <= value <= int(end)
            if "," in cron_part:
                return str(value) in cron_part.split(",")
            try:
                return int(cron_part) == value
            except ValueError:
                return False

        return (
            matches(parts[0], now.minute, 0, 59) and
            matches(parts[1], now.hour, 0, 23) and
            matches(parts[2], now.day, 1, 31) and
            matches(parts[3], now.month, 1, 12) and
            matches(parts[4], now.weekday(), 0, 6)
        )

    def check_and_run(self) -> list[str]:
        """Check all scheduled tasks and run any that are due.

        Returns list of task names that were triggered.
        """
        tasks = self.load_schedule()
        triggered = []

        for task in tasks:
            schedule = task.get("schedule", "")
            if self.should_run_now(schedule):
                name = task.get("name", "unnamed")
                goal = task.get("goal", "")
                budget = task.get("budget", 5.0)

                # Record in history
                self._record_history(task)

                # Start daemon with this goal
                daemon = DaemonManager()
                result = daemon.start(goal=goal, budget=budget)
                triggered.append(name)

                logger.info(f"Scheduled task triggered: {name}")

        return triggered

    def _record_history(self, task: dict) -> None:
        """Record a scheduled task execution."""
        entry = {
            "name": task.get("name"),
            "goal": task.get("goal"),
            "schedule": task.get("schedule"),
            "triggered_at": datetime.now().isoformat(),
        }
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def create_default_schedule(self) -> str:
        """Create a default schedule file with examples."""
        default = """# JudeCode Scheduled Tasks
# Format: cron schedule (minute hour day month weekday)
# * = any, */5 = every 5, 1-5 = range, 1,3,5 = list

tasks:
  # - name: "Run tests nightly"
  #   schedule: "0 2 * * *"
  #   goal: "Run full test suite. If any test fails, find the cause and try to fix it."
  #   budget: 2.0

  # - name: "Check dependencies weekly"
  #   schedule: "0 8 * * 1"
  #   goal: "Run npm audit / pip audit. Fix any high or critical vulnerabilities."
  #   budget: 1.0
"""
        self.schedule_file.write_text(default)
        return f"Default schedule created: {self.schedule_file}"


# ═══════════════════════════════════════════════════════════════
#  4.3 Notification System
# ═══════════════════════════════════════════════════════════════

class NotificationProvider:
    """Base class for notification providers."""

    def send(self, title: str, message: str, priority: str = "normal") -> bool:
        raise NotImplementedError


class DesktopNotification(NotificationProvider):
    """Send desktop notifications (macOS/Linux/Windows)."""

    def send(self, title: str, message: str, priority: str = "normal") -> bool:
        try:
            if sys.platform == "darwin":
                # macOS: use osascript
                script = f'display notification "{message}" with title "{title}"'
                subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True, timeout=5
                )
                return True
            elif sys.platform == "linux":
                # Linux: use notify-send
                subprocess.run(
                    ["notify-send", title, message],
                    capture_output=True, timeout=5
                )
                return True
            elif sys.platform == "win32":
                # Windows: use PowerShell
                ps_script = (
                    f'[System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms") | Out-Null;'
                    f'$n = New-Object System.Windows.Forms.NotifyIcon;'
                    f'$n.Icon = [System.Drawing.SystemIcons]::Information;'
                    f'$n.Visible = $true;'
                    f'$n.ShowBalloonTip(5000, "{title}", "{message}", [System.Windows.Forms.ToolTipIcon]::Info)'
                )
                subprocess.run(
                    ["powershell", "-Command", ps_script],
                    capture_output=True, timeout=5
                )
                return True
        except Exception as e:
            logger.debug(f"Desktop notification failed: {e}")
            return False
        return False


class WebhookNotification(NotificationProvider):
    """Send notifications via webhook (POST request)."""

    def __init__(self, url: str, headers: Optional[dict] = None):
        self.url = url
        self.headers = headers or {"Content-Type": "application/json"}

    def send(self, title: str, message: str, priority: str = "normal") -> bool:
        try:
            import urllib.request
            payload = json.dumps({
                "title": title,
                "message": message,
                "priority": priority,
                "timestamp": datetime.now().isoformat(),
                "source": "judecode",
            }).encode("utf-8")

            req = urllib.request.Request(
                self.url,
                data=payload,
                headers=self.headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception as e:
            logger.debug(f"Webhook notification failed: {e}")
            return False


class TelegramNotification(NotificationProvider):
    """Send notifications via Telegram bot."""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send(self, title: str, message: str, priority: str = "normal") -> bool:
        try:
            import urllib.request
            text = f"🤖 *{title}*\n\n{message}"
            url = (
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
                f"?chat_id={self.chat_id}"
                f"&text={urllib.parse.quote(text)}"
                f"&parse_mode=Markdown"
            )
            with urllib.request.urlopen(url, timeout=10) as resp:
                return resp.status == 200
        except Exception as e:
            logger.debug(f"Telegram notification failed: {e}")
            return False


class NotificationManager:
    """Manage multiple notification providers.

    Reads config from ~/.judecode/notifications.json:
    {
      "providers": {
        "desktop": {"enabled": true},
        "webhook": {"enabled": true, "url": "https://..."},
        "telegram": {"enabled": false, "bot_token": "...", "chat_id": "..."}
      }
    }
    """

    def __init__(self):
        self.config_dir = Path.home() / ".judecode"
        self.config_file = self.config_dir / "notifications.json"
        self.providers: list[NotificationProvider] = []
        self._load_providers()

    def _load_providers(self) -> None:
        """Load notification providers from config."""
        # Always add desktop notification
        self.providers.append(DesktopNotification())

        if not self.config_file.exists():
            return

        try:
            config = json.loads(self.config_file.read_text())
            for name, provider_config in config.get("providers", {}).items():
                if not provider_config.get("enabled", False):
                    continue

                if name == "webhook":
                    url = provider_config.get("url", "")
                    if url:
                        self.providers.append(
                            WebhookNotification(url, provider_config.get("headers"))
                        )
                elif name == "telegram":
                    token = provider_config.get("bot_token", "")
                    chat_id = provider_config.get("chat_id", "")
                    if token and chat_id:
                        self.providers.append(
                            TelegramNotification(token, chat_id)
                        )
        except Exception as e:
            logger.debug(f"Failed to load notification config: {e}")

    def notify(self, title: str, message: str, priority: str = "normal") -> dict[str, bool]:
        """Send notification to all configured providers.

        Returns dict of provider_name → success.
        """
        results = {}
        for provider in self.providers:
            name = provider.__class__.__name__
            try:
                results[name] = provider.send(title, message, priority)
            except Exception:
                results[name] = False
        return results

    def notify_task_complete(self, task_name: str, success: bool, duration: str = "") -> dict[str, bool]:
        """Send notification when a task is completed."""
        icon = "✅" if success else "❌"
        title = f"JudeCode: Task {icon}"
        message = f"{icon} {task_name}"
        if duration:
            message += f" ({duration})"
        if not success:
            message += "\nNeeds your attention!"
        return self.notify(title, message, priority="high" if not success else "normal")

    def notify_session_complete(self, goal: str, completed: int, total: int) -> dict[str, bool]:
        """Send notification when a session is completed."""
        title = "JudeCode: Session Complete"
        message = f"🎯 {goal}\n📊 {completed}/{total} tasks completed"
        return self.notify(title, message)

    def notify_error(self, error: str, task_name: str = "") -> dict[str, bool]:
        """Send notification on critical error."""
        title = "JudeCode: Error ⚠️"
        message = f"❌ {error}"
        if task_name:
            message = f"Task: {task_name}\n{message}"
        return self.notify(title, message, priority="high")

    def create_default_config(self) -> str:
        """Create default notification config."""
        default = {
            "providers": {
                "desktop": {"enabled": True},
                "webhook": {
                    "enabled": False,
                    "url": "https://your-webhook-url.com/notify",
                    "headers": {"Content-Type": "application/json"}
                },
                "telegram": {
                    "enabled": False,
                    "bot_token": "YOUR_BOT_TOKEN",
                    "chat_id": "YOUR_CHAT_ID"
                }
            }
        }
        self.config_file.write_text(
            json.dumps(default, ensure_ascii=False, indent=2)
        )
        return f"Default notification config created: {self.config_file}"


# ═══════════════════════════════════════════════════════════════
#  4.4 CI/CD Integration
# ═══════════════════════════════════════════════════════════════

class CICDIntegration:
    """Generate CI/CD configurations for JudeCode.

    Supports:
    - GitHub Actions workflow generation
    - GitLab CI configuration
    - Generic webhook receiver
    """

    @staticmethod
    def generate_github_actions(
        repo_name: str = "my-project",
        trigger_on_issues: bool = True,
        trigger_on_pr: bool = True,
        auto_merge: bool = False,
        budget: float = 5.0,
    ) -> str:
        """Generate a GitHub Actions workflow file for JudeCode."""
        triggers = []
        if trigger_on_issues:
            triggers.append("""
  issues:
    types: [opened, edited]
  issue_comment:
    types: [created]""")
        if trigger_on_pr:
            triggers.append("""
  pull_request:
    types: [opened, synchronize]""")

        workflow = f"""# .github/workflows/judecode.yml
name: JudeCode Agent

on:{"".join(triggers)}

jobs:
  judecode-agent:
    # Only run if issue has 'judecode' label or comment starts with '/judecode'
    if: |
      contains(github.event.issue.labels.*.name, 'judecode') ||
      contains(github.event.comment.body, '/judecode') ||
      contains(github.event.pull_request.title, '[judecode]')
    runs-on: ubuntu-latest
    permissions:
      contents: write
      pull-requests: write
      issues: write

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install JudeCode
        run: pip install judecode

      - name: Run JudeCode Agent
        env:
          JUDECODE_DEEPSEEK_API_KEY: ${{{{ secrets.DEEPSEEK_API_KEY }}}}
          JUDECODE_ANTHROPIC_API_KEY: ${{{{ secrets.ANTHROPIC_API_KEY }}}}
          AUTONOMOUS_MODE: "True"
          AUTONOMOUS_MAX_BUDGET: "{budget}"
        run: |
          judecode --daemon --goal "${{{{ github.event.issue.title || github.event.pull_request.title }}}}"

      {"- name: Auto-merge PR" + chr(10) + "  if: success() && " + str(auto_merge).lower() + chr(10) + "  run: gh pr merge --auto --squash" if auto_merge else ""}
"""
        return workflow

    @staticmethod
    def generate_gitlab_ci(budget: float = 5.0) -> str:
        """Generate a GitLab CI configuration for JudeCode."""
        return f"""# .gitlab-ci.yml
judecode-agent:
  stage: build
  only:
    variables:
      - $JUDECODE_TRIGGER
  script:
    - pip install judecode
    - judecode --daemon --goal "$JUDECODE_GOAL" --budget {budget}
  variables:
    AUTONOMOUS_MODE: "True"
    AUTONOMOUS_MAX_BUDGET: "{budget}"
"""


# ═══════════════════════════════════════════════════════════════
#  4.5 Multi-Agent Orchestrator
# ═══════════════════════════════════════════════════════════════

class AgentRole:
    """Define a role for a multi-agent setup."""
    def __init__(
        self,
        name: str,
        specialty: str,
        system_prompt_suffix: str = "",
        budget: float = 2.0,
    ):
        self.name = name
        self.specialty = specialty
        self.system_prompt_suffix = system_prompt_suffix
        self.budget = budget


class MultiAgentOrchestrator:
    """Orchestrate multiple agents for complex tasks.

    Example:
      orchestrator = MultiAgentOrchestrator()
      orchestrator.add_role("frontend", "React + TypeScript", budget=2.0)
      orchestrator.add_role("backend", "API + Database", budget=3.0)
      orchestrator.add_role("qa", "Testing + Review", budget=1.0)

      plan = orchestrator.decompose("Build e-commerce app")
      # Returns: [
      #   {"role": "backend", "task": "Create API routes..."},
      #   {"role": "frontend", "task": "Build UI components..."},
      #   {"role": "qa", "task": "Write tests..."},
      # ]
    """

    def __init__(self):
        self.roles: list[AgentRole] = []

    def add_role(self, name: str, specialty: str, budget: float = 2.0) -> None:
        """Add an agent role."""
        self.roles.append(AgentRole(
            name=name,
            specialty=specialty,
            budget=budget,
        ))

    def decompose(self, goal: str) -> list[dict[str, Any]]:
        """Decompose a goal into tasks for each role.

        This is a simple heuristic decomposition. In practice,
        the LLM would do the actual decomposition.
        """
        tasks = []
        for role in self.roles:
            tasks.append({
                "role": role.name,
                "specialty": role.specialty,
                "budget": role.budget,
                "task": f"[{role.name}] Contribute to: {goal} (focus on {role.specialty})",
                "dependencies": [],
            })

        # Add dependencies: QA depends on frontend + backend
        for task in tasks:
            if task["role"] == "qa":
                task["dependencies"] = [
                    r.name for r in self.roles if r.name != "qa"
                ]

        return tasks

    def get_execution_order(self, tasks: list[dict]) -> list[list[dict]]:
        """Get execution order respecting dependencies.

        Returns list of batches that can run in parallel.
        """
        batches = []
        remaining = list(tasks)
        completed_roles = set()

        while remaining:
            # Find tasks with all dependencies met
            batch = []
            for task in remaining:
                deps = task.get("dependencies", [])
                if all(d in completed_roles for d in deps):
                    batch.append(task)

            if not batch:
                # Circular dependency or error — just add remaining
                batches.append(remaining)
                break

            batches.append(batch)
            for task in batch:
                completed_roles.add(task["role"])
                remaining.remove(task)

        return batches

    def get_summary(self) -> str:
        """Get orchestrator summary."""
        lines = [f"🤖 Multi-Agent Orchestrator ({len(self.roles)} agents):"]
        for role in self.roles:
            lines.append(f"  • {role.name}: {role.specialty} (budget: ${role.budget})")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
#  4.6 Enhanced Budget Manager
# ═══════════════════════════════════════════════════════════════

class EnhancedBudgetManager:
    """Enhanced budget management with per-task limits and model switching.

    Extends the basic BudgetTracker from Phase 1 with:
    - Per-task budget limits
    - Daily budget tracking
    - Model switching on budget pressure
    - Budget alerts at configurable thresholds
    """

    def __init__(
        self,
        session_budget: float = 10.0,
        daily_budget: float = 50.0,
        per_task_budget: float = 5.0,
        alert_threshold: float = 0.8,
    ):
        self.session_budget = session_budget
        self.daily_budget = daily_budget
        self.per_task_budget = per_task_budget
        self.alert_threshold = alert_threshold

        # Session tracking
        self.session_cost = 0.0
        self.daily_cost = 0.0
        self.task_costs: dict[int, float] = {}

        # Daily tracking file
        self.daily_file = Path.home() / ".judecode" / "budget_daily.json"
        self._load_daily_budget()

    def _load_daily_budget(self) -> None:
        """Load today's budget usage."""
        if not self.daily_file.exists():
            return

        try:
            data = json.loads(self.daily_file.read_text())
            today = datetime.now().strftime("%Y-%m-%d")
            if data.get("date") == today:
                self.daily_cost = data.get("cost", 0.0)
        except Exception:
            pass

    def _save_daily_budget(self) -> None:
        """Save daily budget usage."""
        today = datetime.now().strftime("%Y-%m-%d")
        data = {"date": today, "cost": self.daily_cost}
        self.daily_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2)
        )

    def record_cost(self, amount: float, task_id: Optional[int] = None) -> dict[str, Any]:
        """Record a cost expenditure.

        Returns alert info if thresholds are exceeded.
        """
        self.session_cost += amount
        self.daily_cost += amount

        if task_id is not None:
            self.task_costs[task_id] = self.task_costs.get(task_id, 0) + amount

        self._save_daily_budget()

        # Check alerts
        alerts = []
        session_pct = self.session_cost / self.session_budget if self.session_budget > 0 else 0
        daily_pct = self.daily_cost / self.daily_budget if self.daily_budget > 0 else 0

        if session_pct >= self.alert_threshold:
            alerts.append(
                f"⚠️ Session budget: {session_pct:.0%} used "
                f"(${self.session_cost:.2f}/${self.session_budget:.2f})"
            )

        if daily_pct >= self.alert_threshold:
            alerts.append(
                f"⚠️ Daily budget: {daily_pct:.0%} used "
                f"(${self.daily_cost:.2f}/${self.daily_budget:.2f})"
            )

        if task_id and task_id in self.task_costs:
            task_pct = self.task_costs[task_id] / self.per_task_budget if self.per_task_budget > 0 else 0
            if task_pct >= self.alert_threshold:
                alerts.append(
                    f"⚠️ Task #{task_id} budget: {task_pct:.0%} used "
                    f"(${self.task_costs[task_id]:.2f}/${self.per_task_budget:.2f})"
                )

        return {
            "session_cost": self.session_cost,
            "daily_cost": self.daily_cost,
            "alerts": alerts,
            "over_session_budget": self.session_cost >= self.session_budget,
            "over_daily_budget": self.daily_cost >= self.daily_budget,
            "over_task_budget": (
                task_id and task_id in self.task_costs
                and self.task_costs[task_id] >= self.per_task_budget
            ),
        }

    def should_switch_model(self) -> tuple[bool, str]:
        """Check if we should switch to a cheaper model.

        Returns (should_switch, reason).
        """
        session_pct = self.session_cost / self.session_budget if self.session_budget > 0 else 0
        if session_pct >= 0.9:
            return (
                True,
                f"Session budget at {session_pct:.0%}. Switching to cheaper model to conserve budget."
            )
        return (False, "")

    def get_status(self) -> str:
        """Get budget status summary."""
        session_pct = (self.session_cost / self.session_budget * 100) if self.session_budget > 0 else 0
        daily_pct = (self.daily_cost / self.daily_budget * 100) if self.daily_budget > 0 else 0

        return (
            f"💰 Budget Status:\n"
            f"   Session: ${self.session_cost:.2f}/${self.session_budget:.2f} ({session_pct:.0f}%)\n"
            f"   Daily:   ${self.daily_cost:.2f}/${self.daily_budget:.2f} ({daily_pct:.0f}%)\n"
            f"   Per-task limit: ${self.per_task_budget:.2f}\n"
            f"   Active tasks: {len(self.task_costs)}"
        )
