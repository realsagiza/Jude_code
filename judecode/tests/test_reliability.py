"""Regression coverage for outcome, verification, snapshots and staged execution."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from judecode.agent import engine as engine_mod
from judecode.agent.autonomous import SelfEvaluator, SessionState
from judecode.agent.engine import AgentEngine
from judecode.api.client import ApiClient
from judecode.agent.tools import execute_tool


class ScriptedAPI:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.requests = []

    async def chat_completion(self, messages, **kwargs):
        self.requests.append(list(messages))
        response = next(self.responses, {"choices": [{"delta": {"content": "Finished response"}, "finish_reason": "stop"}]})
        if isinstance(response, Exception):
            raise response
        yield response

    def _extract_reasoning(self, chunk):
        return ""


def calls(*items, finish="tool_calls"):
    return {"choices": [{"delta": {"tool_calls": [
        {"index": i, "id": str(i), "function": {"name": name,
         "arguments": json.dumps(args) if isinstance(args, dict) else args}}
        for i, (name, args) in enumerate(items)]}, "finish_reason": finish}]}


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    obj = AgentEngine("system", ScriptedAPI())
    obj.autonomous.enabled = False
    obj.notifications.notify_task_complete = lambda **kw: None
    obj.notifications.notify_session_complete = lambda **kw: None
    return obj


@pytest.mark.parametrize("failure", [RuntimeError("context_length_exceeded"),
                                    ApiClient._error_chunk("API error 400: context too large")])
def test_api_failure_is_persisted_as_failed(agent, failure):
    agent.api = ScriptedAPI(failure)
    asyncio.run(agent.chat("work"))
    assert agent.autonomous.session.status == "failed"
    saved = SessionState.load(agent.autonomous.session.session_id)
    assert saved.status == "failed"
    assert agent.memory.get_session_summaries()[-1]["status"] == "failed"


def test_read_only_answer_is_not_implementation_completion(agent):
    asyncio.run(agent.chat("Explain a concept"))
    assert agent.autonomous.session.status == "answered"


@pytest.mark.parametrize("as_chunk", [True, False])
def test_oversized_request_recovers_without_replaying_tools(agent, as_chunk):
    original = "ผลลัพธ์" * 20000
    tool_call = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "done", "type": "function", "function": {
            "name": "shell", "arguments": '{"command":"already executed"}'}}]}
    agent.messages.extend([{"role": "user", "content": "Keep my requirements"},
                           tool_call,
                           {"role": "tool", "tool_call_id": "done", "content": original}])
    error = "API error 413: Request Entity Too Large"
    agent.api = ScriptedAPI(ApiClient._error_chunk(error) if as_chunk else RuntimeError(error))
    asyncio.run(agent.chat("Continue my work"))
    assert len(agent.api.requests) == 2
    assert agent.autonomous.session.status == "answered"
    request = agent.api.requests[-1]
    assert tool_call in request
    assert {"role": "user", "content": "Keep my requirements"} in request
    assert len(next(m for m in request if m["role"] == "tool")["content"]) < 5000
    archives = list((Path.home() / ".judecode/context-recovery").glob("*.json"))
    assert len(archives) == 1
    assert json.loads(archives[0].read_text())[-2]["content"] == original


def test_context_recovery_has_bounded_retries(agent):
    agent.messages.append({"role": "tool", "tool_call_id": "done", "content": "x" * 30000})
    agent.api = ScriptedAPI(*[ApiClient._error_chunk("API error 413") for _ in range(10)])
    asyncio.run(agent.chat("work"))
    assert len(agent.api.requests) == 4
    assert agent.autonomous.session.status == "failed"


def test_context_recovery_preserves_history_if_archive_fails(agent, monkeypatch):
    agent.messages.append({"role": "assistant", "content": "x" * 20000})
    original = list(agent.messages)
    def denied(*args, **kwargs):
        raise PermissionError("denied")
    monkeypatch.setattr("tempfile.NamedTemporaryFile", denied)
    assert not agent._recover_context()
    assert agent.messages == original


def test_context_recovery_does_not_change_user_input_or_tool_arguments(agent):
    agent.messages.append({"role": "user", "content": "x" * 30000})
    assert not agent._recover_context()


def test_cancel_is_paused(agent):
    agent.cancel_requested = True
    asyncio.run(agent.chat("work"))
    assert agent.autonomous.session.status == "paused"


def test_shell_nonzero_is_failure(agent):
    assert execute_tool("shell", {"command": "exit 7"}).startswith("Error executing tool")
    agent.api = ScriptedAPI(calls(("shell", {"command": "exit 7"})))
    asyncio.run(agent.chat("run command"))
    assert agent.autonomous.session.status == "failed"


def test_same_command_retry_can_clear_failure(agent, monkeypatch):
    args = {"command": "some command"}
    results = iter(["Error executing tool shell: failed", "ok"])
    monkeypatch.setattr(engine_mod, "execute_tool", lambda *a: next(results))
    agent._execute_tool_safe("shell", args)
    assert agent._tool_failures
    agent._execute_tool_safe("shell", args)
    assert not agent._tool_failures


@pytest.mark.parametrize("finish", ["tool_calls", "length"])
def test_all_batch_paths_snapshot_before_each_write(agent, tmp_path, finish):
    path = tmp_path / "file.txt"
    path.write_text("original")
    agent.api = ScriptedAPI(calls(("write", {"path": str(path), "content": "first"}),
                                   ("write", {"path": str(path), "content": "second"}), finish=finish))
    asyncio.run(agent._process_turn())
    assert path.read_text() == "second"
    backups = agent.backups.get_recent_backups()
    assert [Path(b["backup_path"]).read_text() for b in backups] == ["original", "first"]
    assert agent.checkpoint.rollback()["success"]
    assert path.read_text() == "first"


def test_delete_has_checkpoint(agent, tmp_path):
    path = tmp_path / "file.txt"
    path.write_text("keep")
    agent._execute_tool_safe("delete", {"path": str(path)})
    assert not path.exists()
    agent.checkpoint.rollback()
    assert path.read_text() == "keep"


def test_backup_failure_prevents_write(agent, tmp_path, monkeypatch):
    path = tmp_path / "file.txt"
    path.write_text("original")
    monkeypatch.setattr(agent.backups, "backup_file", lambda *a, **k: None)
    result = agent._execute_tool_safe("write", {"path": str(path), "content": "bad"})
    assert result.startswith("Error executing tool")
    assert path.read_text() == "original"


def test_partial_json_is_never_executed(agent, monkeypatch):
    def unexpected(*a):
        pytest.fail("Incomplete tool arguments executed")
    monkeypatch.setattr(engine_mod, "execute_tool", unexpected)
    agent.api = ScriptedAPI(calls(("write", '{"path":'), finish="length"))
    asyncio.run(agent._process_turn())
    assert agent._tool_failures
    assert agent.messages[-1]["role"] == "tool"


def test_sandbox_overlay_apply_and_backup(agent, tmp_path):
    path = tmp_path / "file.txt"
    path.write_text("original")
    agent.sandbox.activate()
    agent._execute_tool_safe("write", {"path": str(path), "content": "staged"})
    agent._execute_tool_safe("edit", {"path": str(path), "old_string": "staged", "new_string": "edited"})
    assert "edited" in agent._execute_tool_safe("read", {"path": str(path)})
    assert path.read_text() == "original"
    assert len(agent.sandbox.get_pending_changes()) == 1
    assert agent.sandbox.apply_all()["errors"] == 0
    assert path.read_text() == "edited"
    assert Path(agent.backups.get_recent_backups()[-1]["backup_path"]).read_text() == "original"
    assert agent.autonomous.session.status == "unverified"


def test_sandbox_unsupported_tools_do_not_execute(agent, monkeypatch):
    agent.sandbox.activate()
    monkeypatch.setattr(engine_mod, "execute_tool", lambda *a: pytest.fail("Escaped sandbox"))
    assert "Sandbox supports only" in agent._execute_tool_safe("shell", {"command": "echo hi"})


def test_sandbox_failed_apply_retains_changes(agent, tmp_path, monkeypatch):
    path = tmp_path / "file.txt"
    path.write_text("original")
    agent.sandbox.activate()
    agent._execute_tool_safe("write", {"path": str(path), "content": "staged"})
    monkeypatch.setattr(agent.backups, "backup_file", lambda *a, **k: None)
    assert agent.sandbox.apply_all()["errors"] == 1
    assert path.read_text() == "original"
    assert agent.sandbox.get_pending_changes()
    agent.sandbox.deactivate()
    assert agent.sandbox.is_active


def test_sandbox_parent_paths_cannot_escape_staging(agent, tmp_path):
    outside = tmp_path.parent / "outside.txt"
    stage = Path(agent.sandbox.sandbox_path(str(outside)))
    assert stage.parent == agent.sandbox.sandbox_dir


def test_missing_checks_is_unverified(tmp_path):
    result = SelfEvaluator(str(tmp_path)).run_verification()
    assert not result["passed"]
    assert result["status"] == "unverified"


def test_verification_preserves_failed_exit_code_and_cwd(tmp_path, monkeypatch):
    (tmp_path / "tests").mkdir()
    invocations = []
    def run(command, **kwargs):
        invocations.append((command, kwargs))
        return SimpleNamespace(returncode=1, stdout="failed test", stderr="")
    monkeypatch.setattr("judecode.agent.autonomous.subprocess.run", run)
    result = SelfEvaluator(str(tmp_path)).run_verification()
    assert result["status"] == "failed"
    assert invocations[0][1]["cwd"] == str(tmp_path)
    assert isinstance(invocations[0][0], list)
    assert not invocations[0][1].get("shell", False)


@pytest.mark.parametrize("status,expected", [("passed", "completed"), ("failed", "failed"), ("unverified", "unverified")])
def test_write_outcome_requires_evidence(agent, tmp_path, monkeypatch, status, expected):
    evidence = {"passed": status == "passed", "status": status, "results": [], "summary": "check evidence"}
    monkeypatch.setattr(agent.autonomous.evaluator, "run_verification", lambda: evidence)
    agent.api = ScriptedAPI(calls(("write", {"path": str(tmp_path / "code.py"), "content": "x=1"})))
    asyncio.run(agent.chat("implement change"))
    assert agent.autonomous.session.status == expected
    assert any("Verification status: " + status in (m.get("content") or "")
               for m in agent.api.requests[-1])


def test_task_completion_is_blocked_before_task_manager_changes(agent, monkeypatch):
    monkeypatch.setattr(engine_mod, "execute_tool", lambda *a: pytest.fail("Unverified task marked done"))
    result = agent._execute_tool_safe("task_complete", {"task_id": 1})
    assert "Task completion not verified" in result


def test_max_turns_is_unverified(agent, monkeypatch):
    monkeypatch.setattr(engine_mod, "MAX_TURNS", 0)
    asyncio.run(agent.chat("work"))
    assert agent.autonomous.session.status == "unverified"


def test_real_failing_pytest_is_not_hidden_by_output_truncation(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_failure.py").write_text("def test_failure():\n    assert False\n")
    result = SelfEvaluator(str(tmp_path)).run_verification()
    assert result["status"] == "failed"
    assert result["results"][0]["exit_code"] == 1


def test_successful_task_completion_checks_before_marking_done(agent, monkeypatch):
    events = []
    def verify():
        events.append("verified")
        return {"passed": True, "status": "passed", "summary": "ok", "results": []}
    monkeypatch.setattr(agent.autonomous.evaluator, "run_verification", verify)
    monkeypatch.setattr(engine_mod, "execute_tool", lambda *a: events.append("completed") or "✅ done")
    agent._execute_tool_safe("task_complete", {"task_id": 1})
    assert events == ["verified", "completed"]


def test_edit_invalidates_previous_verification(agent, tmp_path, monkeypatch):
    checks = []
    monkeypatch.setattr(agent.autonomous.evaluator, "run_verification", lambda: checks.append(1) or
                        {"passed": True, "status": "passed", "results": [], "summary": "ok"})
    path = tmp_path / "file"
    agent._execute_tool_safe("write", {"path": str(path), "content": "first"})
    agent._verify_work()
    agent._execute_tool_safe("write", {"path": str(path), "content": "second"})
    agent._verify_work()
    assert len(checks) == 2


def test_sandbox_delete_is_staged_and_discardable(agent, tmp_path):
    path = tmp_path / "file"
    path.write_text("keep")
    agent.sandbox.activate()
    agent._execute_tool_safe("delete", {"path": str(path)})
    assert path.read_text() == "keep"
    assert agent._execute_tool_safe("read", {"path": str(path)}).startswith("Error executing tool")
    agent.sandbox.discard_all()
    assert path.read_text() == "keep"


def test_checkpoint_failure_prevents_new_file(agent, tmp_path, monkeypatch):
    path = tmp_path / "new"
    monkeypatch.setattr(agent.checkpoint, "create_checkpoint", lambda **k: {"files": [{"error": "disk full"}]})
    assert agent._execute_tool_safe("write", {"path": str(path), "content": "bad"}).startswith("Error executing tool")
    assert not path.exists()


def test_stop_between_tools_skips_remaining_writes(agent, tmp_path, monkeypatch):
    actual = agent._execute_tool_safe
    def first_then_stop(*args):
        result = actual(*args)
        agent.request_stop()
        return result
    monkeypatch.setattr(agent, "_execute_tool_safe", first_then_stop)
    one, two = tmp_path / "one", tmp_path / "two"
    agent.api = ScriptedAPI(calls(("write", {"path": str(one), "content": "one"}),
                                 ("write", {"path": str(two), "content": "two"})))
    asyncio.run(agent.chat("write files"))
    assert one.exists() and not two.exists()
    assert agent.autonomous.session.status == "paused"
    assert len([m for m in agent.messages if m["role"] == "tool"]) == 2


def test_task_completion_cannot_hide_prior_tool_failure(agent, monkeypatch):
    agent._execute_tool_safe("shell", {"command": "exit 1"})
    monkeypatch.setattr(agent.autonomous.evaluator, "run_verification", lambda:
                        {"passed": True, "status": "passed", "results": [], "summary": "ok"})
    monkeypatch.setattr(engine_mod, "execute_tool", lambda *a: pytest.fail("Task falsely completed"))
    assert "Unresolved tool failures" in agent._execute_tool_safe("task_complete", {"task_id": 1})


def test_truncated_response_at_limit_is_unverified(agent):
    agent.continuation.max_continuations = 0
    agent.api = ScriptedAPI({"choices": [{"delta": {"content": "partial"}, "finish_reason": "length"}]})
    asyncio.run(agent.chat("work"))
    assert agent.autonomous.session.status == "unverified"


def test_rollback_reports_missing_snapshot_as_failure(agent, tmp_path):
    path = tmp_path / "file"
    path.write_text("old")
    cp = agent.checkpoint.create_checkpoint([str(path)])
    for snapshot in Path(cp["snapshot_dir"]).rglob("*"):
        if snapshot.is_file():
            snapshot.unlink()
    result = agent.checkpoint.rollback()
    assert not result["success"] and result["errors"]
