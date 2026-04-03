"""Tests for Session: command routing, state management."""

import pytest

from llm_agent.agents import BackgroundSubagentStore
from llm_agent.session import Session
from llm_agent.tools import TOOL_REGISTRY
from llm_agent.tools.base import FileObservationStore


class FakeClient:
    """Minimal fake client for Session init."""
    pass


@pytest.fixture
def session(monkeypatch):
    """Create a Session with minimal mocking."""
    # Prevent refresh_project_context from running real git commands
    monkeypatch.setattr(
        "llm_agent.agent.refresh_project_context",
        lambda: "Test project context",
    )
    return Session(FakeClient(), "claude-sonnet-4-6", auto_approve=False)


class TestHandleCommand:
    def test_clear(self, session):
        session.conversation = [{"role": "user", "content": "hi"}]
        result = session.handle_command("/clear")
        assert result is not None
        messages, transformed = result
        assert transformed is None
        assert "cleared" in messages[0]
        assert session.conversation == []

    def test_version(self, session):
        result = session.handle_command("/version")
        messages, transformed = result
        assert transformed is None
        assert any("llm-agent" in m for m in messages)

    def test_model_show_current(self, session):
        result = session.handle_command("/model")
        messages, _ = result
        assert any("claude-sonnet-4-6" in m for m in messages)

    def test_model_unknown(self, session):
        result = session.handle_command("/model foobar")
        messages, _ = result
        assert any("unknown" in m for m in messages)

    def test_model_switches_to_gemma4_ollama_alias(self, session, monkeypatch):
        fake_client = FakeClient()
        monkeypatch.setattr("llm_agent.cli.make_client", lambda model: fake_client)
        session.conversation = [{"role": "user", "content": "keep?"}]

        result = session.handle_command("/model gemma4-31b")
        messages, _ = result

        assert session.model == "ollama:gemma4:31b"
        assert session.client is fake_client
        assert session.conversation == []
        assert any("ollama:gemma4:31b" in m for m in messages)

    def test_thinking_show(self, session):
        result = session.handle_command("/thinking")
        messages, _ = result
        assert any("thinking" in m.lower() for m in messages)

    def test_thinking_set(self, session):
        session.handle_command("/thinking high")
        assert session.thinking_level == "high"

    def test_thinking_off(self, session):
        session.thinking_level = "high"
        session.handle_command("/thinking off")
        assert session.thinking_level is None

    def test_thinking_invalid(self, session):
        result = session.handle_command("/thinking banana")
        messages, _ = result
        assert any("unknown" in m for m in messages)

    def test_skills_command(self, session):
        result = session.handle_command("/skills")
        assert result is not None

    def test_mcp_command(self, session):
        result = session.handle_command("/mcp")
        messages, _ = result
        assert isinstance(messages, list)

    def test_unknown_slash_command(self, session):
        result = session.handle_command("/nonexistent")
        messages, _ = result
        assert any("unknown" in m for m in messages)

    def test_non_command_returns_none(self, session):
        result = session.handle_command("just a question")
        assert result is None


class TestClear:
    def test_clears_conversation(self, session):
        session.conversation = [{"role": "user", "content": "hi"}]
        session.session_usage = {"input": 100, "output": 50, "cache_read": 10, "cache_create": 5}
        session.clear()
        assert session.conversation == []
        assert session.session_usage["input"] == 0
        assert session.session_usage["output"] == 0


class TestSessionInit:
    def test_defaults(self, session):
        assert session.model == "claude-sonnet-4-6"
        assert session.auto_approve is False
        assert session.conversation == []
        assert session.last_response == ""

    def test_web_search_context_is_configured(self, session):
        context = TOOL_REGISTRY["web_search"].get("context")
        assert context is not None
        assert context["client"] is session.client
        assert context["model"] == session.model
        assert context["provider"] == "anthropic"

    def test_file_tool_contexts_are_configured(self, session):
        for tool_name in ("read_file", "read_many_files", "edit_file", "write_file", "apply_patch"):
            context = TOOL_REGISTRY[tool_name].get("context")
            assert context is not None
            assert isinstance(context["file_observations"], FileObservationStore)
            assert context["file_observations"] is session._file_observations

    def test_delegate_context_accepts_model_override_and_metadata(self, monkeypatch):
        monkeypatch.setattr(
            "llm_agent.agent.refresh_project_context",
            lambda: "Test project context",
        )

        captured = {}

        def fake_run_subagent(agent_name, task, client, model, auto_approve,
                              thinking_level=None, model_override=None, return_metadata=False):
            captured["args"] = {
                "agent_name": agent_name,
                "task": task,
                "client": client,
                "model": model,
                "auto_approve": auto_approve,
                "thinking_level": thinking_level,
                "model_override": model_override,
                "return_metadata": return_metadata,
            }
            return {"status": "completed", "result": "ok"}

        monkeypatch.setattr("llm_agent.agents.run_subagent", fake_run_subagent)

        local_session = Session(FakeClient(), "claude-sonnet-4-6", auto_approve=False)
        run_fn = TOOL_REGISTRY["delegate"]["context"]["run_subagent"]
        run_fn("explore", "check", model_override="haiku", return_metadata=True)

        assert captured["args"]["agent_name"] == "explore"
        assert captured["args"]["task"] == "check"
        assert captured["args"]["model"] == "claude-sonnet-4-6"
        assert captured["args"]["model_override"] == "haiku"
        assert captured["args"]["return_metadata"] is True

    def test_check_task_context_includes_subagent_store(self, session):
        context = TOOL_REGISTRY["check_task"].get("context")
        assert context is not None
        assert isinstance(context["subagent_tasks"], BackgroundSubagentStore)
        assert context["subagent_tasks"] is session._subagent_tasks

    def test_clear_resets_file_observations(self, session):
        session._file_observations._observations["/tmp/example"] = {"st_size": 1}
        session.clear()
        assert session._file_observations._observations == {}
