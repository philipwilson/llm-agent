"""Tests for PTY session stdin writes and polling."""

import sys
import time

import pytest

from llm_agent.tools.base import shell
from llm_agent.tools.write_stdin import handle


pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="PTY sessions are unsupported on Windows",
)


def _start_echo_session():
    session_id = shell.start_session("stty -echo; cat")
    shell.write_session(session_id, wait_ms=100)
    return session_id


class TestWriteStdin:
    def test_write_input(self):
        session_id = _start_echo_session()

        result = handle(
            {"session_id": session_id, "chars": "hello from pty\n", "wait_ms": 200}
        )

        assert f"Session: {session_id}" in result
        assert "Status: running" in result
        assert "New output:" in result
        assert "hello from pty" in result

    def test_poll_only_skips_confirmation(self, mock_display):
        session_id = shell.start_session("printf ready; stty -echo; cat")
        time.sleep(0.2)

        result = handle({"session_id": session_id, "wait_ms": 0})

        assert "ready" in result
        assert mock_display.confirms == []

    def test_close_session(self):
        session_id = _start_echo_session()

        result = handle({"session_id": session_id, "close": True, "wait_ms": 200})

        assert f"Session: {session_id}" in result
        assert "Status:" in result
        assert "Exit code:" in result
        assert shell.get_session(session_id)["status"] in ("failed", "completed")

    def test_unknown_session(self):
        result = handle({"session_id": "pty-999"})
        assert "unknown session" in result

    def test_declined_write(self, declining_display):
        session_id = _start_echo_session()

        result = handle({"session_id": session_id, "chars": "hello\n"})

        assert "declined" in result

    def test_declined_close(self, declining_display):
        session_id = _start_echo_session()

        result = handle({"session_id": session_id, "close": True})

        assert "declined" in result

    def test_validate_session_id(self):
        result = handle({})
        assert "session_id is required" in result

    def test_validate_wait_ms(self):
        session_id = _start_echo_session()

        result = handle({"session_id": session_id, "wait_ms": -1})

        assert "wait_ms must be >= 0" in result

    def test_validate_max_output_chars(self):
        session_id = _start_echo_session()

        result = handle({"session_id": session_id, "max_output_chars": 0})

        assert "max_output_chars must be >= 1" in result

    def test_reject_close_with_chars(self):
        session_id = _start_echo_session()

        result = handle({"session_id": session_id, "chars": "exit\n", "close": True})

        assert "close cannot be combined with chars" in result
