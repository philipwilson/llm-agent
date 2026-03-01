"""Shared fixtures for all tests."""

import os
import threading
from contextlib import contextmanager

import pytest

from llm_agent.display import Display, set_display


class MockDisplay(Display):
    """Captures all Display method calls for test assertions.

    Inherits from Display so it's a valid drop-in.  Overrides every method
    to record calls instead of touching stdout/stdin.
    """

    def __init__(self, confirm_result=True, ask_result="y"):
        super().__init__()
        self._confirm_result = confirm_result
        self._ask_result = ask_result
        self.logs = []
        self.confirms = []
        self.asks = []
        self.errors = []
        self.statuses = []
        self.infos = []
        self.auto_approvals = []
        self.stream_tokens = []
        self.tool_results = []

    def stream_start(self):
        pass

    def stream_token(self, text):
        self.stream_tokens.append(text)

    def stream_end(self):
        pass

    def tool_log(self, message):
        self.logs.append(message)

    def tool_result(self, line_count):
        self.tool_results.append(line_count)

    def confirm(self, preview_lines, prompt_text):
        self.confirms.append((preview_lines, prompt_text))
        return self._confirm_result

    def ask_user(self, question, choices=None):
        self.asks.append((question, choices))
        return self._ask_result

    def auto_approved(self, preview_lines):
        self.auto_approvals.append(preview_lines)

    def status(self, message):
        self.statuses.append(message)

    def error(self, message):
        self.errors.append(message)

    def info(self, message):
        self.infos.append(message)

    def info_stderr(self, message):
        self.infos.append(message)


@pytest.fixture(autouse=True)
def mock_display():
    """Inject a MockDisplay for every test so nothing touches stdout."""
    display = MockDisplay()
    set_display(display)
    yield display
    set_display(Display())


@pytest.fixture
def declining_display():
    """A MockDisplay that declines all confirmations."""
    display = MockDisplay(confirm_result=False)
    set_display(display)
    yield display
    set_display(Display())
