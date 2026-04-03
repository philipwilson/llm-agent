"""Tests for read_many_files tool."""

from llm_agent.tools.base import FileObservationStore, shell
from llm_agent.tools.read_many_files import handle


class TestReadManyFiles:
    def test_reads_explicit_paths(self, tmp_path):
        (tmp_path / "a.txt").write_text("alpha\n")
        (tmp_path / "b.txt").write_text("beta\n")
        shell.cwd = str(tmp_path)

        result = handle({"paths": ["a.txt", "b.txt"]})

        assert "read_many_files: 2 file(s)" in result
        assert "alpha" in result
        assert "beta" in result

    def test_reads_include_patterns_with_exclude(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "keep.py").write_text("print('keep')\n")
        (src / "skip.py").write_text("print('skip')\n")
        shell.cwd = str(tmp_path)

        result = handle(
            {
                "path": "src",
                "include": ["*.py"],
                "exclude": ["skip.py"],
            }
        )

        assert "keep.py" in result
        assert "skip.py" not in result

    def test_limit_and_offset_apply_per_file(self, tmp_path):
        (tmp_path / "a.txt").write_text("a1\na2\na3\n")
        (tmp_path / "b.txt").write_text("b1\nb2\nb3\n")
        shell.cwd = str(tmp_path)

        result = handle({"paths": ["a.txt", "b.txt"], "offset": 2, "limit": 1})

        assert "a2" in result
        assert "a1" not in result
        assert "b2" in result
        assert "use offset=3 to continue" in result

    def test_records_file_observations(self, tmp_path):
        tracked = tmp_path / "tracked.txt"
        tracked.write_text("tracked\n")
        shell.cwd = str(tmp_path)
        store = FileObservationStore()

        handle({"paths": ["tracked.txt"]}, context={"file_observations": store})

        assert store.validate_fresh(str(tracked), tracked.stat(), "edit") is None

    def test_max_files_caps_output(self, tmp_path):
        for name in ("a.txt", "b.txt", "c.txt"):
            (tmp_path / name).write_text(f"{name}\n")
        shell.cwd = str(tmp_path)

        result = handle({"include": ["*.txt"], "max_files": 2})

        assert "2 file(s) shown of 3" in result
        assert "increase max_files from 2" in result

    def test_requires_paths_or_include(self, tmp_path):
        shell.cwd = str(tmp_path)
        result = handle({})
        assert "provide at least one explicit path or include glob pattern" in result
