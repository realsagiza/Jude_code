"""Recoverable context reduction without a summarization API call."""

import json
import hashlib
import tempfile
from pathlib import Path
from typing import Any

from judecode.utils.logger import get_logger

logger = get_logger("judecode.context")


def estimate_tokens(value: Any) -> int:
    """Conservative UTF-8 estimate, including JSON/tool arguments; not billing usage."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return (len(text.encode("utf-8")) + 2) // 3


class ContextManager:
    """Keep instructions, assistant decisions and tool protocol intact.

    Older tool bodies become recoverable excerpts. Recent results and the entire
    latest batch stay verbatim so the model can reason about fresh evidence.
    The token target is soft: never discard user requirements to fit it.
    """

    def __init__(self, target_tokens=24000, result_chars=1200, recent_results=3):
        self.target_tokens = max(1, target_tokens)
        self.result_chars = max(500, result_chars)
        self.recent_results = max(1, recent_results)
        self.saved_tokens = 0
        self.last_input_tokens = 0
        self._excerpts: set[str] = set()
        self.archived_results: dict[str, str] = {}

    def archive_references(self):
        return {path: hashlib.sha256(content.encode()).hexdigest()
                for path, content in self.archived_results.items()}

    def restore_archives(self, references):
        directory = (Path.home() / '.judecode' / 'context-results').resolve()
        if not isinstance(references, dict) or len(references) > 1000:
            return
        for name, digest in references.items():
            try:
                path = Path(name)
                if path.is_symlink() or path.resolve().parent != directory or path.suffix != '.txt' or path.stat().st_size > 2 * 1024 * 1024:
                    continue
                content = path.read_text()
                if hashlib.sha256(content.encode()).hexdigest() == digest:
                    self.archived_results[str(path)] = content
            except (OSError, TypeError, ValueError):
                continue

    def prune(self, messages: list[dict], tools: list[dict]) -> list[dict]:
        before = estimate_tokens(messages) + estimate_tokens(tools)
        limit = min(self.result_chars, 600) if before > self.target_tokens else self.result_chars
        tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
        protected = set(tool_indices[-self.recent_results:])
        # A parallel batch can exceed recent_results. Never clip unseen results.
        last_assistant = next((i for i in range(len(messages) - 1, -1, -1)
                               if messages[i].get("role") == "assistant"), len(messages))
        protected.update(i for i in tool_indices if i > last_assistant)
        result = []
        for i, message in enumerate(messages):
            content = message.get("content")
            if (message.get("role") == "tool" and i not in protected
                    and isinstance(content, str) and len(content) > limit
                    and content not in self._excerpts):
                try:
                    directory = Path.home() / ".judecode" / "context-results"
                    directory.mkdir(parents=True, exist_ok=True)
                    # Unique private file; never overwrite another session's evidence.
                    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                            suffix=".txt", dir=directory, delete=False) as archive:
                        archive.write(content)
                        path = archive.name
                    self.archived_results[path] = content
                    notice = (f"\n[Excerpt of {len(content)} chars. Full result: {path}. "
                              "Use read with offset/limit or search this file for omitted details; "
                              "do not rerun the original tool just to recover output.]\n")
                    room = max(0, limit - len(notice))
                    excerpt = content[:room // 2] + notice + content[-(room - room // 2):] if room else notice
                    # A very long home path can make the reference larger than the input.
                    if len(excerpt) < len(content):
                        self._excerpts.add(excerpt)
                        message = {**message, "content": excerpt}
                except OSError:
                    logger.warning("Cannot archive tool output; keeping full context", exc_info=True)
            result.append(message)
        result = self._compact_exchanges(result, tools)
        self.last_input_tokens = estimate_tokens(result) + estimate_tokens(tools)
        self.saved_tokens += max(0, before - self.last_input_tokens)
        return result

    def _compact_exchanges(self, messages, tools):
        """Archive old complete code-writing exchanges under sustained pressure.

        Keep every user/system instruction and the twelve newest assistant turns.
        Never split a tool batch or remove an unresolved action.
        """
        assistant_indices = [i for i, m in enumerate(messages) if m.get('role') == 'assistant']
        total = estimate_tokens(messages) + estimate_tokens(tools)
        assistant_size = sum(estimate_tokens(messages[i]) for i in assistant_indices)
        if total <= self.target_tokens or assistant_size <= self.target_tokens or len(assistant_indices) <= 12:
            return messages
        cutoff = assistant_indices[-12]
        result = []
        i = 0
        while i < len(messages):
            message = messages[i]
            calls = message.get('tool_calls', []) if message.get('role') == 'assistant' else []
            end = i + 1
            while calls and end < len(messages) and messages[end].get('role') == 'tool':
                end += 1
            batch = messages[i:end]
            complete = calls and {c.get('id') for c in calls} == {m.get('tool_call_id') for m in batch[1:]}
            if i < cutoff and complete and total > self.target_tokens and estimate_tokens(batch) > 1000:
                try:
                    directory = Path.home() / '.judecode' / 'context-results'
                    directory.mkdir(parents=True, exist_ok=True)
                    content = json.dumps(batch, ensure_ascii=False)
                    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.txt', dir=directory, delete=False) as archive:
                        archive.write(content)
                        path = archive.name
                    self.archived_results[path] = content
                    names = ', '.join(c.get('function', {}).get('name', '?') for c in calls)
                    decision = message.get('content') or ''
                    if not isinstance(decision, str):
                        decision = json.dumps(decision, ensure_ascii=False)
                    excerpt = {'role': 'assistant', 'content':
                        'Earlier completed tool exchange archived: ' + names + '. Full arguments/results: ' + path +
                        '. Read the archive for exact evidence; do not repeat actions to recover it. This is not a completion claim.\n' +
                        decision[:1000] + '\nResult excerpts:\n' + '\n'.join(str(m.get('content', ''))[-300:] for m in batch[1:])}
                    total -= estimate_tokens(batch) - estimate_tokens(excerpt)
                    result.append(excerpt)
                    i = end
                    continue
                except OSError:
                    logger.warning('Cannot archive old exchange; retaining full history')
            result.append(message)
            i += 1
        return result
