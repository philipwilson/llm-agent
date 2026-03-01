"""Tests for delegate tool."""

import pytest

from llm_agent.tools.delegate import handle, log


class TestDelegate:
    def test_missing_agent(self):
        result = handle({"task": "do something"})
        assert "error" in result
        assert "required" in result

    def test_missing_task(self):
        result = handle({"agent": "explore"})
        assert "error" in result
        assert "required" in result

    def test_empty_agent(self):
        result = handle({"agent": "", "task": "do something"})
        assert "error" in result

    def test_empty_task(self):
        result = handle({"agent": "explore", "task": ""})
        assert "error" in result

    def test_no_context(self):
        result = handle({"agent": "explore", "task": "research something"})
        assert "not configured" in result

    def test_no_run_subagent_in_context(self):
        result = handle({"agent": "explore", "task": "research"}, context={})
        assert "not configured" in result

    def test_calls_run_subagent(self):
        calls = []
        def fake_run(agent, task):
            calls.append((agent, task))
            return "subagent result"

        result = handle(
            {"agent": "explore", "task": "find files"},
            context={"run_subagent": fake_run},
        )
        assert result == "subagent result"
        assert calls == [("explore", "find files")]

    def test_log(self, mock_display):
        log({"agent": "code", "task": "implement feature X in the codebase"})
        assert len(mock_display.logs) == 1
        assert "code" in mock_display.logs[0]

    def test_log_truncates_long_task(self, mock_display):
        long_task = "x" * 200
        log({"agent": "explore", "task": long_task})
        assert len(mock_display.logs) == 1
        assert "..." in mock_display.logs[0]
