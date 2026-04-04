"""Tests for llm_agent.context — project type detection and config parsing."""

import json
import os

import pytest

from llm_agent.context import (
    _detect_project,
    _parse_pyproject,
    _parse_package_json,
    _parse_cargo,
    _parse_go_mod,
    _load_convention_file,
    detect_project_context,
)


class TestParsePyproject:
    def test_extracts_name(self, tmp_path):
        f = tmp_path / "pyproject.toml"
        f.write_text('[project]\nname = "my-package"\n')
        result = _parse_pyproject(str(f))
        assert "my-package" in result
        assert "pyproject.toml" in result

    def test_no_name_field(self, tmp_path):
        f = tmp_path / "pyproject.toml"
        f.write_text("[build-system]\nrequires = []\n")
        result = _parse_pyproject(str(f))
        assert "pyproject.toml" in result

    def test_single_quoted_name(self, tmp_path):
        f = tmp_path / "pyproject.toml"
        f.write_text("[project]\nname = 'single-quoted'\n")
        result = _parse_pyproject(str(f))
        assert "single-quoted" in result

    def test_name_under_wrong_table(self, tmp_path):
        """Only [project].name should match, not a random top-level name key."""
        f = tmp_path / "pyproject.toml"
        f.write_text('[tool.poetry]\nname = "poetry-name"\n')
        result = _parse_pyproject(str(f))
        # Should fall back — no [project] table
        assert result == "Python project (pyproject.toml)"

    def test_multiline_value(self, tmp_path):
        """tomllib handles complex TOML that old line-split couldn't."""
        f = tmp_path / "pyproject.toml"
        f.write_text(
            '[project]\nname = "my-pkg"\n'
            'description = """\nA long\nmultiline description.\n"""\n'
        )
        result = _parse_pyproject(str(f))
        assert "my-pkg" in result


class TestParsePackageJson:
    def test_with_scripts(self, tmp_path):
        f = tmp_path / "package.json"
        f.write_text(json.dumps({"name": "my-app", "scripts": {"test": "jest"}}))
        result = _parse_package_json(str(f))
        assert "my-app" in result
        assert "package.json" in result
        assert "test" in result

    def test_name_only(self, tmp_path):
        f = tmp_path / "package.json"
        f.write_text(json.dumps({"name": "my-app"}))
        result = _parse_package_json(str(f))
        assert "my-app" in result

    def test_no_name(self, tmp_path):
        f = tmp_path / "package.json"
        f.write_text(json.dumps({"version": "1.0"}))
        result = _parse_package_json(str(f))
        assert "Node.js" in result

    def test_scripts_listed(self, tmp_path):
        f = tmp_path / "package.json"
        f.write_text(json.dumps({"name": "app", "scripts": {"test": "jest", "build": "tsc"}}))
        result = _parse_package_json(str(f))
        assert "test" in result


class TestParseCargo:
    def test_extracts_name(self, tmp_path):
        f = tmp_path / "Cargo.toml"
        f.write_text('[package]\nname = "my-crate"\nversion = "0.1.0"\n')
        result = _parse_cargo(str(f))
        assert "my-crate" in result
        assert "Rust" in result

    def test_no_name(self, tmp_path):
        f = tmp_path / "Cargo.toml"
        f.write_text("[workspace]\nmembers = []\n")
        result = _parse_cargo(str(f))
        assert "Rust" in result

    def test_name_with_inline_comment(self, tmp_path):
        """tomllib handles inline comments correctly (old regex didn't)."""
        f = tmp_path / "Cargo.toml"
        f.write_text('[package]\nname = "my-crate" # the main crate\n')
        result = _parse_cargo(str(f))
        assert "my-crate" in result
        assert "#" not in result


class TestParseGoMod:
    def test_extracts_module(self, tmp_path):
        f = tmp_path / "go.mod"
        f.write_text("module github.com/user/repo\n\ngo 1.21\n")
        result = _parse_go_mod(str(f))
        assert "github.com/user/repo" in result
        assert "Go" in result

    def test_no_module_line(self, tmp_path):
        f = tmp_path / "go.mod"
        f.write_text("go 1.21\n")
        result = _parse_go_mod(str(f))
        assert "Go" in result


class TestDetectProject:
    def test_pyproject(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('name = "proj"\n')
        result = _detect_project(str(tmp_path))
        assert result is not None
        assert "Python" in result

    def test_package_json(self, tmp_path):
        (tmp_path / "package.json").write_text('{"name": "app"}')
        result = _detect_project(str(tmp_path))
        assert "Node.js" in result

    def test_makefile(self, tmp_path):
        (tmp_path / "Makefile").write_text("all:\n\techo hi\n")
        result = _detect_project(str(tmp_path))
        assert "Makefile" in result

    def test_no_config_files(self, tmp_path):
        result = _detect_project(str(tmp_path))
        assert result is None

    def test_priority_order(self, tmp_path):
        """pyproject.toml takes priority over package.json."""
        (tmp_path / "pyproject.toml").write_text('name = "py-proj"\n')
        (tmp_path / "package.json").write_text('{"name": "node-proj"}')
        result = _detect_project(str(tmp_path))
        assert "Python" in result


class TestConventionFile:
    def test_loads_agents_md(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("# Project Rules\nBe nice.")
        result = _load_convention_file(str(tmp_path))
        assert "Project Rules" in result

    def test_missing_agents_md(self, tmp_path):
        result = _load_convention_file(str(tmp_path))
        assert result is None


class TestDetectProjectContext:
    def test_returns_empty_for_bare_dir(self, tmp_path):
        result = detect_project_context(str(tmp_path))
        # Might still detect git if tmp_path is inside a repo
        # but should not error
        assert isinstance(result, str)

    def test_includes_project_type(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('name = "test"\n')
        result = detect_project_context(str(tmp_path))
        assert "Python" in result
