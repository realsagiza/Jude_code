"""Keep rejected tool arguments recoverable without resending invalid JSON."""
import copy
import json
import os
from pathlib import Path
import tempfile


def parse_tool_arguments(raw):
    def reject_constant(value):
        raise ValueError("Non-JSON constant: " + value)

    value = json.loads(raw, parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise ValueError("Tool arguments must be an object")
    return value


def repair_tool_history(messages):
    """Archive before repair; placeholders are history only, never executable calls.

    Preserve call IDs/results and valid arguments. Failed archival raises before
    mutation so neither user context nor the original arguments can be lost.
    """
    invalid = []
    for index, message in enumerate(messages):
        for position, call in enumerate(message.get("tool_calls", [])):
            try:
                parse_tool_arguments(call["function"]["arguments"])
            except (ValueError, TypeError):
                invalid.append((index, position))
    if not invalid:
        return messages

    directory = Path.home() / ".judecode" / "tool-argument-recovery"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
            prefix="history-", suffix=".json", dir=directory, delete=False) as stream:
        json.dump(messages, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
        archive = stream.name

    repaired = copy.deepcopy(messages)
    for index, position in invalid:
        message = repaired[index]
        call = message["tool_calls"][position]
        call["function"]["arguments"] = "{}"
        note = (f"[Recovery: tool call {call['id']} had invalid/incomplete JSON. "
                f"Its arguments are replaced with {{}} only for history transport; "
                f"do not execute this placeholder. Original history: {archive}. "
                "Consult its tool result; inspect current state before retrying "
                "with complete arguments, splitting large writes into smaller steps.]")
        content = message.get("content")
        if isinstance(content, list):
            content.append({"type": "text", "text": note})
        else:
            message["content"] = (content + "\n" if content else "") + note
    return repaired
