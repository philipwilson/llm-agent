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
        def fake_run(agent, task, model_override=None, return_metadata=False):
            calls.append((agent, task, model_override, return_metadata))
            if return_metadata:
                return {
                    "agent": agent,
                    "model": model_override or "claude-haiku-4-5",
                    "status": "completed",
                    "steps": 2,
                    "max_steps": 100,
                    "duration_seconds": 0.25,
                    "usage": {"input": 120, "output": 45, "cache_read": 0, "cache_create": 0},
                    "result": "subagent result",
                }
            return "subagent result"

        result = handle(
            {"agent": "explore", "task": "find files"},
            context={"run_subagent": fake_run},
        )
        assert "[delegated run]" in result
        assert "agent: explore" in result
        assert "model: claude-haiku-4-5" in result
        assert "max_steps: 100" in result
        assert "subagent result" in result
        assert calls == [("explore", "find files", None, True)]

    def test_calls_run_subagent_with_model_override(self):
        calls = []

        def fake_run(agent, task, model_override=None, return_metadata=False):
            calls.append((agent, task, model_override, return_metadata))
            return {
                "agent": agent,
                "model": model_override,
                "status": "completed",
                "steps": 1,
                "max_steps": 100,
                "duration_seconds": 0.1,
                "usage": {"input": 100, "output": 20, "cache_read": 0, "cache_create": 0},
                "result": "done",
            }

        result = handle(
            {"agent": "code", "task": "fix it", "model": "gpt-4o-mini"},
            context={"run_subagent": fake_run},
        )

        assert "model: gpt-4o-mini" in result
        assert calls == [("code", "fix it", "gpt-4o-mini", True)]

    def test_starts_background_subagent(self):
        calls = []

        def fake_start(agent, task, model_override=None):
            calls.append((agent, task, model_override))
            return {
                "task_id": "sub-1",
                "agent": agent,
                "model": "claude-haiku-4-5",
            }

        result = handle(
            {"agent": "explore", "task": "research", "run_in_background": True},
            context={"run_subagent": lambda *args, **kwargs: None, "start_subagent": fake_start},
        )

        assert "Background delegated task started: sub-1" in result
        assert "claude-haiku-4-5" in result
        assert calls == [("explore", "research", None)]

    def test_log(self, mock_display):
        log({"agent": "code", "task": "implement feature X in the codebase"})
        assert len(mock_display.logs) == 1
        assert "code" in mock_display.logs[0]

    def test_log_truncates_long_task(self, mock_display):
        long_task = "x" * 200
        log({"agent": "explore", "task": long_task})
        assert len(mock_display.logs) == 1
        assert "..." in mock_display.logs[0]
