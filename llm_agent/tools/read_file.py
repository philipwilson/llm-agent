"""read_file tool: read file contents with line numbers."""

import os

from llm_agent.formatting import bold, cyan
from llm_agent.tools.base import _resolve, read_text_file

DEFAULT_READ_LIMIT = 200

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


def _validate_window(offset, limit):
    if offset is None:
        offset = 1
    if limit is None:
        limit = DEFAULT_READ_LIMIT
    if offset < 1:
        raise ValueError(f"offset must be >= 1, got {offset}")
    if limit < 1:
        raise ValueError(f"limit must be >= 1, got {limit}")
    return offset, limit


def format_file_excerpt(path, file_info, offset=1, limit=DEFAULT_READ_LIMIT):
    offset, limit = _validate_window(offset, limit)

    content = file_info["content"]
    size = file_info["size"]
    lines = content.splitlines()
    total = len(lines)

    if offset > max(total, 1):
        raise ValueError(
            f"offset ({offset}) exceeds file length ({total} lines); "
            f"use an offset between 1 and {max(total, 1)}"
        )

    selected = lines[offset - 1 : offset - 1 + limit]
    end_line = min(offset - 1 + limit, total)

    header = f"[{path}: {total} lines, {size} bytes]"
    if total and (offset > 1 or end_line < total):
        header += f" (showing lines {offset}-{end_line})"

    output_lines = [header]
    output_lines.extend(f"{i:6}\t{line}" for i, line in enumerate(selected, start=offset))

    if end_line < total:
        output_lines.append(f"(truncated; use offset={end_line + 1} to continue)")

    return "\n".join(output_lines)


def handle(params, context=None):
    path = _resolve(params.get("path", ""))

    try:
        file_info = read_text_file(path)
        stat_result = file_info["stat"]
        if context:
            observations = context.get("file_observations")
            if observations is not None:
                observations.record_read(path, stat_result)
        return format_file_excerpt(
            path,
            file_info,
            offset=params.get("offset", 1),
            limit=params.get("limit", DEFAULT_READ_LIMIT),
        )
    except ValueError as e:
        return f"(error: {e})"
    except IsADirectoryError:
        return f"(error: {path} is a directory, use list_directory instead)"
    except Exception as e:
        return f"(error reading {path}: {e})"
