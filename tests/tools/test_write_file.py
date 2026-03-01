"""Tests for write_file tool."""

import os

import pytest

from llm_agent.tools.write_file import handle
from llm_agent.tools.base import shell


class TestWriteFile:
    def test_create_new_file(self, tmp_path):
        shell.cwd = str(tmp_path)
        result = handle({"path": "new.txt", "content": "hello\n"}, auto_approve=True)
        assert "wrote" in result
        assert (tmp_path / "new.txt").read_text() == "hello\n"

    def test_overwrite_existing(self, tmp_path):
        f = tmp_path / "existing.txt"
        f.write_text("old content\n")
        shell.cwd = str(tmp_path)
        result = handle({"path": "existing.txt", "content": "new content\n"}, auto_approve=True)
        assert "wrote" in result
        assert f.read_text() == "new content\n"

    def test_creates_parent_directories(self, tmp_path):
        shell.cwd = str(tmp_path)
        result = handle(
            {"path": "a/b/c/deep.txt", "content": "deep\n"},
            auto_approve=True,
        )
        assert "wrote" in result
        assert (tmp_path / "a" / "b" / "c" / "deep.txt").read_text() == "deep\n"

    def test_declined(self, declining_display, tmp_path):
        shell.cwd = str(tmp_path)
        result = handle({"path": "file.txt", "content": "data\n"})
        assert "declined" in result
        assert not (tmp_path / "file.txt").exists()

    def test_empty_content(self, tmp_path):
        shell.cwd = str(tmp_path)
        result = handle({"path": "empty.txt", "content": ""}, auto_approve=True)
        assert "wrote" in result
        assert (tmp_path / "empty.txt").read_text() == ""

    def test_line_count_in_result(self, tmp_path):
        shell.cwd = str(tmp_path)
        content = "line1\nline2\nline3\n"
        result = handle({"path": "test.txt", "content": content}, auto_approve=True)
        assert "3 lines" in result

    def test_preview_short_file(self, mock_display, tmp_path):
        shell.cwd = str(tmp_path)
        handle({"path": "short.txt", "content": "a\nb\nc\n"}, auto_approve=True)
        # auto_approved should have been called with preview lines
        assert len(mock_display.auto_approvals) == 1

    def test_preview_long_file_truncated(self, mock_display, tmp_path):
        shell.cwd = str(tmp_path)
        lines = "\n".join(f"line {i}" for i in range(20))
        handle({"path": "long.txt", "content": lines}, auto_approve=True)
        assert len(mock_display.auto_approvals) == 1
        # Preview should contain "more lines" indicator
        preview = mock_display.auto_approvals[0]
        assert any("more lines" in str(line) for line in preview)

    def test_absolute_path(self, tmp_path):
        target = tmp_path / "abs.txt"
        result = handle({"path": str(target), "content": "abs\n"}, auto_approve=True)
        assert "wrote" in result
        assert target.read_text() == "abs\n"
