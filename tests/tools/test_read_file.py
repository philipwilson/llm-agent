"""Tests for read_file tool."""

import os

import pytest

from llm_agent.tools.read_file import handle
from llm_agent.tools.base import FileObservationStore, shell


class TestReadFile:
    def test_read_basic(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line 1\nline 2\nline 3\n")
        shell.cwd = str(tmp_path)
        result = handle({"path": "test.txt"})
        assert "3 lines" in result
        assert "line 1" in result
        assert "line 2" in result
        assert "line 3" in result

    def test_line_numbers(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("aaa\nbbb\nccc\n")
        shell.cwd = str(tmp_path)
        result = handle({"path": "test.txt"})
        # Line numbers should be present
        assert "\t" in result  # tab separator in numbering

    def test_offset(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n")
        shell.cwd = str(tmp_path)
        result = handle({"path": "test.txt", "offset": 3})
        assert "line 3" in result
        assert "showing lines 3-5" in result

    def test_limit(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n")
        shell.cwd = str(tmp_path)
        result = handle({"path": "test.txt", "limit": 2})
        assert "showing lines 1-2" in result

    def test_offset_and_limit(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("\n".join(f"line {i}" for i in range(1, 11)) + "\n")
        shell.cwd = str(tmp_path)
        result = handle({"path": "test.txt", "offset": 3, "limit": 3})
        assert "showing lines 3-5" in result
        assert "use offset=6 to continue" in result

    def test_invalid_offset(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line 1\n")
        shell.cwd = str(tmp_path)
        result = handle({"path": "test.txt", "offset": 0})
        assert "offset must be >= 1" in result

    def test_offset_beyond_file_length(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line 1\nline 2\n")
        shell.cwd = str(tmp_path)
        result = handle({"path": "test.txt", "offset": 5})
        assert "exceeds file length" in result

    def test_invalid_limit(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line 1\n")
        shell.cwd = str(tmp_path)
        result = handle({"path": "test.txt", "limit": 0})
        assert "limit must be >= 1" in result

    def test_file_not_found(self, tmp_path):
        shell.cwd = str(tmp_path)
        result = handle({"path": "nonexistent.txt"})
        assert "error" in result

    def test_directory_error(self, tmp_path):
        shell.cwd = str(tmp_path)
        result = handle({"path": str(tmp_path)})
        assert "directory" in result

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("")
        shell.cwd = str(tmp_path)
        result = handle({"path": "empty.txt"})
        assert "0 lines" in result

    def test_file_size_reported(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello\n")
        shell.cwd = str(tmp_path)
        result = handle({"path": "test.txt"})
        assert "bytes" in result

    def test_absolute_path(self, tmp_path):
        f = tmp_path / "abs.txt"
        f.write_text("absolute\n")
        result = handle({"path": str(f)})
        assert "absolute" in result

    def test_records_file_observation_in_context(self, tmp_path):
        f = tmp_path / "tracked.txt"
        f.write_text("tracked\n")
        shell.cwd = str(tmp_path)
        store = FileObservationStore()

        handle({"path": "tracked.txt"}, context={"file_observations": store})

        stat_result = f.stat()
        assert store.validate_fresh(str(f), stat_result, "edit") is None

    def test_reads_latin1_text(self, tmp_path):
        f = tmp_path / "latin1.txt"
        f.write_bytes("café\n".encode("latin-1"))
        shell.cwd = str(tmp_path)

        result = handle({"path": "latin1.txt"})

        assert "café" in result
