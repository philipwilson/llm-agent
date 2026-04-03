"""Tests for write_file tool."""

import os

import pytest

from llm_agent.tools.base import FileObservationStore, shell
from llm_agent.tools.read_file import handle as read_file_handle
from llm_agent.tools.write_file import handle


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

    def test_overwrite_requires_fresh_read_when_context_present(self, tmp_path):
        f = tmp_path / "existing.txt"
        f.write_text("old content\n")
        shell.cwd = str(tmp_path)
        store = FileObservationStore()

        result = handle(
            {"path": "existing.txt", "content": "new content\n"},
            auto_approve=True,
            context={"file_observations": store},
        )

        assert "must read" in result

    def test_overwrite_after_read(self, tmp_path):
        f = tmp_path / "existing.txt"
        f.write_text("old content\n")
        shell.cwd = str(tmp_path)
        store = FileObservationStore()
        context = {"file_observations": store}

        read_file_handle({"path": "existing.txt"}, context=context)
        result = handle(
            {"path": "existing.txt", "content": "new content\n"},
            auto_approve=True,
            context=context,
        )

        assert "wrote" in result
        assert f.read_text() == "new content\n"
        assert "action=overwrite" in result
        assert "format=utf-8, LF" in result

    def test_overwrite_rejects_stale_file(self, tmp_path):
        f = tmp_path / "existing.txt"
        f.write_text("old content\n")
        shell.cwd = str(tmp_path)
        store = FileObservationStore()
        context = {"file_observations": store}

        read_file_handle({"path": "existing.txt"}, context=context)
        f.write_text("changed elsewhere\n")
        result = handle(
            {"path": "existing.txt", "content": "new content\n"},
            auto_approve=True,
            context=context,
        )

        assert "changed since it was last read" in result

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
        assert "3 lines, 6 chars" in mock_display.auto_approvals[0][0]

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

    def test_overwrite_preview_includes_format_metadata(self, mock_display, tmp_path):
        f = tmp_path / "existing.txt"
        f.write_text("old content\n")
        shell.cwd = str(tmp_path)
        store = FileObservationStore()
        context = {"file_observations": store}

        read_file_handle({"path": "existing.txt"}, context=context)
        handle(
            {"path": "existing.txt", "content": "new content\n"},
            auto_approve=True,
            context=context,
        )

        preview = mock_display.auto_approvals[0]
        assert any("format: utf-8, LF" in str(line) for line in preview)

    def test_rejects_if_existing_file_changes_while_waiting_for_confirmation(self, tmp_path, mock_display):
        f = tmp_path / "existing.txt"
        f.write_text("old content\n")
        shell.cwd = str(tmp_path)
        store = FileObservationStore()
        context = {"file_observations": store}

        read_file_handle({"path": "existing.txt"}, context=context)

        def mutate_on_confirm(preview_lines, prompt_text):
            f.write_text("changed elsewhere\n")
            return True

        mock_display.confirm = mutate_on_confirm
        result = handle(
            {"path": "existing.txt", "content": "new content\n"},
            context=context,
        )

        assert "changed while waiting for confirmation" in result

    def test_rejects_if_new_file_is_created_while_waiting_for_confirmation(self, tmp_path, mock_display):
        shell.cwd = str(tmp_path)
        target = tmp_path / "race.txt"

        def create_on_confirm(preview_lines, prompt_text):
            target.write_text("appeared\n")
            return True

        mock_display.confirm = create_on_confirm
        result = handle({"path": "race.txt", "content": "new\n"})

        assert "was created while waiting for confirmation" in result

    def test_overwrite_preserves_crlf_line_endings(self, tmp_path):
        f = tmp_path / "existing.txt"
        f.write_bytes(b"old\r\ncontent\r\n")
        shell.cwd = str(tmp_path)
        store = FileObservationStore()
        context = {"file_observations": store}

        read_file_handle({"path": "existing.txt"}, context=context)
        result = handle(
            {"path": "existing.txt", "content": "new\ncontent\n"},
            auto_approve=True,
            context=context,
        )

        assert "wrote" in result
        assert f.read_bytes() == b"new\r\ncontent\r\n"

    def test_overwrite_preserves_latin1_encoding(self, tmp_path):
        f = tmp_path / "existing.txt"
        f.write_bytes("café\n".encode("latin-1"))
        shell.cwd = str(tmp_path)
        store = FileObservationStore()
        context = {"file_observations": store}

        read_file_handle({"path": "existing.txt"}, context=context)
        result = handle(
            {"path": "existing.txt", "content": "jalapeño\n"},
            auto_approve=True,
            context=context,
        )

        assert "wrote" in result
        assert f.read_bytes() == "jalapeño\n".encode("latin-1")

    def test_overwrite_reports_unencodable_content(self, tmp_path):
        f = tmp_path / "existing.txt"
        f.write_bytes("café\n".encode("latin-1"))
        shell.cwd = str(tmp_path)
        store = FileObservationStore()
        context = {"file_observations": store}

        read_file_handle({"path": "existing.txt"}, context=context)
        result = handle(
            {"path": "existing.txt", "content": "emoji 😀\n"},
            auto_approve=True,
            context=context,
        )

        assert "cannot be encoded" in result

    def test_rejects_obvious_omission_placeholder(self, tmp_path):
        shell.cwd = str(tmp_path)

        result = handle(
            {"path": "new.txt", "content": "... existing code ...\n"},
            auto_approve=True,
        )

        assert "omission placeholder" in result
