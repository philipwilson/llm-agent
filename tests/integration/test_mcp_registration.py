"""Integration tests for MCP tool registration and cleanup."""

import pytest

from llm_agent.tools import (
    TOOLS,
    TOOL_REGISTRY,
    register_mcp_tools,
    unregister_mcp_tools,
    build_tool_set,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mcp_tool(name, description="an mcp tool"):
    """Create a (schema, registry_entry) pair mimicking an MCP tool."""
    schema = {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": {}},
    }
    entry = {
        "handler": lambda params: f"mcp result from {name}",
        "log": lambda params: None,
    }
    return schema, entry


@pytest.fixture(autouse=True)
def cleanup_mcp_tools():
    """Ensure MCP tools are cleaned up after each test."""
    yield
    unregister_mcp_tools()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRegisterMcpTools:
    def test_tools_appear_in_registry(self):
        tools = [_make_mcp_tool("mcp_echo"), _make_mcp_tool("mcp_fetch")]
        register_mcp_tools(tools)

        assert "mcp_echo" in TOOL_REGISTRY
        assert "mcp_fetch" in TOOL_REGISTRY

    def test_tools_appear_in_tools_list(self):
        tools = [_make_mcp_tool("mcp_test")]
        register_mcp_tools(tools)

        names = [t["name"] for t in TOOLS]
        assert "mcp_test" in names

    def test_handler_callable(self):
        tools = [_make_mcp_tool("mcp_call")]
        register_mcp_tools(tools)

        handler = TOOL_REGISTRY["mcp_call"]["handler"]
        result = handler({})
        assert "mcp result from mcp_call" in result

    def test_default_timeout(self):
        from llm_agent.tools import MCP_TOOL_TIMEOUT
        tools = [_make_mcp_tool("mcp_timed")]
        register_mcp_tools(tools)

        assert TOOL_REGISTRY["mcp_timed"]["timeout"] == MCP_TOOL_TIMEOUT

    def test_included_in_build_tool_set(self):
        tools = [_make_mcp_tool("mcp_visible")]
        register_mcp_tools(tools)

        schemas, registry = build_tool_set()
        names = {t["name"] for t in schemas}
        assert "mcp_visible" in names
        assert "mcp_visible" in registry

    def test_can_include_by_name(self):
        tools = [_make_mcp_tool("mcp_inc")]
        register_mcp_tools(tools)

        schemas, registry = build_tool_set(include=["read_file", "mcp_inc"])
        names = {t["name"] for t in schemas}
        assert "mcp_inc" in names
        assert "read_file" in names
        assert len(names) == 2

    def test_can_exclude_by_name(self):
        tools = [_make_mcp_tool("mcp_exc")]
        register_mcp_tools(tools)

        schemas, registry = build_tool_set(exclude=["mcp_exc"])
        names = {t["name"] for t in schemas}
        assert "mcp_exc" not in names


class TestUnregisterMcpTools:
    def test_removes_from_registry(self):
        tools = [_make_mcp_tool("mcp_gone")]
        register_mcp_tools(tools)
        assert "mcp_gone" in TOOL_REGISTRY

        unregister_mcp_tools()
        assert "mcp_gone" not in TOOL_REGISTRY

    def test_removes_from_tools_list(self):
        tools = [_make_mcp_tool("mcp_removed")]
        register_mcp_tools(tools)
        names_before = [t["name"] for t in TOOLS]
        assert "mcp_removed" in names_before

        unregister_mcp_tools()
        names_after = [t["name"] for t in TOOLS]
        assert "mcp_removed" not in names_after

    def test_idempotent(self):
        """Calling unregister twice should not error."""
        tools = [_make_mcp_tool("mcp_idem")]
        register_mcp_tools(tools)
        unregister_mcp_tools()
        unregister_mcp_tools()  # should not raise

    def test_builtin_tools_preserved(self):
        """Unregistering MCP tools should not remove builtins."""
        builtin_names = {t["name"] for t in TOOLS}
        tools = [_make_mcp_tool("mcp_temp")]
        register_mcp_tools(tools)
        unregister_mcp_tools()

        remaining_names = {t["name"] for t in TOOLS}
        assert builtin_names == remaining_names


class TestMcpManagerFormat:
    def test_format_status(self):
        from llm_agent.mcp_client import MCPManager
        mgr = MCPManager()
        # No sessions — should return "(no MCP servers connected)"
        assert "no MCP servers" in mgr.format_status()

    def test_format_tool_result_text(self):
        from types import SimpleNamespace
        from llm_agent.mcp_client import _format_tool_result
        result = SimpleNamespace(
            content=[SimpleNamespace(text="hello world")],
            isError=False,
        )
        assert _format_tool_result(result) == "hello world"

    def test_format_tool_result_error(self):
        from types import SimpleNamespace
        from llm_agent.mcp_client import _format_tool_result
        result = SimpleNamespace(
            content=[SimpleNamespace(text="something broke")],
            isError=True,
        )
        assert "Error:" in _format_tool_result(result)
        assert "something broke" in _format_tool_result(result)

    def test_format_tool_result_empty(self):
        from types import SimpleNamespace
        from llm_agent.mcp_client import _format_tool_result
        result = SimpleNamespace(content=[], isError=False)
        assert _format_tool_result(result) == "(no output)"

    def test_call_tool_unknown_server(self):
        from llm_agent.mcp_client import MCPManager
        mgr = MCPManager()
        result = mgr.call_tool("nonexistent_tool", {})
        assert "not found" in result

    def test_call_tool_timeout(self):
        """Tool calls that exceed TOOL_CALL_TIMEOUT return an error string."""
        import asyncio
        from llm_agent.mcp_client import MCPManager

        mgr = MCPManager()
        mgr.TOOL_CALL_TIMEOUT = 0.1  # 100ms for fast test
        mgr._loop = asyncio.new_event_loop()
        import threading
        mgr._thread = threading.Thread(
            target=mgr._loop.run_forever, daemon=True
        )
        mgr._thread.start()

        # Register a fake session and tool mapping
        mgr._tool_map["slow_tool"] = "fake_server"
        mgr._sessions["fake_server"] = "placeholder"

        # Monkey-patch _async_call_tool to sleep longer than the timeout
        async def _slow_call(server_name, tool_name, params):
            await asyncio.sleep(5)
            return "should not reach here"

        mgr._async_call_tool = _slow_call

        result = mgr.call_tool("slow_tool", {})
        assert "timed out" in result
        assert "slow_tool" in result

        mgr._loop.call_soon_threadsafe(mgr._loop.stop)
        mgr._thread.join(timeout=2)
