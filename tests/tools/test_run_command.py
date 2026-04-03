"""Tests for run_command tool: shell state, background tasks, cwd tracking."""

import shlex
import sys
import time

import pytest

from llm_agent.tools.base import shell
from llm_agent.tools.run_command import handle, confirm


class TestShellRun:
    def test_basic_command(self):
        result = shell.run("echo hello")
        assert "hello" in result

    def test_cwd_tracking(self, tmp_path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        shell.cwd = str(tmp_path)
        shell.run(f"cd {sub}")
        assert shell.cwd == str(sub)

    def test_stderr_included(self):
        result = shell.run("echo err >&2")
        assert "[stderr]" in result
        assert "err" in result

    def test_no_output(self):
        result = shell.run("true")
        assert result == "(no output)"

    def test_command_error(self):
        result = shell.run("exit 1")
        # Should still return without crashing
        assert isinstance(result, str)

    def test_timeout(self, monkeypatch):
        import llm_agent.tools.base as base_mod
        original = base_mod.COMMAND_TIMEOUT
        base_mod.COMMAND_TIMEOUT = 1
        try:
            result = shell.run("sleep 10")
            assert "timed out" in result
        finally:
            base_mod.COMMAND_TIMEOUT = original


class TestBackgroundTasks:
    def test_start_background(self):
        task_id = shell.start_background("echo background-output")
        assert task_id == "bg-1"
        time.sleep(0.5)
        info = shell.get_task(task_id)
        assert info["status"] == "completed"
        assert info["exit_code"] == 0
        assert info["pid"] > 0
        assert info["cwd"] == str(shell.cwd)
        assert info["duration_seconds"] >= 0
        assert info["started_at"] is not None
        assert "background-output" in info["output"]

    def test_sequential_ids(self):
        t1 = shell.start_background("echo a")
        t2 = shell.start_background("echo b")
        assert t1 == "bg-1"
        assert t2 == "bg-2"

    def test_list_tasks(self):
        shell.start_background("echo task1")
        shell.start_background("echo task2")
        time.sleep(0.5)
        tasks = shell.list_tasks()
        assert len(tasks) == 2
        assert tasks[0]["task_id"] == "bg-1"
        assert tasks[1]["task_id"] == "bg-2"

    def test_unknown_task(self):
        assert shell.get_task("bg-999") is None

    def test_failed_task(self):
        task_id = shell.start_background("exit 1")
        time.sleep(0.5)
        info = shell.get_task(task_id)
        assert info["status"] == "failed"
        assert info["exit_code"] == 1

    def test_background_reads_stdout_and_stderr_concurrently(self):
        command = (
            f"{shlex.quote(sys.executable)} -c "
            "\"import sys; sys.stderr.write('x' * 200000); sys.stderr.flush(); print('done')\""
        )
        task_id = shell.start_background(command)
        time.sleep(0.5)
        info = shell.get_task(task_id)
        assert info["status"] == "completed"
        assert info["exit_code"] == 0
        assert "done" in info["output"]
        assert "x" in info["output"]

    def test_stop_all(self):
        task_id = shell.start_background("sleep 30")
        time.sleep(0.2)
        info = shell.get_task(task_id)
        assert info["status"] == "running"
        shell.stop_all()
        time.sleep(0.2)
        info = shell.get_task(task_id)
        assert info["status"] in ("failed", "completed")

    def test_stop_all_idempotent(self):
        """stop_all on already-finished tasks should not error."""
        shell.start_background("echo done")
        time.sleep(0.5)
        shell.stop_all()  # should not raise


class TestHandle:
    def test_handle_auto_approve(self):
        result = handle({"command": "echo hi"}, auto_approve=True)
        assert "hi" in result

    def test_handle_background(self):
        result = handle(
            {"command": "echo bg", "run_in_background": True},
            auto_approve=True,
        )
        assert "bg-" in result
        assert "check_task" in result
        assert "pid" in result

    def test_handle_declined(self, declining_display):
        result = handle({"command": "echo hi"})
        assert "declined" in result

    def test_handle_with_description(self):
        result = handle(
            {"command": "echo hi", "description": "testing"},
            auto_approve=True,
        )
        assert "hi" in result
