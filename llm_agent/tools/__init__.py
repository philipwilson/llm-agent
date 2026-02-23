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
