"""Fixtures for tool tests."""

import os

import pytest

from llm_agent.tools.base import shell, ShellState


@pytest.fixture(autouse=True)
def reset_shell_cwd(tmp_path):
    """Reset the global shell's cwd to a temp dir before each test."""
    original_cwd = shell.cwd
    original_tasks = shell._tasks
    original_counter = shell._task_counter
    shell.cwd = str(tmp_path)
    shell._tasks = {}
    shell._task_counter = 0
    yield tmp_path
    shell.cwd = original_cwd
    shell._tasks = original_tasks
    shell._task_counter = original_counter


@pytest.fixture
def sample_file(tmp_path):
    """Create a sample file for read/edit tests."""
    p = tmp_path / "sample.txt"
    p.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n")
    return p
