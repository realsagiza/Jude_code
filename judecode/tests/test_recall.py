"""Tests for the Memory Recall system (judecode/agent/recall.py)."""

import os
import tempfile

import pytest

from judecode.agent import recall as recall_mod
from judecode.agent.recall import (
    MemoryRecall,
    PreferenceStore,
    add_project_note,
    read_project_memory,
    recall_search,
    update_project_memory_file,
)


@pytest.fixture
def tmp_memory_dir(tmp_path, monkeypatch):
    """Isolate the global memory dir so tests never touch real user memory."""
    mem_dir = tmp_path / "memory"
    monkeypatch.setattr(recall_mod, "MEMORY_DIR", mem_dir)
    return mem_dir


@pytest.fixture
def tmp_project(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    return proj


class TestPreferenceStore:
    def test_add_and_list(self, tmp_memory_dir):
        ps = PreferenceStore()
        ps.add("always reply in Thai", "language")
        prefs = ps.list_all()
        assert len(prefs) == 1
        assert prefs[0]["text"] == "always reply in Thai"

    def test_dedupe(self, tmp_memory_dir):
        ps = PreferenceStore()
        ps.add("run tests before commit")
        result = ps.add("run tests before commit")
        assert "already" in result
        assert len(ps.list_all()) == 1

    def test_remove(self, tmp_memory_dir):
        ps = PreferenceStore()
        ps.add("use tabs")
        ps.add("use spaces in yaml")
        ps.remove("tabs")
        remaining = ps.list_all()
        assert len(remaining) == 1
        assert "yaml" in remaining[0]["text"]

    def test_as_text_empty(self, tmp_memory_dir):
        assert PreferenceStore().as_text() == ""


class TestProjectMemoryFile:
    def test_create_and_session_log(self, tmp_project):
        update_project_memory_file("did something important", cwd=str(tmp_project))
        content = read_project_memory(str(tmp_project))
        assert "Session Log" in content
        assert "did something important" in content

    def test_session_log_capped(self, tmp_project):
        for i in range(15):
            update_project_memory_file(f"session {i}", cwd=str(tmp_project))
        content = read_project_memory(str(tmp_project))
        assert "session 14" in content
        assert "session 0" not in content  # oldest rotated out

    def test_add_note(self, tmp_project):
        add_project_note("uses pytest", cwd=str(tmp_project))
        content = read_project_memory(str(tmp_project))
        assert "- uses pytest" in content
        # note stays above the session log
        assert content.index("uses pytest") < content.index("Session Log")

    def test_add_note_dedupe(self, tmp_project):
        add_project_note("uses pytest", cwd=str(tmp_project))
        result = add_project_note("uses pytest", cwd=str(tmp_project))
        assert "already" in result


class TestMemoryRecall:
    def test_empty_preamble_on_fresh_state(self, tmp_memory_dir, tmp_project):
        mr = MemoryRecall(memory=None, cwd=str(tmp_project))
        assert mr.build_preamble() == ""

    def test_preamble_includes_prefs_and_project_memory(
        self, tmp_memory_dir, tmp_project
    ):
        PreferenceStore().add("reply in Thai")
        add_project_note("built with FastAPI", cwd=str(tmp_project))
        mr = MemoryRecall(memory=None, cwd=str(tmp_project))
        preamble = mr.build_preamble()
        assert "MEMORY" in preamble
        assert "reply in Thai" in preamble
        assert "FastAPI" in preamble

    def test_preamble_size_capped(self, tmp_memory_dir, tmp_project):
        add_project_note("x" * 10000, cwd=str(tmp_project))
        mr = MemoryRecall(memory=None, cwd=str(tmp_project))
        assert len(mr.build_preamble()) <= 6000


class TestRecallSearch:
    def test_finds_preference(self, tmp_memory_dir, tmp_project):
        PreferenceStore().add("always use black formatter")
        result = recall_search("black", memory=None)
        assert "[preference]" in result

    def test_finds_project_note(self, tmp_memory_dir, tmp_project):
        add_project_note("database is PostgreSQL 16", cwd=str(tmp_project))
        result = recall_search("PostgreSQL", memory=None)
        assert "JUDE.md" in result

    def test_no_results(self, tmp_memory_dir, tmp_project):
        result = recall_search("zzz_nonexistent_zzz", memory=None)
        assert "No memories" in result
