"""Integration tests for subagent delegation via run_subagent."""

import time

import pytest

from llm_agent.agents import (
    BUILTIN_AGENTS,
    BackgroundSubagentStore,
    DEFAULT_SUBAGENT_MAX_STEPS,
    run_subagent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeClient:
    pass


def _make_turn_fn(responses):
    """Create a fake turn function that returns canned responses.

    responses: list of (content_blocks, done) tuples.
    Each call pops the first response off the list.
    """
    remaining = list(responses)

    def turn_fn(client, model, messages, auto_approve,
                usage_totals=None, tools=None, tool_registry=None,
                system_prompt=None, **kwargs):
        content, done = remaining.pop(0)
        messages.append({"role": "assistant", "content": content})
        if usage_totals is not None:
            usage_totals["input"] += 100
            usage_totals["output"] += 50
        return messages, done

    return turn_fn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRunSubagent:
    def test_unknown_agent(self):
        result = run_subagent("nonexistent", "task", FakeClient(), "claude-sonnet-4-6", False)
        assert "unknown agent" in result
        assert "explore" in result  # should list available agents

    def test_simple_text_response(self, monkeypatch):
        """Subagent returns a simple text answer."""
        fake_turn = _make_turn_fn([
            ("The answer is 42", True),
        ])
        monkeypatch.setattr("llm_agent.agent.agent_turn", fake_turn)

        result = run_subagent("explore", "what is 6*7?", FakeClient(), "claude-sonnet-4-6", False)
        assert "42" in result

    def test_list_content_response(self, monkeypatch):
        """Subagent returns content as a list of blocks."""
        content = [{"type": "text", "text": "Found 3 files matching the pattern."}]
        fake_turn = _make_turn_fn([(content, True)])
        monkeypatch.setattr("llm_agent.agent.agent_turn", fake_turn)

        result = run_subagent("explore", "find files", FakeClient(), "claude-sonnet-4-6", False)
        assert "3 files" in result

    def test_multi_step_subagent(self, monkeypatch):
        """Subagent takes multiple turns before producing a final answer."""
        fake_turn = _make_turn_fn([
            ([{"type": "tool_use", "id": "t1", "name": "read_file", "input": {}}], False),
            ("After reading the file, the answer is X.", True),
        ])
        monkeypatch.setattr("llm_agent.agent.agent_turn", fake_turn)

        result = run_subagent("explore", "read and summarize", FakeClient(), "claude-sonnet-4-6", False)
        assert "answer is X" in result

    def test_step_limit(self, monkeypatch):
        """Subagent hitting step limit should stop gracefully."""
        # Return not-done forever
        calls = {"count": 0}

        def infinite_turn(client, model, messages, auto_approve,
                          usage_totals=None, tools=None, tool_registry=None,
                          system_prompt=None, **kwargs):
            calls["count"] += 1
            messages.append({"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "read_file", "input": {}},
            ]})
            return messages, False

        monkeypatch.setattr("llm_agent.agent.agent_turn", infinite_turn)

        result = run_subagent("explore", "infinite loop", FakeClient(), "claude-sonnet-4-6", False)
        # Should return something (the last assistant message or "no text output")
        assert isinstance(result, str)
        assert calls["count"] == DEFAULT_SUBAGENT_MAX_STEPS

    def test_no_text_output(self, monkeypatch):
        """Subagent produces no text blocks — should return a fallback message."""
        content = [{"type": "tool_use", "id": "t1", "name": "read_file", "input": {}}]
        fake_turn = _make_turn_fn([(content, True)])
        monkeypatch.setattr("llm_agent.agent.agent_turn", fake_turn)

        result = run_subagent("explore", "do something", FakeClient(), "claude-sonnet-4-6", False)
        assert "no text output" in result

    def test_tool_filtering(self, monkeypatch):
        """Explore subagent should only get read-only tools."""
        captured = {}

        def capturing_turn(client, model, messages, auto_approve,
                           usage_totals=None, tools=None, tool_registry=None,
                           system_prompt=None, **kwargs):
            captured["tools"] = tools
            captured["registry"] = tool_registry
            captured["system_prompt"] = system_prompt
            messages.append({"role": "assistant", "content": "done"})
            return messages, True

        monkeypatch.setattr("llm_agent.agent.agent_turn", capturing_turn)

        run_subagent("explore", "research", FakeClient(), "claude-sonnet-4-6", False)

        tool_names = {t["name"] for t in captured["tools"]}
        # Explore should have read-only tools
        assert "read_file" in tool_names
        assert "read_many_files" in tool_names
        assert "search_files" in tool_names
        assert "file_outline" in tool_names
        # But not write/mutate tools
        assert "write_file" not in tool_names
        assert "edit_file" not in tool_names
        assert "run_command" not in tool_names
        # Never delegate or ask_user
        assert "delegate" not in tool_names
        assert "ask_user" not in tool_names

    def test_code_agent_gets_write_tools(self, monkeypatch):
        """Code subagent should get full tools (minus delegate/ask_user)."""
        captured = {}

        def capturing_turn(client, model, messages, auto_approve,
                           usage_totals=None, tools=None, tool_registry=None,
                           system_prompt=None, **kwargs):
            captured["tools"] = tools
            messages.append({"role": "assistant", "content": "done"})
            return messages, True

        monkeypatch.setattr("llm_agent.agent.agent_turn", capturing_turn)

        run_subagent("code", "fix bug", FakeClient(), "claude-sonnet-4-6", False)

        tool_names = {t["name"] for t in captured["tools"]}
        assert "read_file" in tool_names
        assert "read_many_files" in tool_names
        assert "file_outline" in tool_names
        assert "write_file" in tool_names
        assert "edit_file" in tool_names
        assert "apply_patch" in tool_names
        assert "run_command" in tool_names
        assert "check_task" in tool_names
        assert "start_session" in tool_names
        assert "write_stdin" in tool_names
        assert "delegate" not in tool_names
        assert "ask_user" not in tool_names

    def test_explore_uses_haiku(self, monkeypatch):
        """Explore agent should use haiku model."""
        captured = {}

        def capturing_turn(client, model, messages, auto_approve,
                           usage_totals=None, tools=None, tool_registry=None,
                           system_prompt=None, **kwargs):
            captured["model"] = model
            messages.append({"role": "assistant", "content": "done"})
            return messages, True

        monkeypatch.setattr("llm_agent.agent.agent_turn", capturing_turn)

        run_subagent("explore", "look around", FakeClient(), "claude-sonnet-4-6", False)
        assert captured["model"] == "claude-haiku-4-5"

    def test_code_inherits_model(self, monkeypatch):
        """Code agent should inherit the parent model."""
        captured = {}

        def capturing_turn(client, model, messages, auto_approve,
                           usage_totals=None, tools=None, tool_registry=None,
                           system_prompt=None, **kwargs):
            captured["model"] = model
            messages.append({"role": "assistant", "content": "done"})
            return messages, True

        monkeypatch.setattr("llm_agent.agent.agent_turn", capturing_turn)

        run_subagent("code", "fix it", FakeClient(), "claude-sonnet-4-6", False)
        assert captured["model"] == "claude-sonnet-4-6"

    def test_model_override_wins(self, monkeypatch):
        """Per-run model override should win over the agent default and parent model."""
        captured = {}

        def capturing_turn(client, model, messages, auto_approve,
                           usage_totals=None, tools=None, tool_registry=None,
                           system_prompt=None, **kwargs):
            captured["model"] = model
            messages.append({"role": "assistant", "content": "done"})
            return messages, True

        monkeypatch.setattr("llm_agent.agent.agent_turn", capturing_turn)

        run_subagent(
            "code",
            "fix it",
            FakeClient(),
            "claude-sonnet-4-6",
            False,
            model_override="haiku",
        )
        assert captured["model"] == "claude-haiku-4-5"

    def test_custom_agent_ollama_alias_uses_ollama_turn(self, monkeypatch):
        """Custom agent aliases that resolve to ollama:* should use ollama_agent_turn."""
        captured = {}
        local_client = FakeClient()

        monkeypatch.setattr(
            "llm_agent.agents.load_all_agents",
            lambda: {
                "local": {
                    "name": "local",
                    "description": "Local ollama agent",
                    "model": "gemma4-31b",
                    "tools": ["read_file"],
                }
            },
        )
        monkeypatch.setattr("llm_agent.cli.make_client", lambda model: local_client)

        def capturing_turn(client, model, messages, auto_approve,
                           usage_totals=None, tools=None, tool_registry=None,
                           system_prompt=None, **kwargs):
            captured["client"] = client
            captured["model"] = model
            messages.append({"role": "assistant", "content": "done"})
            return messages, True

        monkeypatch.setattr("llm_agent.ollama_agent.ollama_agent_turn", capturing_turn)

        run_subagent("local", "run locally", FakeClient(), "claude-sonnet-4-6", False)

        assert captured["client"] is local_client
        assert captured["model"] == "ollama:gemma4:31b"

    def test_streaming_suppressed(self, mock_display, monkeypatch):
        """Subagent should suppress streaming to avoid garbled output."""
        streaming_states = []

        def capturing_turn(client, model, messages, auto_approve,
                           usage_totals=None, tools=None, tool_registry=None,
                           system_prompt=None, **kwargs):
            from llm_agent.display import get_display
            streaming_states.append(get_display()._is_streaming_suppressed())
            messages.append({"role": "assistant", "content": "done"})
            return messages, True

        monkeypatch.setattr("llm_agent.agent.agent_turn", capturing_turn)

        run_subagent("explore", "check", FakeClient(), "claude-sonnet-4-6", False)
        assert streaming_states == [True]

    def test_subagent_counter(self, mock_display, monkeypatch):
        """Subagent should increment/decrement the active counter."""
        def capturing_turn(client, model, messages, auto_approve,
                           usage_totals=None, tools=None, tool_registry=None,
                           system_prompt=None, **kwargs):
            from llm_agent.display import get_display
            assert get_display().active_subagents == 1
            messages.append({"role": "assistant", "content": "done"})
            return messages, True

        monkeypatch.setattr("llm_agent.agent.agent_turn", capturing_turn)

        assert mock_display.active_subagents == 0
        run_subagent("explore", "check", FakeClient(), "claude-sonnet-4-6", False)
        assert mock_display.active_subagents == 0  # restored after

    def test_return_metadata(self, monkeypatch):
        fake_turn = _make_turn_fn([
            ("The answer is 42", True),
        ])
        monkeypatch.setattr("llm_agent.agent.agent_turn", fake_turn)

        result = run_subagent(
            "explore",
            "what is 6*7?",
            FakeClient(),
            "claude-sonnet-4-6",
            False,
            return_metadata=True,
        )

        assert result["agent"] == "explore"
        assert result["model"] == "claude-haiku-4-5"
        assert result["status"] == "completed"
        assert result["steps"] == 1
        assert result["max_steps"] == DEFAULT_SUBAGENT_MAX_STEPS
        assert result["usage"]["input"] == 100
        assert result["result"] == "The answer is 42"

    def test_subagent_status_lines_include_progress_and_done(self, mock_display, monkeypatch):
        fake_turn = _make_turn_fn([
            ([{"type": "tool_use", "id": "t1", "name": "read_file", "input": {}}], False),
            ("final answer", True),
        ])
        monkeypatch.setattr("llm_agent.agent.agent_turn", fake_turn)

        run_subagent("explore", "read and summarize", FakeClient(), "claude-sonnet-4-6", False)

        assert any(
            f"subagent starting: model claude-haiku-4-5, max {DEFAULT_SUBAGENT_MAX_STEPS} steps"
            in status
            for status in mock_display.statuses
        )
        assert any("subagent progress: step 1" in status for status in mock_display.statuses)
        assert any("subagent done: 2 steps" in status for status in mock_display.statuses)

    def test_custom_agent_max_steps_is_respected(self, mock_display, monkeypatch):
        calls = {"count": 0}

        def infinite_turn(client, model, messages, auto_approve,
                          usage_totals=None, tools=None, tool_registry=None,
                          system_prompt=None, **kwargs):
            calls["count"] += 1
            messages.append({"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "read_file", "input": {}},
            ]})
            return messages, False

        monkeypatch.setattr(
            "llm_agent.agents.load_all_agents",
            lambda: {
                "limited": {
                    "name": "limited",
                    "description": "Limited agent",
                    "model": "haiku",
                    "max_steps": 3,
                    "tools": ["read_file"],
                }
            },
        )
        monkeypatch.setattr("llm_agent.agent.agent_turn", infinite_turn)

        result = run_subagent("limited", "loop", FakeClient(), "claude-sonnet-4-6", False)

        assert isinstance(result, str)
        assert calls["count"] == 3
        assert any("step limit of 3" in error for error in mock_display.errors)

    def test_background_subagent_store_runs_task(self, monkeypatch):
        fake_turn = _make_turn_fn([
            ("background result", True),
        ])
        monkeypatch.setattr("llm_agent.agent.agent_turn", fake_turn)

        store = BackgroundSubagentStore()
        info = store.start(
            "explore",
            "research this",
            FakeClient(),
            "claude-sonnet-4-6",
            False,
        )

        assert info["task_id"] == "sub-1"
        assert info["status"] in {"running", "completed"}

        deadline = time.time() + 2
        latest = info
        while time.time() < deadline:
            latest = store.get_task("sub-1")
            if latest["status"] != "running":
                break
            time.sleep(0.02)

        assert latest["status"] == "completed"
        assert latest["model"] == "claude-haiku-4-5"
        assert latest["max_steps"] == DEFAULT_SUBAGENT_MAX_STEPS
        assert latest["result"] == "background result"
