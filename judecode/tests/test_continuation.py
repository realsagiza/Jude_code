"""Regression tests for the continuation / nudge detection system.

These guard against the false-positive bug where normal tool output that
merely *contained* phrases like "error executing tool", "timeout" or
"connection" (e.g. when `read`/`grep` returned source code) would wrongly
trigger an automatic continuation nudge.
"""

from judecode.agent.continuation import (
    detect_tool_error,
    detect_incomplete_work,
    detect_completion,
    detect_stream_interruption,
)


class TestDetectToolError:
    def test_real_error_is_detected(self):
        assert detect_tool_error("Error executing tool 'shell': TypeError: ...") is True

    def test_real_error_with_leading_whitespace(self):
        assert detect_tool_error("  Error executing tool 'read': FileNotFoundError") is True

    def test_empty_is_not_error(self):
        assert detect_tool_error("") is False

    def test_source_code_containing_phrase_is_not_error(self):
        # grep/read returning source that mentions the phrase mid-line
        src = 'grep result: return f"Error executing tool \'{name}\'"\n2 matches'
        assert detect_tool_error(src) is False

    def test_output_with_keyword_timeout_is_not_error(self):
        assert detect_tool_error("def connect(timeout=30):\n    pass") is False

    def test_output_with_keyword_connection_is_not_error(self):
        assert detect_tool_error("Established connection to database") is False

    def test_normal_output_is_not_error(self):
        assert detect_tool_error("File written successfully: /tmp/x.txt") is False


class TestDetectIncompleteWork:
    def test_clean_tool_results_not_incomplete(self):
        # Output containing the phrase but not an actual error
        results = ['2 matches: "Error executing tool" found in source']
        assert detect_incomplete_work("Here is the count.", results) is False

    def test_genuine_error_marks_incomplete(self):
        results = ["Error executing tool 'shell': command failed"]
        assert detect_incomplete_work("", results) is True


class TestDetectCompletion:
    def test_completion_phrase(self):
        assert detect_completion("All done! Let me know if you need anything else.") is True

    def test_non_completion(self):
        assert detect_completion("Reading the next file now") is False


class TestDetectStreamInterruption:
    def test_real_interruption(self):
        assert detect_stream_interruption("Stream interrupted after partial content") is True

    def test_empty(self):
        assert detect_stream_interruption("") is False
