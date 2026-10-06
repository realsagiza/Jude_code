# Automatic execution reliability

Tools continue to run automatically under the existing permission settings. No new approval prompts were added.

## Outcomes

The engine persists a status and `outcome_reason` in session state and memory:

- `completed`: configured local project checks passed after changes, with no unresolved tool errors. This is evidence about those checks, not proof that every user requirement is satisfied.
- `answered`: a response was delivered without implementation work requiring verification.
- `unverified`: changes have no available checks, are only staged, or execution reached a turn/continuation limit.
- `failed`: API/context errors, unresolved tool failures, failed checks, or unexpected execution errors.
- `paused`: the user stopped execution.

Shell nonzero exit codes are tool failures. Completion notifications are sent only for `completed`. `task_complete` checks verification before changing the task manager or advancing the queue. A successful retry of the same command or file operation clears that operation's failure.

Checks run before the next model request after potentially mutating tools. The model receives their status and output. Python projects with `tests/` or `pytest.ini` use the current Python's pytest; projects with a package.json test script use npm test. Commands run with a separate cwd, without shell pipelines or automatic runner downloads. Missing checks mean unverified. Non-code conversation does not require a test suite; edited documents without a validator remain unverified.

## File changes and staging

All engine tool batches now execute in order. Single calls, multi-call responses and complete calls accompanying `finish_reason=length` share the same preflight. Malformed/truncated JSON is returned as a tool error and is never executed with empty arguments.

The `write`, `edit` and `delete` tools back up existing files and create checkpoints before executing. A failed backup/checkpoint blocks that operation. Sequential execution also prevents same-file writes within a batch racing each other. Arbitrary shell commands, external services, and other specialized mutation tools do not gain filesystem transaction/rollback guarantees from this change.

The existing legacy `/sandbox` mode now supports only `read`, `write`, `edit`, and `delete`. Reads see staged changes. Other tools, including shell, return an explicit unsupported error without executing. Each sandbox has its own directory; paths outside the project cannot escape staging. Apply creates backup/checkpoint before each real change and marks the result unverified until checked. Failed changes remain staged for retry; deactivation does not silently discard pending changes. Apply is per-file, not an atomic multi-file transaction.

Stopping skips the remaining calls in a batch after the current action finishes. It does not kill running subprocess trees. Model prose is not a trusted success signal: the persisted outcome and check results are authoritative.

## Validation and remaining scope

API size rejections (including HTTP 413 synthetic error chunks) now archive the current conversation to `~/.judecode/context-recovery/` and shorten bulky tool output and assistant text before retrying. User instructions, tool arguments, and tool/result pairing are retained. Up to three reductions are attempted; an unchanged request is never retried by this recovery path. Archive failure preserves the history unchanged. Successful requests reset the recovery counter. This does not re-execute tools or reload an already running Python process.

If instructions, tool arguments or tool definitions alone exceed the provider limit, recovery stops while retaining the conversation; reduce the oversized input or change model before `/continue`. Archives preserve the history available at rejection, after ordinary context pruning; they are not automatic cross-session restore files.

Regression tests cover isolated fake API streams and temporary files, including real failing pytest execution, 413 recovery, retry exhaustion, and archive failure. They do not call paid APIs. Model capability benchmarks, stream retry reconstruction, monetary budget enforcement, cross-session resume, semantic context summarization, packaging, and default TUI command parity remain outside this change.

## Recoverable token savings

Before every model request (including `/continue` and with autonomous mode off),
the engine replaces older tool output with an excerpt and an absolute path to a
private UTF-8 file under `~/.judecode/context-results/`. Use `read` with offset and
limit, or search that file, to recover omitted evidence without rerunning the
original command. Archive failures leave the original message intact. Existing
excerpts are stable across requests; there is no summarization API cost.

User/system messages, assistant decisions, tool arguments, and message ordering
are preserved. The last three tool results and every result in the newest batch
stay intact. This replaces the engine's previous destructive pruning and separate
80-message compaction pass. It does not summarize assistant prose or guarantee a
hard context limit; a very large recent result, user input or tool schema can still
require the existing size-rejection recovery. Archives are local data, persist
until removed by the user, and are not an automatic cross-session resume feature.

Optional environment settings (restart JudeCode after changes):

| Variable | Default | Meaning |
| --- | --- | --- |
| `JUDECODE_CONTEXT_TARGET_TOKENS` | `24000` | Soft estimated input target, including schemas; above it old excerpts shrink to 600 characters |
| `JUDECODE_CONTEXT_RESULT_CHARS` | `1200` | Normal old-result excerpt size including archive reference (minimum 500) |
| `JUDECODE_CONTEXT_RECENT_RESULTS` | `3` | Minimum number of recent full results (minimum 1; newest batch always retained) |

`/budget` now estimates full resent input on each engine request, including tool
schemas/arguments, and counts generated text/reasoning/tool calls once, including
partial or failed streams. Newly created results are charged only when sent to the
model. UTF-8 estimates account for Thai text, but are not provider tokenization or
billing usage. Rejected requests are conservatively counted; API-client-internal
retries and cache discounts are not resolved by these estimates. Dollar amounts
still use configured tracker rates; budget limits remain monitor-only. The report
also shows the current estimated context and tokens removed by pruning (not a
cumulative dollar saving or a measured provider billing reduction).
