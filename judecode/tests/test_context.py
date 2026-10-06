"""Recoverability, protocol continuity and per-request token accounting."""
import asyncio
import copy
from pathlib import Path


from judecode.agent.context import ContextManager, estimate_tokens
from judecode.agent.tools import TOOL_DEFINITIONS
from .test_reliability import agent, ScriptedAPI, calls


def history():
    messages = [{"role": "system", "content": "System rules"},
                {"role": "user", "content": "ห้ามลบข้อมูล และทำงานต่อให้เสร็จ"}]
    for i in range(12):
        messages.extend([
            {"role": "assistant", "content": f"Decision {i}",
             "reasoning_content": "provider-required reasoning",
             "tool_calls": [{"id": str(i), "type": "function", "function": {
                 "name": "shell", "arguments": '{"command":"test"}'}}]},
            {"role": "tool", "tool_call_id": str(i),
             "content": "head\n" + ("evidence in the middle\n" * 1000) + "exit code: 1\n"}])
    return messages


def test_reduction_is_recoverable_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    original = history()
    untouched = copy.deepcopy(original)
    manager = ContextManager()
    reduced = manager.prune(original, [])
    assert original == untouched
    assert estimate_tokens(reduced) < estimate_tokens(original) * .35
    assert reduced[-6:] == original[-6:]
    for before, after in zip(original, reduced):
        if before["role"] != "tool":
            assert before == after
        else:
            assert before["tool_call_id"] == after["tool_call_id"]
    archives = list((tmp_path / ".judecode/context-results").glob("*.txt"))
    assert len(archives) == 9
    for archive in archives:
        assert archive.read_text() == original[3]["content"]
        assert any(str(archive) in m.get("content", "") for m in reduced)
        assert archive.stat().st_mode & 0o077 == 0
    assert manager.prune(reduced, []) == reduced
    assert len(list(archives[0].parent.glob("*.txt"))) == 9


def test_latest_large_batch_and_user_instructions_are_never_cut(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    messages = history()[:2]
    messages.append({"role": "assistant", "tool_calls": [{"id": str(i)} for i in range(8)]})
    messages.extend({"role": "tool", "tool_call_id": str(i), "content": "x" * 50000} for i in range(8))
    assert ContextManager(target_tokens=1).prune(messages, []) == messages


def test_archive_failure_preserves_full_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr("judecode.agent.context.tempfile.NamedTemporaryFile", fail)
    messages = history()
    assert ContextManager().prune(messages, []) == messages


def test_soft_target_counts_schemas_and_unicode(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    messages = history()
    manager = ContextManager(target_tokens=10**9)
    standard = manager.prune(messages, [])
    pressure = ContextManager(target_tokens=1).prune(messages, [{"schema": "x" * 100}])
    assert len(pressure[3]["content"]) < len(standard[3]["content"])
    assert estimate_tokens("ภาษาไทย") > estimate_tokens("abcdefg")


def test_every_request_counts_full_context_and_tools_without_double_output(agent):
    agent.api = ScriptedAPI()
    agent.messages.append({"role": "user", "content": "hello"})
    assert agent.autonomous.budget.total_input_tokens == 0
    expected = sum(estimate_tokens(m) for m in agent.messages) + estimate_tokens(TOOL_DEFINITIONS)
    asyncio.run(agent._process_turn(1))
    budget = agent.autonomous.budget
    assert budget.total_input_tokens == expected
    assert budget.total_output_tokens == estimate_tokens("Finished response")
    second = sum(estimate_tokens(m) for m in agent.messages) + estimate_tokens(TOOL_DEFINITIONS)
    asyncio.run(agent._process_turn(2))
    assert budget.total_input_tokens == expected + second
    assert budget.total_output_tokens == 2 * estimate_tokens("Finished response")
    assert budget.turn_count == 2
    assert sum(budget.tokens.values()) == budget.total_input_tokens + budget.total_output_tokens


def test_tool_turn_accounts_arguments_but_result_only_when_sent(agent, monkeypatch):
    agent.api = ScriptedAPI(calls(("read", {"path": "file"})))
    monkeypatch.setattr(agent, "_execute_tool_safe", lambda *args: "large result " * 1000)
    asyncio.run(agent._process_turn(1))
    budget = agent.autonomous.budget
    assert budget.total_output_tokens > 0
    assert budget.tokens["tool_results"] == 0
    assert budget.turn_count == 1
    asyncio.run(agent._process_turn(2))
    assert budget.tokens["tool_results"] > 0
    assert budget.turn_count == 2


def test_partial_failed_stream_is_accounted(agent):
    class BrokenAPI(ScriptedAPI):
        async def chat_completion(self, *args, **kwargs):
            yield {"choices": [{"delta": {"content": "partial text"}}]}
            raise RuntimeError("connection lost")
    agent.api = BrokenAPI()
    asyncio.run(agent._process_turn(1))
    assert agent.autonomous.budget.total_output_tokens == estimate_tokens("partial text")
    assert agent.autonomous.budget.turn_count == 1


def test_long_engine_history_preserves_requirements_and_pairs(agent):
    messages = history()
    # Cross the old 80-message threshold and exercise the actual request path.
    messages += copy.deepcopy(messages[2:]) * 3
    agent.messages = messages
    original = copy.deepcopy(messages)
    asyncio.run(agent._process_turn(1))
    sent = agent.api.requests[0]
    assert len(sent) == len(original)
    assert [m for m in sent if m["role"] != "tool"] == [
        m for m in original if m["role"] != "tool"]
    assert [m.get("tool_call_id") for m in sent] == [
        m.get("tool_call_id") for m in original]
    assert agent.autonomous.budget.context_tokens_saved > 0
    assert "Estimated tokens" in agent.autonomous.budget.get_status()
