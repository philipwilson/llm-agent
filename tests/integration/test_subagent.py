"""Integration tests for subagent delegation via run_subagent."""

import pytest

from llm_agent.agents import run_subagent, BUILTIN_AGENTS


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
        def infinite_turn(client, model, messages, auto_approve,
                          usage_totals=None, tools=None, tool_registry=None,
                          system_prompt=None, **kwargs):
            messages.append({"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "read_file", "input": {}},
            ]})
            return messages, False

        monkeypatch.setattr("llm_agent.agent.agent_turn", infinite_turn)

        result = run_subagent("explore", "infinite loop", FakeClient(), "claude-sonnet-4-6", False)
        # Should return something (the last assistant message or "no text output")
        assert isinstance(result, str)

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
        assert "search_files" in tool_names
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
        assert "write_file" in tool_names
        assert "edit_file" in tool_names
        assert "run_command" in tool_names
        assert "check_task" in tool_names
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
