"""Shell command execution utility."""

import subprocess
from typing import Optional


def execute_shell(
    command: str,
    cwd: Optional[str] = None,
    timeout: int = 120,
) -> dict:
    """
    Execute a shell command safely.
    Returns a dict with stdout, stderr, exit_code.
    """
    proc = subprocess.run(
        ["/bin/zsh", "-c", command],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
    )
    return {
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "exit_code": proc.returncode,
    }


def execute_shell_stream(
    command: str,
    cwd: Optional[str] = None,
    timeout: int = 120,
):
    """
    Execute a shell command streaming stdout/stderr.
    Yields lines of output as they arrive.
    """
    proc = subprocess.Popen(
        ["/bin/zsh", "-c", command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
    )
    # Read combined lines from both stdout and stderr
    import select
    import os

    out_fd = proc.stdout.fileno() if proc.stdout else -1
    err_fd = proc.stderr.fileno() if proc.stderr else -1
    fds = [f for f in [out_fd, err_fd] if f >= 0]

    while fds:
        ready, _, _ = select.select(fds, [], [], timeout)
        if not ready:
            break
        for fd in ready:
            line = os.read(fd, 4096).decode("utf-8", errors="replace")
            if line:
                yield line
            else:
                fds.remove(fd)

    proc.wait(timeout=timeout)
    yield {
        "type": "return",
        "exit_code": proc.returncode,
    }
