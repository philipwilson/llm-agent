"""Tests for Display protocol."""

import threading

import pytest

from llm_agent.display import Display, get_display, set_display


class TestDisplayProtocol:
    def test_get_set_display(self):
        original = get_display()
        new_display = Display()
        set_display(new_display)
        assert get_display() is new_display
        set_display(original)

    def test_suppress_streaming(self, capsys):
        display = Display()
        with display.suppress_streaming():
            display.stream_start()
            display.stream_token("hidden")
            display.stream_end()
        captured = capsys.readouterr()
        assert "hidden" not in captured.out

    def test_streaming_not_suppressed_by_default(self, capsys):
        display = Display()
        display.stream_token("visible")
        captured = capsys.readouterr()
        assert "visible" in captured.out

    def test_suppress_streaming_thread_local(self):
        """Suppression in one thread should not affect another."""
        display = Display()
        results = {}

        def thread_fn():
            results["suppressed"] = display._is_streaming_suppressed()

        with display.suppress_streaming():
            t = threading.Thread(target=thread_fn)
            t.start()
            t.join()
            assert display._is_streaming_suppressed() is True
            assert results["suppressed"] is False

    def test_subagent_counting(self):
        display = Display()
        assert display.active_subagents == 0
        display.subagent_started()
        assert display.active_subagents == 1
        display.subagent_started()
        assert display.active_subagents == 2
        display.subagent_finished()
        assert display.active_subagents == 1
        display.subagent_finished()
        assert display.active_subagents == 0

    def test_subagent_finished_floors_at_zero(self):
        display = Display()
        display.subagent_finished()
        assert display.active_subagents == 0

    def test_confirm_accepts_yes(self, monkeypatch):
        display = Display()
        monkeypatch.setattr("builtins.input", lambda _: "y")
        assert display.confirm(["preview"], "Apply?") is True

    def test_confirm_accepts_empty(self, monkeypatch):
        display = Display()
        monkeypatch.setattr("builtins.input", lambda _: "")
        assert display.confirm(["preview"], "Apply?") is True

    def test_confirm_rejects_no(self, monkeypatch):
        display = Display()
        monkeypatch.setattr("builtins.input", lambda _: "n")
        assert display.confirm(["preview"], "Apply?") is False

    def test_ask_user_returns_answer(self, monkeypatch):
        display = Display()
        monkeypatch.setattr("builtins.input", lambda _: "my answer")
        result = display.ask_user("What?")
        assert result == "my answer"

    def test_ask_user_empty_returns_default(self, monkeypatch):
        display = Display()
        monkeypatch.setattr("builtins.input", lambda _: "")
        result = display.ask_user("What?")
        assert result == "(no answer provided)"

    def test_ask_user_eof(self, monkeypatch):
        display = Display()
        def raise_eof(_):
            raise EOFError
        monkeypatch.setattr("builtins.input", raise_eof)
        result = display.ask_user("What?")
        assert result == "(no answer provided)"
