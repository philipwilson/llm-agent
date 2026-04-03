"""Tests for check_task tool."""

import time

import pytest

from llm_agent.tools.base import shell
from llm_agent.tools.check_task import handle


class FakeSubagentStore:
    def __init__(self, tasks=None):
        self._tasks = tasks or {}

    def get_task(self, task_id):
        return self._tasks.get(task_id)

    def list_tasks(self):
        return list(self._tasks.values())


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

    def test_check_delegated_background_task(self):
        store = FakeSubagentStore(
            {
                "sub-1": {
                    "task_id": "sub-1",
                    "type": "delegate",
                    "agent": "explore",
                    "task": "research the parser",
                    "model": "claude-haiku-4-5",
                    "status": "completed",
                    "started_at": time.time() - 1,
                    "finished_at": time.time(),
                    "duration_seconds": 1.0,
                    "steps": 2,
                    "usage": {"input": 100, "output": 50, "cache_read": 0, "cache_create": 0},
                    "result": "Found the relevant files.",
                }
            }
        )

        result = handle({"task_id": "sub-1"}, context={"subagent_tasks": store})

        assert "delegated subagent" in result
        assert "Agent: explore" in result
        assert "Steps: 2" in result
        assert "Found the relevant files." in result

    def test_list_all_tasks_includes_delegated_tasks(self):
        store = FakeSubagentStore(
            {
                "sub-1": {
                    "task_id": "sub-1",
                    "type": "delegate",
                    "agent": "explore",
                    "task": "research the parser",
                    "model": "claude-haiku-4-5",
                    "status": "running",
                    "started_at": time.time(),
                    "finished_at": None,
                    "duration_seconds": 0.1,
                    "steps": 0,
                    "usage": {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0},
                    "result": "",
                }
            }
        )

        result = handle({}, context={"subagent_tasks": store})

        assert "Background tasks:" in result
        assert "sub-1" in result
        assert "delegate" in result
