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

Regression tests cover isolated fake API streams and temporary files, including real failing pytest execution. They do not call paid APIs. Model capability benchmarks, stream retry reconstruction, monetary budget enforcement, cross-session resume/context compaction, packaging, and default TUI command parity remain outside this change.
