"""Tests for apply_patch tool."""

from llm_agent.tools.apply_patch import _parse_patch, handle
from llm_agent.tools.base import FileObservationStore, shell
from llm_agent.tools.read_file import handle as read_file_handle


class TestParsePatch:
    def test_requires_begin_marker(self):
        try:
            _parse_patch("*** Add File: test.txt\n+hello\n*** End Patch")
        except Exception as e:
            assert "Begin Patch" in str(e)
        else:
            raise AssertionError("expected parse error")

    def test_parses_move_only_update(self):
        ops = _parse_patch(
            "*** Begin Patch\n"
            "*** Update File: old.txt\n"
            "*** Move to: new.txt\n"
            "*** End Patch"
        )

        assert ops == [{
            "type": "update",
            "path": "old.txt",
            "move_to": "new.txt",
            "hunks": [],
            "eof": False,
        }]


class TestHandle:
    def test_parse_error_includes_grammar_hint(self, tmp_path):
        shell.cwd = str(tmp_path)
        result = handle(
            {"patch": "*** Update File: test.txt\n@@\n-hello\n+hi\n*** End Patch"},
            auto_approve=True,
        )

        assert "Begin Patch" in result
        assert "Use *** Begin Patch" in result

    def test_add_file(self, tmp_path):
        shell.cwd = str(tmp_path)
        result = handle(
            {
                "patch": (
                    "*** Begin Patch\n"
                    "*** Add File: new.txt\n"
                    "+hello\n"
                    "+world\n"
                    "*** End Patch"
                )
            },
            auto_approve=True,
        )

        assert (tmp_path / "new.txt").read_text() == "hello\nworld\n"
        assert "added=1" in result
        assert "add " in result

    def test_update_file_after_read(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello\n")
        shell.cwd = str(tmp_path)
        store = FileObservationStore()
        context = {"file_observations": store}

        read_file_handle({"path": "test.txt"}, context=context)
        result = handle(
            {
                "patch": (
                    "*** Begin Patch\n"
                    "*** Update File: test.txt\n"
                    "@@\n"
                    "-hello\n"
                    "+hi\n"
                    "*** End Patch"
                )
            },
            auto_approve=True,
            context=context,
        )

        assert f.read_text() == "hi\n"
        assert "updated=1" in result
        assert "format=utf-8, LF" in result

    def test_update_requires_fresh_read(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello\n")
        shell.cwd = str(tmp_path)

        result = handle(
            {
                "patch": (
                    "*** Begin Patch\n"
                    "*** Update File: test.txt\n"
                    "@@\n"
                    "-hello\n"
                    "+hi\n"
                    "*** End Patch"
                )
            },
            auto_approve=True,
            context={"file_observations": FileObservationStore()},
        )

        assert "must read" in result

    def test_delete_after_read(self, tmp_path):
        f = tmp_path / "delete.txt"
        f.write_text("gone\n")
        shell.cwd = str(tmp_path)
        store = FileObservationStore()
        context = {"file_observations": store}

        read_file_handle({"path": "delete.txt"}, context=context)
        result = handle(
            {
                "patch": (
                    "*** Begin Patch\n"
                    "*** Delete File: delete.txt\n"
                    "*** End Patch"
                )
            },
            auto_approve=True,
            context=context,
        )

        assert not f.exists()
        assert "deleted=1" in result

    def test_move_file_after_read(self, tmp_path):
        source = tmp_path / "old.txt"
        source.write_text("move me\n")
        shell.cwd = str(tmp_path)
        store = FileObservationStore()
        context = {"file_observations": store}

        read_file_handle({"path": "old.txt"}, context=context)
        result = handle(
            {
                "patch": (
                    "*** Begin Patch\n"
                    "*** Update File: old.txt\n"
                    "*** Move to: new.txt\n"
                    "*** End Patch"
                )
            },
            auto_approve=True,
            context=context,
        )

        assert not source.exists()
        assert (tmp_path / "new.txt").read_text() == "move me\n"
        assert "moved=1" in result

    def test_move_and_update_file_after_read(self, tmp_path):
        source = tmp_path / "old.txt"
        source.write_text("hello\n")
        shell.cwd = str(tmp_path)
        store = FileObservationStore()
        context = {"file_observations": store}

        read_file_handle({"path": "old.txt"}, context=context)
        result = handle(
            {
                "patch": (
                    "*** Begin Patch\n"
                    "*** Update File: old.txt\n"
                    "*** Move to: new.txt\n"
                    "@@\n"
                    "-hello\n"
                    "+hi\n"
                    "*** End Patch"
                )
            },
            auto_approve=True,
            context=context,
        )

        assert not source.exists()
        assert (tmp_path / "new.txt").read_text() == "hi\n"
        assert "move+update" in result

    def test_rejects_omission_placeholder(self, tmp_path):
        shell.cwd = str(tmp_path)
        result = handle(
            {
                "patch": (
                    "*** Begin Patch\n"
                    "*** Add File: new.txt\n"
                    "+... existing code ...\n"
                    "*** End Patch"
                )
            },
            auto_approve=True,
        )

        assert "omission placeholder" in result

    def test_preview_includes_summary_metadata(self, mock_display, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello\n")
        shell.cwd = str(tmp_path)
        store = FileObservationStore()
        context = {"file_observations": store}

        read_file_handle({"path": "test.txt"}, context=context)
        handle(
            {
                "patch": (
                    "*** Begin Patch\n"
                    "*** Update File: test.txt\n"
                    "@@\n"
                    "-hello\n"
                    "+hi\n"
                    "*** End Patch"
                )
            },
            auto_approve=True,
            context=context,
        )

        preview = mock_display.auto_approvals[0]
        assert any("summary:" in str(line) for line in preview)
        assert any("format: utf-8, LF" in str(line) for line in preview)

    def test_rejects_duplicate_paths(self, tmp_path):
        shell.cwd = str(tmp_path)
        result = handle(
            {
                "patch": (
                    "*** Begin Patch\n"
                    "*** Add File: a.txt\n"
                    "+one\n"
                    "*** Add File: a.txt\n"
                    "+two\n"
                    "*** End Patch"
                )
            },
            auto_approve=True,
        )

        assert "more than once" in result

    def test_unmatched_hunk_error_includes_recovery_guidance(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello\n")
        shell.cwd = str(tmp_path)
        store = FileObservationStore()
        context = {"file_observations": store}

        read_file_handle({"path": "test.txt"}, context=context)
        result = handle(
            {
                "patch": (
                    "*** Begin Patch\n"
                    "*** Update File: test.txt\n"
                    "@@\n"
                    "-goodbye\n"
                    "+hi\n"
                    "*** End Patch"
                )
            },
            auto_approve=True,
            context=context,
        )

        assert "test.txt: update hunk could not be matched" in result
        assert "goodbye" in result
        assert "re-read the file and regenerate the patch" in result

    def test_ambiguous_hunk_error_asks_for_more_context(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello\nmiddle\nhello\n")
        shell.cwd = str(tmp_path)
        store = FileObservationStore()
        context = {"file_observations": store}

        read_file_handle({"path": "test.txt"}, context=context)
        result = handle(
            {
                "patch": (
                    "*** Begin Patch\n"
                    "*** Update File: test.txt\n"
                    "@@\n"
                    "-hello\n"
                    "+hi\n"
                    "*** End Patch"
                )
            },
            auto_approve=True,
            context=context,
        )

        assert "matches multiple locations" in result
        assert "hello" in result
        assert "more unchanged context lines" in result
