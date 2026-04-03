"""Tests for edit_file tool: fuzzy matching, line ranges, batch edits."""

import os

import pytest

from llm_agent.tools.edit_file import (
    _normalize_ws,
    _fuzzy_find,
    _validate_single_edit,
    handle,
)
from llm_agent.tools.base import FileObservationStore, shell
from llm_agent.tools.read_file import handle as read_file_handle


class TestNormalizeWs:
    def test_collapses_spaces(self):
        assert _normalize_ws("a  b   c") == "a b c"

    def test_collapses_tabs(self):
        assert _normalize_ws("a\t\tb") == "a b"

    def test_strips_trailing(self):
        assert _normalize_ws("hello   ") == "hello"

    def test_multiline(self):
        assert _normalize_ws("a  b\n  c  d  ") == "a b\n c d"

    def test_empty_string(self):
        assert _normalize_ws("") == ""


class TestFuzzyFind:
    def test_exact_match_not_used(self):
        """fuzzy_find should still work when content matches exactly."""
        content = "hello world"
        result = _fuzzy_find(content, "hello world")
        assert result == (0, 11)

    def test_whitespace_difference(self):
        content = "hello   world"
        result = _fuzzy_find(content, "hello world")
        assert result is not None
        start, end = result
        assert content[start:end] == "hello   world"

    def test_tab_vs_space(self):
        content = "hello\tworld"
        result = _fuzzy_find(content, "hello world")
        assert result is not None

    def test_no_match(self):
        result = _fuzzy_find("hello world", "goodbye")
        assert result is None

    def test_multiple_matches(self):
        """Multiple matches should return None (ambiguous)."""
        result = _fuzzy_find("ab ab", "ab")
        assert result is None


class TestValidateSingleEdit:
    def _content_and_lines(self, text):
        return text, text.splitlines()

    def test_string_match(self):
        content, lines = self._content_and_lines("hello world\ngoodbye world\n")
        old, new, start, end, fuzzy, err = _validate_single_edit(
            {"old_string": "hello world", "new_string": "hi world"},
            content, lines,
        )
        assert err is None
        assert old == "hello world"
        assert not fuzzy

    def test_string_not_found(self):
        content, lines = self._content_and_lines("hello world\n")
        _, _, _, _, _, err = _validate_single_edit(
            {"old_string": "nonexistent", "new_string": "x"},
            content, lines,
        )
        assert err is not None
        assert "not found" in err
        assert "Re-read the file" in err

    def test_multiple_matches_error(self):
        content, lines = self._content_and_lines("ab\nab\n")
        _, _, _, _, _, err = _validate_single_edit(
            {"old_string": "ab", "new_string": "x"},
            content, lines,
        )
        assert "matches 2" in err
        assert "line 1" in err

    def test_line_range(self):
        content, lines = self._content_and_lines("line1\nline2\nline3\n")
        old, new, start, end, fuzzy, err = _validate_single_edit(
            {"start_line": 2, "end_line": 2, "new_string": "replaced\n"},
            content, lines,
        )
        assert err is None
        assert "line2" in old

    def test_line_range_invalid(self):
        content, lines = self._content_and_lines("line1\nline2\n")
        _, _, _, _, _, err = _validate_single_edit(
            {"start_line": 1, "end_line": 5, "new_string": "x"},
            content, lines,
        )
        assert err is not None
        assert "exceeds" in err

    def test_cannot_combine_modes(self):
        content, lines = self._content_and_lines("hello\n")
        _, _, _, _, _, err = _validate_single_edit(
            {"old_string": "hello", "start_line": 1, "end_line": 1, "new_string": "x"},
            content, lines,
        )
        assert "cannot combine" in err

    def test_must_provide_something(self):
        content, lines = self._content_and_lines("hello\n")
        _, _, _, _, _, err = _validate_single_edit(
            {"new_string": "x"},
            content, lines,
        )
        assert "must provide" in err


class TestHandle:
    def test_requires_fresh_read_when_context_present(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world\n")
        shell.cwd = str(tmp_path)
        store = FileObservationStore()

        result = handle(
            {"path": "test.txt", "old_string": "hello", "new_string": "hi"},
            auto_approve=True,
            context={"file_observations": store},
        )

        assert "must read" in result

    def test_string_replace_after_read(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world\n")
        shell.cwd = str(tmp_path)
        store = FileObservationStore()
        context = {"file_observations": store}

        read_file_handle({"path": "test.txt"}, context=context)
        result = handle(
            {"path": "test.txt", "old_string": "hello", "new_string": "hi"},
            auto_approve=True,
            context=context,
        )

        assert "edited" in result
        assert f.read_text() == "hi world\n"
        assert "lines=1->1" in result
        assert "format=utf-8, LF" in result

    def test_rejects_stale_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world\n")
        shell.cwd = str(tmp_path)
        store = FileObservationStore()
        context = {"file_observations": store}

        read_file_handle({"path": "test.txt"}, context=context)
        f.write_text("changed elsewhere\n")
        result = handle(
            {"path": "test.txt", "old_string": "changed", "new_string": "updated"},
            auto_approve=True,
            context=context,
        )

        assert "changed since it was last read" in result

    def test_string_replace(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world\n")
        shell.cwd = str(tmp_path)
        result = handle(
            {"path": "test.txt", "old_string": "hello", "new_string": "hi"},
            auto_approve=True,
        )
        assert "edited" in result
        assert f.read_text() == "hi world\n"

    def test_line_range_replace(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3\n")
        shell.cwd = str(tmp_path)
        result = handle(
            {"path": "test.txt", "start_line": 2, "end_line": 2, "new_string": "replaced\n"},
            auto_approve=True,
        )
        assert "edited" in result
        assert f.read_text() == "line1\nreplaced\nline3\n"

    def test_batch_edits(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("aaa\nbbb\nccc\n")
        shell.cwd = str(tmp_path)
        result = handle(
            {"path": "test.txt", "edits": [
                {"old_string": "aaa", "new_string": "AAA"},
                {"old_string": "ccc", "new_string": "CCC"},
            ]},
            auto_approve=True,
        )
        assert "2 edits" in result
        assert f.read_text() == "AAA\nbbb\nCCC\n"

    def test_preview_includes_summary_metadata(self, mock_display, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world\n")
        shell.cwd = str(tmp_path)
        store = FileObservationStore()
        context = {"file_observations": store}

        read_file_handle({"path": "test.txt"}, context=context)
        handle(
            {"path": "test.txt", "old_string": "hello", "new_string": "hi"},
            auto_approve=True,
            context=context,
        )

        preview = mock_display.auto_approvals[0]
        assert any("summary:" in str(line) for line in preview)
        assert any("format: utf-8, LF" in str(line) for line in preview)

    def test_file_not_found(self, tmp_path):
        shell.cwd = str(tmp_path)
        result = handle({"path": "nope.txt", "old_string": "x", "new_string": "y"})
        assert "error" in result

    def test_declined(self, declining_display, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("original\n")
        shell.cwd = str(tmp_path)
        result = handle({"path": "test.txt", "old_string": "original", "new_string": "changed"})
        assert "declined" in result
        assert f.read_text() == "original\n"

    def test_overlapping_edits_rejected(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world\n")
        shell.cwd = str(tmp_path)
        result = handle(
            {"path": "test.txt", "edits": [
                {"old_string": "hello world", "new_string": "x"},
                {"old_string": "world", "new_string": "y"},
            ]},
            auto_approve=True,
        )
        assert "overlapping" in result

    def test_rejects_if_file_changes_while_waiting_for_confirmation(self, tmp_path, mock_display):
        f = tmp_path / "test.txt"
        f.write_text("hello world\n")
        shell.cwd = str(tmp_path)
        store = FileObservationStore()
        context = {"file_observations": store}

        read_file_handle({"path": "test.txt"}, context=context)

        def mutate_on_confirm(preview_lines, prompt_text):
            f.write_text("changed elsewhere\n")
            return True

        mock_display.confirm = mutate_on_confirm
        result = handle(
            {"path": "test.txt", "old_string": "hello", "new_string": "hi"},
            context=context,
        )

        assert "changed while waiting for confirmation" in result

    def test_preserves_crlf_line_endings(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_bytes(b"line1\r\nline2\r\n")
        shell.cwd = str(tmp_path)
        store = FileObservationStore()
        context = {"file_observations": store}

        read_file_handle({"path": "test.txt"}, context=context)
        result = handle(
            {"path": "test.txt", "start_line": 2, "end_line": 2, "new_string": "replaced\n"},
            auto_approve=True,
            context=context,
        )

        assert "edited" in result
        assert f.read_bytes() == b"line1\r\nreplaced\r\n"

    def test_preserves_latin1_encoding(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_bytes("café\n".encode("latin-1"))
        shell.cwd = str(tmp_path)
        store = FileObservationStore()
        context = {"file_observations": store}

        read_file_handle({"path": "test.txt"}, context=context)
        result = handle(
            {"path": "test.txt", "old_string": "café", "new_string": "cañón"},
            auto_approve=True,
            context=context,
        )

        assert "edited" in result
        assert f.read_bytes() == "cañón\n".encode("latin-1")

    def test_rejects_obvious_omission_placeholder(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world\n")
        shell.cwd = str(tmp_path)
        store = FileObservationStore()
        context = {"file_observations": store}

        read_file_handle({"path": "test.txt"}, context=context)
        result = handle(
            {
                "path": "test.txt",
                "old_string": "hello world",
                "new_string": "... existing code ...",
            },
            auto_approve=True,
            context=context,
        )

        assert "omission placeholder" in result

    def test_not_found_error_suggests_close_match_recovery(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello brave new world\n")
        shell.cwd = str(tmp_path)
        store = FileObservationStore()
        context = {"file_observations": store}

        read_file_handle({"path": "test.txt"}, context=context)
        result = handle(
            {
                "path": "test.txt",
                "old_string": "hello new world",
                "new_string": "hi world",
            },
            auto_approve=True,
            context=context,
        )

        assert "Closest matches" in result
        assert "hello brave new world" in result
        assert "start_line/end_line or apply_patch" in result

    def test_ambiguous_match_error_lists_match_lines(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("value = 1\nother = 2\nvalue = 1\n")
        shell.cwd = str(tmp_path)
        store = FileObservationStore()
        context = {"file_observations": store}

        read_file_handle({"path": "test.txt"}, context=context)
        result = handle(
            {
                "path": "test.txt",
                "old_string": "value = 1",
                "new_string": "value = 3",
            },
            auto_approve=True,
            context=context,
        )

        assert "matches 2 locations" in result
        assert "line 1" in result
        assert "line 3" in result

    def test_line_range_error_suggests_read_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("one\ntwo\n")
        shell.cwd = str(tmp_path)
        store = FileObservationStore()
        context = {"file_observations": store}

        read_file_handle({"path": "test.txt"}, context=context)
        result = handle(
            {
                "path": "test.txt",
                "start_line": 1,
                "end_line": 5,
                "new_string": "replacement\n",
            },
            auto_approve=True,
            context=context,
        )

        assert "use read_file to confirm the current line numbers" in result
