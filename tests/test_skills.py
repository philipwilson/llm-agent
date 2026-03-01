"""Tests for llm_agent.skills — skill parsing, rendering, and discovery."""

import os

import pytest

from llm_agent.skills import parse_skill, render_skill, format_skill_list, load_all_skills


class TestParseSkill:
    def test_valid_skill(self, tmp_path):
        f = tmp_path / "SKILL.md"
        f.write_text(
            "---\n"
            "name: review\n"
            "description: Code review\n"
            "argument-hint: <file>\n"
            "---\n"
            "Review this file: $0\n"
        )
        skill = parse_skill(str(f))
        assert skill is not None
        assert skill["name"] == "review"
        assert skill["description"] == "Code review"
        assert skill["argument-hint"] == "<file>"
        assert "$0" in skill["body"]

    def test_missing_name(self, tmp_path):
        f = tmp_path / "SKILL.md"
        f.write_text("---\ndescription: no name\n---\nbody\n")
        assert parse_skill(str(f)) is None

    def test_no_frontmatter(self, tmp_path):
        f = tmp_path / "SKILL.md"
        f.write_text("Just plain text, no frontmatter\n")
        assert parse_skill(str(f)) is None

    def test_missing_closing_delimiter(self, tmp_path):
        f = tmp_path / "SKILL.md"
        f.write_text("---\nname: broken\nno closing delimiter\n")
        assert parse_skill(str(f)) is None

    def test_invalid_yaml(self, tmp_path):
        f = tmp_path / "SKILL.md"
        f.write_text("---\n: invalid: yaml: [[\n---\nbody\n")
        assert parse_skill(str(f)) is None

    def test_nonexistent_file(self):
        assert parse_skill("/nonexistent/SKILL.md") is None


class TestRenderSkill:
    def _make_skill(self, body):
        return {"name": "test", "body": body}

    def test_arguments_substitution(self):
        skill = self._make_skill("Args: $ARGUMENTS")
        result = render_skill(skill, "foo bar")
        assert result == "Args: foo bar"

    def test_positional_args(self):
        skill = self._make_skill("First: $0\nSecond: $1")
        result = render_skill(skill, "alpha beta")
        assert "First: alpha" in result
        assert "Second: beta" in result

    def test_no_args(self):
        skill = self._make_skill("No args here.")
        result = render_skill(skill, "")
        assert result == "No args here."

    def test_empty_arguments_replaced(self):
        skill = self._make_skill("Args: [$ARGUMENTS]")
        result = render_skill(skill, "")
        assert result == "Args: []"

    def test_dynamic_injection(self):
        skill = self._make_skill('!`echo injected-output`')
        result = render_skill(skill, "")
        assert "injected-output" in result

    def test_dynamic_injection_failure(self):
        skill = self._make_skill('!`false`')
        result = render_skill(skill, "")
        # Should not crash, returns empty or stderr
        assert isinstance(result, str)

    def test_mixed_static_and_dynamic(self):
        skill = self._make_skill("Static line\n!`echo dynamic`\nAnother static")
        result = render_skill(skill, "")
        lines = result.splitlines()
        assert lines[0] == "Static line"
        assert lines[1] == "dynamic"
        assert lines[2] == "Another static"


class TestFormatSkillList:
    def test_formats_skills(self):
        skills = {
            "review": {"name": "review", "description": "Review code", "argument-hint": "<file>"},
            "deploy": {"name": "deploy", "description": "Deploy app"},
        }
        result = format_skill_list(skills)
        assert "review" in result
        assert "deploy" in result
        assert "Review code" in result

    def test_empty_skills(self):
        assert format_skill_list({}) == ""


class TestLoadAllSkills:
    def test_loads_from_directory(self, tmp_path, monkeypatch):
        # Create a skill directory structure
        skill_dir = tmp_path / ".skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: test-skill\ndescription: A test\n---\nBody\n")

        monkeypatch.setattr(os, "getcwd", lambda: str(tmp_path))
        skills = load_all_skills()
        assert "test-skill" in skills

    def test_no_skills_directory(self, tmp_path, monkeypatch):
        monkeypatch.setattr(os, "getcwd", lambda: str(tmp_path))
        # Patch expanduser so it doesn't pick up real ~/.skills
        monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "nope"))
        skills = load_all_skills()
        assert isinstance(skills, dict)
