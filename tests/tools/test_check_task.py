"""Tests for check_task tool."""

import time

import pytest

from llm_agent.tools.base import shell
from llm_agent.tools.check_task import handle


class TestCheckTask:
    def test_no_tasks(self):
        result = handle({})
        assert "no background tasks" in result

    def test_unknown_task_id(self):
        result = handle({"task_id": "bg-999"})
        assert "unknown task" in result

    def test_check_specific_task(self):
        task_id = shell.start_background("echo check-output")
        time.sleep(0.5)
        result = handle({"task_id": task_id})
        assert "Working directory:" in result
        assert "PID:" in result
        assert "Runtime:" in result
        assert "check-output" in result
        assert "completed" in result
        assert "Exit code: 0" in result

    def test_list_all_tasks(self):
        shell.start_background("echo first")
        shell.start_background("echo second")
        time.sleep(0.5)
        result = handle({})
        assert "bg-1" in result
        assert "bg-2" in result
        assert "Background tasks:" in result
        assert "pid" in result

    def test_running_task(self):
        task_id = shell.start_background("sleep 30")
        time.sleep(0.2)
        result = handle({"task_id": task_id})
        assert "running" in result
        shell.stop_all()

    def test_failed_task_output(self):
        task_id = shell.start_background("echo fail-msg >&2 && exit 1")
        time.sleep(0.5)
        result = handle({"task_id": task_id})
        assert "failed" in result
        assert "fail-msg" in result

    def test_tail_lines(self):
        task_id = shell.start_background("printf 'one\\ntwo\\nthree\\n'")
        time.sleep(0.5)
        result = handle({"task_id": task_id, "tail_lines": 2})
        assert "Output (last 2 lines):" in result
        assert "\nOutput (last 2 lines):\ntwo\nthree" in result

    def test_invalid_tail_lines(self):
        result = handle({"task_id": "bg-1", "tail_lines": 0})
        assert "tail_lines must be >= 1" in result
