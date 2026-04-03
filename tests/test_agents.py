"""Tests for agents.py: agent definitions, custom loading, tool filtering."""

import json
import os

import pytest

from llm_agent.agents import BUILTIN_AGENTS, load_all_agents, _load_custom_agents


class TestBuiltinAgents:
    def test_explore_agent(self):
        explore = BUILTIN_AGENTS["explore"]
        assert explore["name"] == "explore"
        assert explore["model"] == "haiku"
        # Should be read-only tools
        assert "write_file" not in explore["tools"]
        assert "run_command" not in explore["tools"]
        assert "read_file" in explore["tools"]

    def test_code_agent(self):
        code = BUILTIN_AGENTS["code"]
        assert code["name"] == "code"
        assert code["model"] is None  # inherits parent
        assert "read_file" in code["tools"]
        assert "write_file" in code["tools"]
        assert "apply_patch" in code["tools"]
        assert "run_command" in code["tools"]
        assert "check_task" in code["tools"]
        assert "start_session" in code["tools"]
        assert "write_stdin" in code["tools"]

    def test_no_delegate_in_builtins(self):
        for agent in BUILTIN_AGENTS.values():
            assert "delegate" not in agent["tools"]
            assert "ask_user" not in agent["tools"]


class TestLoadCustomAgents:
    def test_loads_from_project_dir(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / ".agents"
        agents_dir.mkdir()
        (agents_dir / "helper.json").write_text(json.dumps({
            "name": "helper",
            "description": "A helper agent",
            "tools": ["read_file", "search_files"],
        }))
        monkeypatch.setattr(os, "getcwd", lambda: str(tmp_path))
        # Patch expanduser to avoid loading real ~/.agents
        monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "nohome"))

        agents = _load_custom_agents()
        assert "helper" in agents
        assert agents["helper"]["description"] == "A helper agent"

    def test_filters_delegate_and_ask_user(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / ".agents"
        agents_dir.mkdir()
        (agents_dir / "sneaky.json").write_text(json.dumps({
            "name": "sneaky",
            "tools": ["read_file", "delegate", "ask_user", "run_command"],
        }))
        monkeypatch.setattr(os, "getcwd", lambda: str(tmp_path))
        monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "nohome"))

        agents = _load_custom_agents()
        tools = agents["sneaky"]["tools"]
        assert "delegate" not in tools
        assert "ask_user" not in tools
        assert "read_file" in tools
        assert "run_command" in tools

    def test_skips_invalid_json(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / ".agents"
        agents_dir.mkdir()
        (agents_dir / "bad.json").write_text("not valid json{{{")
        monkeypatch.setattr(os, "getcwd", lambda: str(tmp_path))
        monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "nohome"))

        agents = _load_custom_agents()
        assert "bad" not in agents

    def test_project_overrides_user(self, tmp_path, monkeypatch):
        user_dir = tmp_path / "home" / ".agents"
        user_dir.mkdir(parents=True)
        (user_dir / "agent.json").write_text(json.dumps({
            "name": "agent",
            "description": "user-level",
        }))

        proj_dir = tmp_path / "project" / ".agents"
        proj_dir.mkdir(parents=True)
        (proj_dir / "agent.json").write_text(json.dumps({
            "name": "agent",
            "description": "project-level",
        }))

        monkeypatch.setattr(os, "getcwd", lambda: str(tmp_path / "project"))
        monkeypatch.setattr(os.path, "expanduser", lambda p: str(user_dir.parent))

        agents = _load_custom_agents()
        assert agents["agent"]["description"] == "project-level"


class TestLoadAllAgents:
    def test_includes_builtins(self):
        agents = load_all_agents()
        assert "explore" in agents
        assert "code" in agents
