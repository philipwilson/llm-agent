"""MCP client: connect to MCP servers, discover tools, dispatch calls."""

import asyncio
import json
import logging
import os
import threading
from contextlib import AsyncExitStack

logger = logging.getLogger(__name__)


def load_mcp_config():
    """Load MCP server config from .mcp.json (project) and ~/.mcp.json (user).

    Project-level servers override user-level servers with the same name.
    Returns dict of server_name -> server_config, or None if no config found.
    """
    servers = {}
    for path in [
        os.path.expanduser("~/.mcp.json"),
        os.path.join(os.getcwd(), ".mcp.json"),  # project takes priority
    ]:
        if os.path.isfile(path):
            with open(path) as f:
                data = json.load(f)
            for name, cfg in data.get("mcpServers", {}).items():
                servers[name] = cfg
    return servers or None


def _format_tool_result(result):
    """Convert MCP CallToolResult to a plain string."""
    parts = []
    for block in result.content:
        if hasattr(block, "text"):
            parts.append(block.text)
        elif hasattr(block, "type"):
            parts.append(f"[{block.type}: {getattr(block, 'mimeType', 'unknown')}]")
    text = "\n".join(parts) if parts else "(no output)"
    if result.isError:
        return f"Error: {text}"
    return text


class MCPManager:
    """Manages MCP server connections and tool dispatch.

    The MCP Python SDK is fully async, but the agent runs synchronously in
    threads.  Solution: a dedicated asyncio event loop in a daemon thread
    keeps MCP sessions alive for the duration of the agent session.

    Sync wrappers submit coroutines to that loop via
    ``asyncio.run_coroutine_threadsafe`` and block for the result.  This is
    thread-safe -- multiple parallel tool calls from ``dispatch_tool_calls()``
    can submit to the same loop concurrently.
    """

    def __init__(self):
        self._loop = None
        self._thread = None
        self._exit_stack = None
        self._sessions = {}       # server_name -> ClientSession
        self._tool_map = {}       # tool_name -> server_name
        self._tools = []          # list of (schema, registry_entry)

    def start(self, config):
        """Connect to all configured MCP servers and register tools."""
        from llm_agent.display import get_display

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, daemon=True, name="mcp-event-loop"
        )
        self._thread.start()

        future = asyncio.run_coroutine_threadsafe(
            self._connect_all(config), self._loop
        )
        future.result(timeout=30)  # block until all servers connected

        if self._tools:
            from llm_agent.tools import register_mcp_tools
            register_mcp_tools(self._tools)
            count = len(self._tools)
            servers = len(self._sessions)
            get_display().status(
                f"  [MCP: {count} tool{'s' if count != 1 else ''} "
                f"from {servers} server{'s' if servers != 1 else ''}]"
            )

    async def _connect_all(self, config):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from llm_agent.tools import TOOL_REGISTRY

        self._exit_stack = AsyncExitStack()

        for name, cfg in config.items():
            try:
                params = StdioServerParameters(
                    command=cfg["command"],
                    args=cfg.get("args", []),
                    env={**os.environ, **cfg["env"]} if cfg.get("env") else None,
                )
                transport = await self._exit_stack.enter_async_context(
                    stdio_client(params)
                )
                read, write = transport
                session = await self._exit_stack.enter_async_context(
                    ClientSession(read, write)
                )
                await session.initialize()
                self._sessions[name] = session

                # Discover tools
                response = await session.list_tools()
                for tool in response.tools:
                    if tool.name in TOOL_REGISTRY or tool.name in self._tool_map:
                        logger.warning(
                            "MCP tool '%s' from '%s' skipped (name collision)",
                            tool.name, name,
                        )
                        continue
                    self._tool_map[tool.name] = name
                    self._tools.append(self._make_tool_entry(name, tool))

            except Exception as e:
                from llm_agent.display import get_display
                get_display().error(f"  [MCP server '{name}' failed: {e}]")

    def _make_tool_entry(self, server_name, mcp_tool):
        """Create (schema, registry_entry) for an MCP tool."""
        from llm_agent.display import get_display
        from llm_agent.formatting import bold, dim

        schema = {
            "name": mcp_tool.name,
            "description": mcp_tool.description or "",
            "input_schema": mcp_tool.inputSchema
                if hasattr(mcp_tool, "inputSchema")
                else {"type": "object", "properties": {}},
        }

        tool_name = mcp_tool.name
        sname = server_name

        def log(params):
            get_display().tool_log(
                f"  {bold(tool_name)}: {dim(f'[{sname}]')} "
                f"{dim(str(params)[:120])}"
            )

        def handler(params):
            return self.call_tool(tool_name, params)

        entry = {"handler": handler, "log": log}
        return schema, entry

    def format_status(self):
        """Return a formatted string listing connected servers and their tools."""
        if not self._sessions:
            return "(no MCP servers connected)"
        lines = []
        # Group tools by server
        server_tools = {}
        for tool_name, server_name in self._tool_map.items():
            server_tools.setdefault(server_name, []).append(tool_name)
        for name in sorted(self._sessions):
            tools = sorted(server_tools.get(name, []))
            lines.append(f"  {name} ({len(tools)} tools)")
            for t in tools:
                lines.append(f"    - {t}")
        return "\n".join(lines)

    def call_tool(self, tool_name, params):
        """Call an MCP tool synchronously (blocks until result)."""
        server_name = self._tool_map.get(tool_name)
        if not server_name or server_name not in self._sessions:
            return f"(MCP server not found for tool: {tool_name})"

        future = asyncio.run_coroutine_threadsafe(
            self._async_call_tool(server_name, tool_name, params),
            self._loop,
        )
        return future.result()

    async def _async_call_tool(self, server_name, tool_name, params):
        try:
            session = self._sessions[server_name]
            result = await session.call_tool(tool_name, params)
            return _format_tool_result(result)
        except Exception as e:
            return f"Error: MCP tool '{tool_name}' failed: {e}"

    def stop(self):
        """Shut down all MCP servers and the event loop."""
        if self._loop and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                self._cleanup(), self._loop
            )
            try:
                future.result(timeout=5)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=3)

        if self._tools:
            from llm_agent.tools import unregister_mcp_tools
            unregister_mcp_tools()
            self._tools = []

    async def _cleanup(self):
        if self._exit_stack:
            await self._exit_stack.aclose()


# Module-level singleton
_manager = None


def get_mcp_manager():
    """Return the global MCPManager singleton, creating it if needed."""
    global _manager
    if _manager is None:
        _manager = MCPManager()
    return _manager
