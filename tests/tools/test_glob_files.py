"""Tests for glob_files tool."""

import os

import pytest

from llm_agent.tools.glob_files import handle
from llm_agent.tools.base import shell


class TestGlobFiles:
    def test_basic_glob(self, tmp_path):
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")
        (tmp_path / "c.txt").write_text("")
        shell.cwd = str(tmp_path)
        result = handle({"pattern": "*.py"})
        assert "a.py" in result
        assert "b.py" in result
        assert "c.txt" not in result
        assert "2 files" in result

    def test_recursive_glob(self, tmp_path):
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "main.py").write_text("")
        (tmp_path / "setup.py").write_text("")
        shell.cwd = str(tmp_path)
        result = handle({"pattern": "**/*.py"})
        assert "setup.py" in result
        assert os.path.join("src", "main.py") in result

    def test_no_matches(self, tmp_path):
        shell.cwd = str(tmp_path)
        result = handle({"pattern": "*.xyz"})
        assert "0 files" in result
        assert "no matches" in result

    def test_max_results(self, tmp_path):
        for i in range(10):
            (tmp_path / f"file{i}.txt").write_text("")
        shell.cwd = str(tmp_path)
        result = handle({"pattern": "*.txt", "max_results": 3})
        assert "not shown" in result

    def test_excludes_directories(self, tmp_path):
        (tmp_path / "mydir").mkdir()
        (tmp_path / "file.txt").write_text("")
        shell.cwd = str(tmp_path)
        result = handle({"pattern": "*"})
        assert "file.txt" in result
        assert "mydir" not in result.split("\n", 1)[1]  # not in results, only in header

    def test_nonexistent_directory(self, tmp_path):
        shell.cwd = str(tmp_path)
        result = handle({"pattern": "*.py", "path": "nonexistent"})
        assert "error" in result

    def test_custom_path(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "found.py").write_text("")
        shell.cwd = str(tmp_path)
        result = handle({"pattern": "*.py", "path": "sub"})
        assert "found.py" in result

    def test_results_sorted(self, tmp_path):
        (tmp_path / "c.py").write_text("")
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")
        shell.cwd = str(tmp_path)
        result = handle({"pattern": "*.py"})
        lines = result.strip().splitlines()[1:]  # skip header
        assert lines == sorted(lines)

    def test_exclude_filter(self, tmp_path):
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b_test.py").write_text("")
        shell.cwd = str(tmp_path)
        result = handle({"pattern": "*.py", "exclude": ["*_test.py"]})
        assert "a.py" in result
        assert "b_test.py" not in result

    def test_hidden_files_excluded_by_default(self, tmp_path):
        hidden = tmp_path / ".hidden.py"
        hidden.write_text("")
        shell.cwd = str(tmp_path)
        result = handle({"pattern": "*.py"})
        assert ".hidden.py" not in result

    def test_hidden_files_can_be_included(self, tmp_path):
        hidden = tmp_path / ".hidden.py"
        hidden.write_text("")
        shell.cwd = str(tmp_path)
        result = handle({"pattern": "*.py", "hidden": True})
        assert ".hidden.py" in result

    def test_invalid_max_results(self, tmp_path):
        shell.cwd = str(tmp_path)
        result = handle({"pattern": "*.py", "max_results": 0})
        assert "max_results must be >= 1" in result
