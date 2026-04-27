"""Shell command execution utility — cross-platform (macOS/Linux/Windows)."""

import platform
import subprocess
import sys
import threading
import time
from queue import Queue, Empty
from typing import Iterator, Optional


def _get_shell_args() -> list[str]:
    """Return the shell executable and flag for the current OS."""
    system = platform.system().lower()
    if system == "darwin" or system == "linux":
        # Prefer zsh if available, otherwise bash, otherwise sh
        for sh in ["/bin/zsh", "/bin/bash", "/bin/sh"]:
            import shutil
            if shutil.which(sh):
                return [sh, "-c"]
        # Fallback to system default via /bin/sh
        return ["/bin/sh", "-c"]
    elif system == "windows":
        # Use PowerShell if available (modern Windows ships with it),
        # otherwise fall back to cmd.exe
        import shutil
        pwsh = shutil.which("powershell.exe")
        if pwsh:
            return [pwsh, "-Command"]
        return [r"C:\Windows\System32\cmd.exe", "/c"]
    else:
        # Generic fallback
        import shutil
        if shutil.which("sh"):
            return ["sh", "-c"]
        return [sys.executable, "-c"]


def execute_shell(
    command: str,
    cwd: Optional[str] = None,
    timeout: int = 120,
) -> dict:
    """
    Execute a shell command safely.
    Returns a dict with stdout, stderr, exit_code.
    """
    shell_args = _get_shell_args()
    proc = subprocess.run(
        [*shell_args, command],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "exit_code": proc.returncode,
    }


def _enqueue_stream(stream, queue: Queue, tag: str):
    """Helper to read lines from a stream and put them into a queue."""
    try:
        for line in iter(stream.readline, ""):
            if line:
                queue.put((tag, line))
    finally:
        stream.close()
        queue.put((tag, None))  # sentinel


def execute_shell_stream(
    command: str,
    cwd: Optional[str] = None,
    timeout: int = 120,
) -> Iterator[str | dict]:
    """
    Execute a shell command streaming stdout/stderr.
    Yields lines of output as they arrive (tagged as 'stdout' or 'stderr').
    Finally yields {"type": "return", "exit_code": int}.
    Cross-platform replacement for select-based streaming.
    """
    shell_args = _get_shell_args()
    proc = subprocess.Popen(
        [*shell_args, command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        encoding="utf-8",
        errors="replace",
        bufsize=1,  # line buffered
    )

    queue: Queue = Queue()
    out_thread = threading.Thread(
        target=_enqueue_stream, args=(proc.stdout, queue, "stdout")
    )
    err_thread = threading.Thread(
        target=_enqueue_stream, args=(proc.stderr, queue, "stderr")
    )
    out_thread.start()
    err_thread.start()

    ended = {"stdout": False, "stderr": False}
    deadline = time.monotonic() + timeout if timeout else None

    while not (ended["stdout"] and ended["stderr"]):
        if deadline and time.monotonic() > deadline:
            proc.kill()
            proc.wait()
            yield {"type": "return", "exit_code": proc.returncode}
            return

        try:
            tag, payload = queue.get(timeout=min(0.1, timeout or 1))
        except Empty:
            if proc.poll() is not None:
                # Process exited; drain remaining queue
                continue
            continue

        if payload is None:
            ended[tag] = True
            continue

        yield payload

    proc.wait(timeout=max(timeout or 0, 1) if timeout else None)
    yield {"type": "return", "exit_code": proc.returncode}
