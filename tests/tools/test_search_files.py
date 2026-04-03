"""Tests for search_files tool."""

import os

import pytest

from llm_agent.tools.search_files import handle
from llm_agent.tools.base import shell


class TestSearchFiles:
    def test_basic_search(self, tmp_path):
        (tmp_path / "file.txt").write_text("hello world\ngoodbye world\n")
        shell.cwd = str(tmp_path)
        result = handle({"pattern": "hello", "path": "."})
        assert "hello" in result
        assert "matches" in result

    def test_no_matches(self, tmp_path):
        (tmp_path / "file.txt").write_text("hello world\n")
        shell.cwd = str(tmp_path)
        result = handle({"pattern": "nonexistent", "path": "."})
        assert "no matches" in result

    def test_regex_pattern(self, tmp_path):
        (tmp_path / "code.py").write_text("def foo():\ndef bar():\n")
        shell.cwd = str(tmp_path)
        result = handle({"pattern": "def \\w+\\(", "path": "."})
        assert "foo" in result
        assert "bar" in result

    def test_glob_filter(self, tmp_path):
        (tmp_path / "code.py").write_text("target\n")
        (tmp_path / "readme.md").write_text("target\n")
        shell.cwd = str(tmp_path)
        result = handle({"pattern": "target", "path": ".", "glob": "*.py"})
        assert "code.py" in result
        # readme.md should be filtered out
        assert "readme.md" not in result

    def test_max_results(self, tmp_path):
        lines = "\n".join(f"match line {i}" for i in range(20))
        (tmp_path / "many.txt").write_text(lines + "\n")
        shell.cwd = str(tmp_path)
        result = handle({"pattern": "match", "path": ".", "max_results": 5})
        assert "capped at 5" in result

    def test_mode_files_lists_matching_files_only(self, tmp_path):
        (tmp_path / "a.txt").write_text("pattern here\n")
        (tmp_path / "b.txt").write_text("no match\n")
        shell.cwd = str(tmp_path)
        result = handle({"pattern": "pattern", "path": ".", "mode": "files"})
        assert "a.txt" in result
        assert "pattern here" not in result

    def test_context_lines_include_surrounding_text(self, tmp_path):
        (tmp_path / "file.txt").write_text("one\ntwo match\nthree\n")
        shell.cwd = str(tmp_path)
        result = handle({"pattern": "match", "path": ".", "context_lines": 1})
        assert "one" in result
        assert "three" in result

    def test_max_matches_per_file_limits_each_file(self, tmp_path):
        (tmp_path / "many.txt").write_text("match 1\nmatch 2\nmatch 3\n")
        shell.cwd = str(tmp_path)
        result = handle({"pattern": "match", "path": ".", "max_matches_per_file": 1})
        assert "match 1" in result
        assert "match 2" not in result

    def test_invalid_mode(self, tmp_path):
        (tmp_path / "file.txt").write_text("hello\n")
        shell.cwd = str(tmp_path)
        result = handle({"pattern": "hello", "mode": "weird"})
        assert "unsupported mode" in result

    def test_default_path(self, tmp_path):
        (tmp_path / "file.txt").write_text("findme\n")
        shell.cwd = str(tmp_path)
        result = handle({"pattern": "findme"})
        assert "findme" in result

    def test_subdirectory_search(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.txt").write_text("deep content\n")
        shell.cwd = str(tmp_path)
        result = handle({"pattern": "deep", "path": "."})
        assert "deep" in result

    def test_multiple_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("pattern here\n")
        (tmp_path / "b.txt").write_text("pattern there\n")
        shell.cwd = str(tmp_path)
        result = handle({"pattern": "pattern", "path": "."})
        assert "a.txt" in result
        assert "b.txt" in result
