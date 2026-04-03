"""Tests for tool dispatch: parallel/sequential routing, timeouts."""

import time
import threading

import pytest

from llm_agent.tools import dispatch_tool_calls


def _make_tool_use(name, params=None, tool_id=None):
    return {
        "id": tool_id or f"tool_{name}",
        "name": name,
        "input": params or {},
    }


def _make_registry(handlers):
    """Build a registry from {name: handler_fn} dict."""
    return {
        name: {"handler": fn}
        for name, fn in handlers.items()
    }


class TestDispatch:
    def test_single_tool(self):
        registry = _make_registry({"echo": lambda p: f"got: {p.get('msg', '')}"})
        results = dispatch_tool_calls(
            [_make_tool_use("echo", {"msg": "hi"})],
            registry,
        )
        assert len(results) == 1
        assert "got: hi" in results[0]["content"]

    def test_unknown_tool(self):
        results = dispatch_tool_calls(
            [_make_tool_use("nonexistent")],
            {},
        )
        assert "unknown tool" in results[0]["content"]

    def test_tool_exception(self):
        def bad_tool(p):
            raise ValueError("boom")
        registry = _make_registry({"bad": bad_tool})
        results = dispatch_tool_calls([_make_tool_use("bad")], registry)
        assert "error" in results[0]["content"]
        assert "boom" in results[0]["content"]

    def test_parallel_execution(self):
        """Multiple safe tools should run in parallel."""
        call_times = {}
        def slow_tool(p):
            call_times[threading.current_thread().name] = time.time()
            time.sleep(0.2)
            return f"done-{p.get('id', '')}"

        registry = _make_registry({"slow": slow_tool})
        tool_uses = [
            _make_tool_use("slow", {"id": "1"}, "t1"),
            _make_tool_use("slow", {"id": "2"}, "t2"),
        ]
        start = time.time()
        results = dispatch_tool_calls(tool_uses, registry)
        elapsed = time.time() - start

        assert len(results) == 2
        assert all("done-" in r["content"] for r in results)
        # Parallel should be faster than serial (2 * 0.2s)
        assert elapsed < 0.35

    def test_sequential_for_confirm_tools(self):
        """Tools needing confirmation should run sequentially."""
        order = []
        def tool_a(p, auto_approve=False):
            order.append("a")
            return "a done"
        def tool_b(p, auto_approve=False):
            order.append("b")
            return "b done"

        registry = {
            "a": {"handler": tool_a, "needs_confirm": True},
            "b": {"handler": tool_b, "needs_confirm": True},
        }
        results = dispatch_tool_calls(
            [_make_tool_use("a", tool_id="t1"), _make_tool_use("b", tool_id="t2")],
            registry,
            auto_approve=False,
        )
        assert len(results) == 2
        assert order == ["a", "b"]

    def test_needs_sequential_flag(self):
        """Tools with NEEDS_SEQUENTIAL always run sequentially."""
        def seq_tool(p):
            return "sequential"

        registry = {
            "seq": {"handler": seq_tool, "needs_sequential": True},
        }
        results = dispatch_tool_calls([_make_tool_use("seq")], registry)
        assert results[0]["content"] == "sequential"

    def test_result_order_matches_input(self):
        """Results should be in the same order as tool_uses."""
        def tool_fn(p):
            return f"result-{p['n']}"

        registry = _make_registry({"t": tool_fn})
        tool_uses = [
            _make_tool_use("t", {"n": i}, f"id-{i}")
            for i in range(5)
        ]
        results = dispatch_tool_calls(tool_uses, registry)
        for i, r in enumerate(results):
            assert f"result-{i}" in r["content"]
            assert r["tool_use_id"] == f"id-{i}"

    def test_timeout_handling(self):
        """Tools that exceed their timeout should return a timeout message."""
        def hanging_tool(p):
            time.sleep(5)
            return "done"

        registry = {
            "hang": {"handler": hanging_tool, "timeout": 0.3},
        }
        # Need at least 2 tools for threading to kick in
        tool_uses = [
            _make_tool_use("hang", tool_id="t1"),
            _make_tool_use("hang", tool_id="t2"),
        ]
        results = dispatch_tool_calls(tool_uses, registry)
        assert any("timed out" in r["content"] for r in results)

    def test_auto_approve_makes_confirm_parallel(self):
        """With auto_approve, confirm tools should run in parallel."""
        call_times = {}
        def confirm_tool(p, auto_approve=False):
            call_times[p.get("id")] = time.time()
            time.sleep(0.2)
            return f"done-{p.get('id', '')}"

        registry = {
            "ct": {"handler": confirm_tool, "needs_confirm": True},
        }
        tool_uses = [
            _make_tool_use("ct", {"id": "1"}, "t1"),
            _make_tool_use("ct", {"id": "2"}, "t2"),
        ]
        start = time.time()
        results = dispatch_tool_calls(tool_uses, registry, auto_approve=True)
        elapsed = time.time() - start

        assert len(results) == 2
        # Should be parallel with auto_approve
        assert elapsed < 0.35

    def test_confirm_tools_receive_context_and_auto_approve(self):
        captured = {}

        def tool_fn(p, auto_approve=False, context=None):
            captured["auto_approve"] = auto_approve
            captured["context"] = context
            return "ok"

        registry = {
            "ctx": {
                "handler": tool_fn,
                "needs_confirm": True,
                "context": {"value": 1},
            }
        }

        results = dispatch_tool_calls(
            [_make_tool_use("ctx", tool_id="t1")],
            registry,
            auto_approve=True,
        )

        assert results[0]["content"] == "ok"
        assert captured["auto_approve"] is True
        assert captured["context"] == {"value": 1}
