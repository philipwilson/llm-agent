"""Tests for PTY session startup."""

import sys
import time

import pytest

from llm_agent.tools.base import shell
from llm_agent.tools.start_session import handle


pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="PTY sessions are unsupported on Windows",
)


class TestPtySessions:
    def test_start_session_direct_api(self):
        session_id = shell.start_session("printf ready; stty -echo; cat")
        assert session_id == "pty-1"

        time.sleep(0.2)
        info = shell.write_session(session_id, wait_ms=0)
        assert info["status"] == "running"
        assert info["pid"] > 0
        assert info["cwd"] == str(shell.cwd)
        assert "ready" in info["output"]

        sessions = shell.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == session_id

    def test_stop_all_terminates_sessions(self):
        session_id = shell.start_session("sleep 30")
        time.sleep(0.2)
        assert shell.get_session(session_id)["status"] == "running"

        shell.stop_all()
        time.sleep(0.2)

        assert shell.get_session(session_id)["status"] in ("failed", "completed")


class TestHandle:
    def test_handle_starts_session(self):
        result = handle(
            {"command": "printf ready; stty -echo; cat", "wait_ms": 200}
        )
        assert "Interactive session started: pty-1" in result
        assert "Use write_stdin" in result
        assert "ready" in result

    def test_handle_declined(self, declining_display):
        result = handle({"command": "stty -echo; cat"})
        assert "declined" in result

    def test_handle_requires_command(self):
        result = handle({})
        assert "command is required" in result

    def test_handle_validates_wait_ms(self):
        result = handle({"command": "cat", "wait_ms": -1})
        assert "wait_ms must be >= 0" in result

    def test_handle_validates_max_output_chars(self):
        result = handle({"command": "cat", "max_output_chars": 0})
        assert "max_output_chars must be >= 1" in result
