"""Tool registry: collects schemas and handlers from individual tool modules."""

from llm_agent.tools import (
    read_file,
    list_directory,
    search_files,
    glob_files,
    file_outline,
    read_url,
    write_file,
    edit_file,
    run_command,
    web_search,
    delegate,
)

# Default timeout (seconds) for tools that don't specify one.
DEFAULT_TOOL_TIMEOUT = 120

# Per-tool timeouts.  Tools with their own internal timeouts (read_url,
# web_search, run_command) get a generous outer bound.  Delegate gets a
# long timeout since subagents run multi-step loops.
_TOOL_TIMEOUTS = {
    "read_file": 30,
    "list_directory": 30,
    "search_files": 30,
    "glob_files": 30,
    "file_outline": 30,
    "read_url": 30,         # has internal 15s timeout
    "web_search": 30,       # has internal 15s timeout
    "write_file": 30,
    "edit_file": 30,
    "run_command": None,    # uses its own COMMAND_TIMEOUT
    "delegate": 300,        # subagents need time for multi-step work
}

# MCP tools get this timeout by default
MCP_TOOL_TIMEOUT = 60

_MODULES = [
    read_file,
    list_directory,
    search_files,
    glob_files,
    file_outline,
    read_url,
    write_file,
    edit_file,
    run_command,
    web_search,
    delegate,
]

TOOLS = [m.SCHEMA for m in _MODULES]

TOOL_REGISTRY = {}
for _m in _MODULES:
    _name = _m.SCHEMA["name"]
    _entry = {"handler": _m.handle}
    if hasattr(_m, "LOG"):
        _entry["log"] = _m.LOG
    if getattr(_m, "NEEDS_CONFIRM", False):
        _entry["needs_confirm"] = True
    if _name in _TOOL_TIMEOUTS:
        _entry["timeout"] = _TOOL_TIMEOUTS[_name]
    TOOL_REGISTRY[_name] = _entry


def dispatch_tool_calls(tool_uses, registry, auto_approve=False):
    """Execute tool calls, running safe tools in parallel.

    Tools that need confirmation (and aren't auto-approved) run sequentially
    in the main thread.  All other tools run concurrently via a thread pool.
    Each parallel tool has a timeout (from the registry or DEFAULT_TOOL_TIMEOUT).

    Args:
        tool_uses: list of tool_use dicts (each has 'id', 'name', 'input').
        registry: tool registry mapping name -> entry dict.
        auto_approve: if True, confirmation tools are auto-approved (parallel-safe).

    Returns:
        list of tool_result dicts in the same order as tool_uses.
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError

    from llm_agent.display import get_display

    display = get_display()

    def _run_one(tool_use):
        name = tool_use["name"]
        params = tool_use["input"]
        entry = registry.get(name)
        if entry is None:
            output = f"(unknown tool: {name})"
        else:
            try:
                log_fn = entry.get("log")
                if log_fn:
                    log_fn(params)
                if entry.get("needs_confirm"):
                    output = entry["handler"](params, auto_approve=auto_approve)
                else:
                    output = entry["handler"](params)
            except Exception as e:
                output = f"(error in tool '{name}': {e})"
        display.tool_result(len(output.splitlines()))
        return {
            "type": "tool_result",
            "tool_use_id": tool_use["id"],
            "content": output,
        }

    def _timeout_for(tool_name):
        entry = registry.get(tool_name)
        if entry:
            t = entry.get("timeout")
            if t is not None:
                return t
        return DEFAULT_TOOL_TIMEOUT

    # Classify each tool call
    parallel_idx = []
    sequential_idx = []
    for i, tu in enumerate(tool_uses):
        entry = registry.get(tu["name"])
        if entry and entry.get("needs_confirm") and not auto_approve:
            sequential_idx.append(i)
        else:
            parallel_idx.append(i)

    results = [None] * len(tool_uses)

    # Use threading only when there's actual concurrency to gain
    if len(tool_uses) > 1 and parallel_idx:
        pool = ThreadPoolExecutor(max_workers=4)
        futures = {i: pool.submit(_run_one, tool_uses[i]) for i in parallel_idx}
        # Run sequential tools in main thread while parallel ones execute
        for i in sequential_idx:
            results[i] = _run_one(tool_uses[i])
        # Collect parallel results with per-tool timeouts
        for i, fut in futures.items():
            timeout = _timeout_for(tool_uses[i]["name"])
            try:
                results[i] = fut.result(timeout=timeout)
            except TimeoutError:
                name = tool_uses[i]["name"]
                display.error(f"  (tool '{name}' timed out after {timeout}s)")
                results[i] = {
                    "type": "tool_result",
                    "tool_use_id": tool_uses[i]["id"],
                    "content": f"(tool '{name}' timed out after {timeout}s)",
                }
        # Don't wait for timed-out threads — let them finish in the background
        pool.shutdown(wait=False, cancel_futures=True)
    else:
        # Single tool or all sequential — no threading overhead
        for i in range(len(tool_uses)):
            results[i] = _run_one(tool_uses[i])

    return results


# Track MCP tool names for cleanup
_mcp_tool_names = []


def register_mcp_tools(tools_and_entries):
    """Add MCP tools to the global TOOLS list and TOOL_REGISTRY."""
    global _mcp_tool_names
    for schema, entry in tools_and_entries:
        name = schema["name"]
        entry.setdefault("timeout", MCP_TOOL_TIMEOUT)
        TOOLS.append(schema)
        TOOL_REGISTRY[name] = entry
        _mcp_tool_names.append(name)
    # Invalidate Anthropic tool cache so cache breakpoint is recalculated
    from llm_agent.agent import invalidate_tool_cache
    invalidate_tool_cache()


def unregister_mcp_tools():
    """Remove all MCP tools from global state."""
    global _mcp_tool_names
    names = set(_mcp_tool_names)
    for name in _mcp_tool_names:
        TOOL_REGISTRY.pop(name, None)
    TOOLS[:] = [t for t in TOOLS if t["name"] not in names]
    _mcp_tool_names = []
    from llm_agent.agent import invalidate_tool_cache
    invalidate_tool_cache()


def build_tool_set(include=None, exclude=None):
    """Return (schemas_list, registry_dict) filtered to a subset of tools.

    Args:
        include: if given, only these tool names are included.
        exclude: if given, these tool names are excluded (ignored if include is set).
    """
    if include is not None:
        names = set(include)
    elif exclude is not None:
        names = {t["name"] for t in TOOLS} - set(exclude)
    else:
        names = {t["name"] for t in TOOLS}

    schemas = [t for t in TOOLS if t["name"] in names]
    registry = {k: v for k, v in TOOL_REGISTRY.items() if k in names}
    return schemas, registry
