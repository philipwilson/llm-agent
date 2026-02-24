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
    TOOL_REGISTRY[_name] = _entry


def dispatch_tool_calls(tool_uses, registry, auto_approve=False):
    """Execute tool calls, running safe tools in parallel.

    Tools that need confirmation (and aren't auto-approved) run sequentially
    in the main thread.  All other tools run concurrently via a thread pool.

    Args:
        tool_uses: list of tool_use dicts (each has 'id', 'name', 'input').
        registry: tool registry mapping name -> entry dict.
        auto_approve: if True, confirmation tools are auto-approved (parallel-safe).

    Returns:
        list of tool_result dicts in the same order as tool_uses.
    """
    from concurrent.futures import ThreadPoolExecutor

    from llm_agent.display import get_display

    display = get_display()

    def _run_one(tool_use):
        name = tool_use["name"]
        params = tool_use["input"]
        entry = registry.get(name)
        if entry is None:
            output = f"(unknown tool: {name})"
        else:
            log_fn = entry.get("log")
            if log_fn:
                log_fn(params)
            if entry.get("needs_confirm"):
                output = entry["handler"](params, auto_approve=auto_approve)
            else:
                output = entry["handler"](params)
        display.tool_result(len(output.splitlines()))
        return {
            "type": "tool_result",
            "tool_use_id": tool_use["id"],
            "content": output,
        }

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
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {i: pool.submit(_run_one, tool_uses[i]) for i in parallel_idx}
            # Run sequential tools in main thread while parallel ones execute
            for i in sequential_idx:
                results[i] = _run_one(tool_uses[i])
            # Collect parallel results
            for i, fut in futures.items():
                results[i] = fut.result()
    else:
        # Single tool or all sequential — no threading overhead
        for i in range(len(tool_uses)):
            results[i] = _run_one(tool_uses[i])

    return results


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
