"""Tests for list_directory tool."""

import os

import pytest

from llm_agent.tools.list_directory import handle
from llm_agent.tools.base import shell


class TestListDirectory:
    def test_basic_listing(self, tmp_path):
        (tmp_path / "file.txt").write_text("hello")
        (tmp_path / "subdir").mkdir()
        shell.cwd = str(tmp_path)
        result = handle({"path": "."})
        assert "file.txt" in result
        assert "subdir/" in result

    def test_entry_count(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        shell.cwd = str(tmp_path)
        result = handle({"path": "."})
        assert "2 entries" in result

    def test_hidden_files_excluded(self, tmp_path):
        (tmp_path / ".hidden").write_text("secret")
        (tmp_path / "visible").write_text("public")
        shell.cwd = str(tmp_path)
        result = handle({"path": "."})
        assert ".hidden" not in result
        assert "visible" in result

    def test_hidden_files_included(self, tmp_path):
        (tmp_path / ".hidden").write_text("secret")
        (tmp_path / "visible").write_text("public")
        shell.cwd = str(tmp_path)
        result = handle({"path": ".", "hidden": True})
        assert ".hidden" in result
        assert "visible" in result

    def test_file_size_bytes(self, tmp_path):
        (tmp_path / "small.txt").write_text("hi")
        shell.cwd = str(tmp_path)
        result = handle({"path": "."})
        assert "2B" in result

    def test_file_size_kb(self, tmp_path):
        (tmp_path / "medium.txt").write_bytes(b"x" * 5000)
        shell.cwd = str(tmp_path)
        result = handle({"path": "."})
        assert "5.0k" in result

    def test_file_size_mb(self, tmp_path):
        (tmp_path / "large.txt").write_bytes(b"x" * 2_000_000)
        shell.cwd = str(tmp_path)
        result = handle({"path": "."})
        assert "2.0M" in result

    def test_directory_indicator(self, tmp_path):
        (tmp_path / "mydir").mkdir()
        shell.cwd = str(tmp_path)
        result = handle({"path": "."})
        assert "mydir/" in result

    def test_symlink(self, tmp_path):
        target = tmp_path / "target.txt"
        target.write_text("target")
        link = tmp_path / "link.txt"
        link.symlink_to(target)
        shell.cwd = str(tmp_path)
        result = handle({"path": "."})
        assert "link.txt ->" in result

    def test_empty_directory(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        shell.cwd = str(tmp_path)
        result = handle({"path": "empty"})
        assert "0 entries" in result
        assert "empty" in result.lower()

    def test_nonexistent_directory(self, tmp_path):
        shell.cwd = str(tmp_path)
        result = handle({"path": "nonexistent"})
        assert "error" in result

    def test_default_path(self, tmp_path):
        (tmp_path / "file.txt").write_text("hello")
        shell.cwd = str(tmp_path)
        result = handle({})
        assert "file.txt" in result

    def test_sorted_entries(self, tmp_path):
        (tmp_path / "c.txt").write_text("")
        (tmp_path / "a.txt").write_text("")
        (tmp_path / "b.txt").write_text("")
        shell.cwd = str(tmp_path)
        result = handle({"path": "."})
        lines = result.splitlines()
        # Entries should be alphabetically sorted
        entries = [l.strip().split()[0] for l in lines[1:] if l.strip()]
        assert entries == sorted(entries)

    def test_depth_lists_nested_entries(self, tmp_path):
        nested = tmp_path / "subdir"
        nested.mkdir()
        (nested / "deep.txt").write_text("hello")
        shell.cwd = str(tmp_path)

        result = handle({"path": ".", "depth": 2})

        assert "subdir/" in result
        assert "subdir/deep.txt" in result

    def test_pagination(self, tmp_path):
        for name in ("a.txt", "b.txt", "c.txt"):
            (tmp_path / name).write_text(name)
        shell.cwd = str(tmp_path)

        result = handle({"path": ".", "offset": 2, "limit": 1})

        assert "showing entries 2-2" in result
        assert "truncated; use offset=3 to continue" in result

    def test_invalid_depth(self, tmp_path):
        shell.cwd = str(tmp_path)
        result = handle({"path": ".", "depth": 0})
        assert "depth must be >= 1" in result

    def test_offset_beyond_directory_length(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        shell.cwd = str(tmp_path)
        result = handle({"path": ".", "offset": 5})
        assert "exceeds directory length" in result
