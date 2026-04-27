"""Tests for shell execution utility."""

from judecode.utils.shell import execute_shell


class TestExecuteShell:
    def test_simple_command(self):
        result = execute_shell("echo hello")
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]

    def test_exit_code_failure(self):
        result = execute_shell("exit 1")
        assert result["exit_code"] == 1

    def test_stderr_capture(self):
        result = execute_shell("python3 -c \"import sys; sys.stderr.write('error')\"")
        assert "error" in result["stderr"]

    def test_chaining_commands(self):
        result = execute_shell("echo a && echo b")
        assert result["exit_code"] == 0
        assert "a" in result["stdout"]
        assert "b" in result["stdout"]
