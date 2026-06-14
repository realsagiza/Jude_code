"""Regression tests for two engine reliability fixes:

1. Ctrl+C (cancel_requested) must abort a streaming response IMMEDIATELY
   and STOP — it must not keep flowing or auto-continue.

2. A "context too large" / context_length_exceeded API error must STOP the
   loop instead of auto-continuing forever (which would re-send the same
   oversized context and loop infinitely).
"""

import asyncio

import pytest

from judecode.agent.engine import AgentEngine


# ── Fake API clients ────────────────────────────────────────────────────


class _LongStreamAPI:
    """Emits many content chunks (simulates a long response)."""

    async def chat_completion(self, messages, tools=None, stream=True):
        for i in range(100):
            yield {"choices": [{"delta": {"content": f"token{i} "}, "finish_reason": None}]}

    def _extract_reasoning(self, chunk):
        return ""


class _OverflowAPI:
    """Yields a little, then raises a context-length error (like a real 400)."""

    async def chat_completion(self, messages, tools=None, stream=True):
        yield {"choices": [{"delta": {"content": "partial "}, "finish_reason": None}]}
        raise RuntimeError(
            "API error 400: context_length_exceeded - prompt is too long"
        )

    def _extract_reasoning(self, chunk):
        return ""


class _NormalAPI:
    """A clean finished response with no tool calls."""

    async def chat_completion(self, messages, tools=None, stream=True):
        for w in ["Hello", " world", ". Done!"]:
            yield {"choices": [{"delta": {"content": w}, "finish_reason": None}]}
        yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

    def _extract_reasoning(self, chunk):
        return ""


# ── Context overflow detection (static helper) ──────────────────────────


class TestContextOverflowDetection:
    def test_detects_common_overflow_messages(self):
        f = AgentEngine._is_context_overflow_error
        assert f("API error 400: context_length_exceeded")
        assert f("This model maximum context length is 128000 tokens")
        assert f("Anthropic API error 400: prompt is too long")
        assert f("Error: request too large")
        assert f("string too long")
        assert f("API error 413: payload too large")

    def test_ignores_unrelated_errors(self):
        f = AgentEngine._is_context_overflow_error
        assert not f("Stream error: ConnectError: connection refused")
        assert not f("API error 500: internal server error")
        assert not f("API error 429: rate limit exceeded")
        assert not f("")


# ── Behaviour of _process_turn under interruption / overflow ────────────


class TestProcessTurnStops:
    def test_ctrl_c_aborts_stream_and_stops(self):
        agent = AgentEngine("sys", _LongStreamAPI())
        agent.cancel_requested = True  # simulate Ctrl+C before/while streaming

        should_continue = asyncio.run(agent._process_turn(turn_number=1))

        assert should_continue is False, "must stop when cancelled"
        assert agent.cancel_requested is False, "cancel flag must be reset"

    def test_context_overflow_stops_without_continuation(self):
        agent = AgentEngine("sys", _OverflowAPI())

        should_continue = asyncio.run(agent._process_turn(turn_number=1))

        assert should_continue is False, "overflow must STOP, not loop"
        assert agent.continuation.had_stream_error is False, (
            "overflow must NOT be treated as a retryable stream error"
        )

    def test_normal_response_still_works(self):
        agent = AgentEngine("sys", _NormalAPI())

        should_continue = asyncio.run(agent._process_turn(turn_number=1))

        assert should_continue is False  # no tool calls → done
        last = agent.messages[-1]
        assert last["role"] == "assistant"
        assert "Hello world" in last["content"]


class TestChatLoopRespectsCancel:
    def test_chat_stops_before_new_turn_when_cancelled(self):
        agent = AgentEngine("sys", _NormalAPI())

        # Patch _process_turn to request a stop and ask for another turn.
        calls = {"n": 0}

        async def fake_turn(turn_number=1):
            calls["n"] += 1
            agent.cancel_requested = True
            return True  # ask the loop to continue (it should NOT, due to cancel)

        agent._process_turn = fake_turn
        asyncio.run(agent.chat("hi"))

        # Loop ran exactly one turn then bailed on the cancel check.
        assert calls["n"] == 1
        assert agent.cancel_requested is False
