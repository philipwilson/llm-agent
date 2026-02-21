"""Tool registry: collects schemas and handlers from individual tool modules."""

from llm_agent.tools import (
    read_file,
    list_directory,
    search_files,
    read_url,
    write_file,
    edit_file,
    run_command,
)

_MODULES = [
    read_file,
    list_directory,
    search_files,
    read_url,
    write_file,
    edit_file,
    run_command,
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
