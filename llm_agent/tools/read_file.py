"""read_file tool: read file contents with line numbers."""

import os

from llm_agent.formatting import bold, cyan
from llm_agent.tools.base import _resolve

SCHEMA = {
    "name": "read_file",
    "description": (
        "Read the contents of a file with line numbers. "
        "Returns the total line count and file size so you can decide "
        "whether to read more. Prefer this over cat/head/tail via run_command."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to read.",
            },
            "offset": {
                "type": "integer",
                "description": "Starting line number, 1-based (default: 1).",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of lines to return (default: 200).",
            },
        },
        "required": ["path"],
    },
}


def log(params):
    from llm_agent.display import get_display
    get_display().tool_log(f"  {bold('read_file')}: {cyan(params.get('path', ''))}")

LOG = log


def handle(params):
    path = _resolve(params.get("path", ""))
    offset = max(params.get("offset", 1), 1)
    limit = params.get("limit", 200)

    try:
        size = os.path.getsize(path)
        with open(path, "r", errors="replace") as f:
            lines = f.readlines()
        total = len(lines)
        selected = lines[offset - 1 : offset - 1 + limit]
        numbered = []
        for i, line in enumerate(selected, start=offset):
            numbered.append(f"{i:6}\t{line.rstrip()}")
        header = f"[{path}: {total} lines, {size} bytes]"
        if offset > 1 or offset - 1 + limit < total:
            header += f" (showing lines {offset}-{min(offset - 1 + limit, total)})"
        return header + "\n" + "\n".join(numbered)
    except IsADirectoryError:
        return f"(error: {path} is a directory, use list_directory instead)"
    except Exception as e:
        return f"(error reading {path}: {e})"
