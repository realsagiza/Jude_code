"""Tests for file operations utility."""

import os
import tempfile
from pathlib import Path

import pytest

from judecode.utils.file_ops import (
    read_file,
    write_file,
    edit_file,
    delete_file,
    list_directory,
)


class TestReadFile:
    def test_read_existing_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("line1\nline2\nline3\n")
            path = f.name
        try:
            content = read_file(path)
            assert content == "line1\nline2\nline3\n"
        finally:
            os.unlink(path)

    def test_read_with_offset_and_limit(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("line1\nline2\nline3\nline4\nline5\n")
            path = f.name
        try:
            content = read_file(path, offset=2, limit=2)
            assert content == "line2\nline3\n"
        finally:
            os.unlink(path)

    def test_read_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            read_file("/nonexistent/path/file.txt")

    def test_read_directory_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with pytest.raises(IsADirectoryError):
                read_file(d)


class TestWriteFile:
    def test_write_new_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "test.txt")
            write_file(path, "hello world")
            assert Path(path).read_text() == "hello world"

    def test_write_creates_directories(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "subdir", "subsubdir", "file.txt")
            write_file(path, "content")
            assert Path(path).is_file()
            assert Path(path).read_text() == "content"

    def test_overwrite_existing_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("old")
            path = f.name
        try:
            write_file(path, "new")
            assert read_file(path) == "new"
        finally:
            os.unlink(path)


class TestEditFile:
    def test_edit_unique_string(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hello world old value good bye")
            path = f.name
        try:
            edit_file(path, "old value", "new value")
            assert read_file(path) == "hello world new value good bye"
        finally:
            os.unlink(path)

    def test_edit_nonexistent_text(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hello")
            path = f.name
        try:
            with pytest.raises(ValueError, match="Could not find"):
                edit_file(path, "nonexistent", "replacement")
        finally:
            os.unlink(path)

    def test_edit_non_unique_raises(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hello hello")
            path = f.name
        try:
            with pytest.raises(ValueError, match="Found 2 occurrences"):
                edit_file(path, "hello", "hi")
        finally:
            os.unlink(path)


class TestDeleteFile:
    def test_delete_existing_file(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            path = f.name
        assert Path(path).exists()
        delete_file(path)
        assert not Path(path).exists()

    def test_delete_nonexistent_raises(self):
        with pytest.raises(FileNotFoundError):
            delete_file("/nonexistent/file.txt")


class TestListDirectory:
    def test_list_current_directory(self):
        with tempfile.TemporaryDirectory() as d:
            write_file(os.path.join(d, "file1.txt"), "")
            Path(d).joinpath("folder").mkdir()
            result = list_directory(d)
            assert "folder/" in result
            assert "file1.txt" in result

    def test_list_not_a_directory(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name
        try:
            with pytest.raises(NotADirectoryError):
                list_directory(path)
        finally:
            os.unlink(path)

    def test_list_empty_directory(self):
        with tempfile.TemporaryDirectory() as d:
            result = list_directory(d)
            assert result == ""
