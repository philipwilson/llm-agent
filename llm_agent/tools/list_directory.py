"""list_directory tool: list directory contents with types and sizes."""

import os

from llm_agent.formatting import bold, cyan
from llm_agent.tools.base import _resolve

SCHEMA = {
    "name": "list_directory",
    "description": (
        "List the contents of a directory with file types and sizes. "
        "Prefer this over ls via run_command."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory to list (default: current directory).",
            },
            "hidden": {
                "type": "boolean",
                "description": "Include hidden files/directories (default: false).",
            },
            "depth": {
                "type": "integer",
                "description": "How many directory levels to include (default: 1).",
            },
            "offset": {
                "type": "integer",
                "description": "Starting entry number, 1-based (default: 1).",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of entries to return (default: 200).",
            },
        },
    },
}


def log(params):
    from llm_agent.display import get_display
    get_display().tool_log(f"  {bold('list_directory')}: {cyan(params.get('path', '.'))}")

LOG = log


def _format_size(size):
    if size >= 1_000_000:
        return f"{size / 1_000_000:.1f}M"
    if size >= 1_000:
        return f"{size / 1_000:.1f}k"
    return f"{size}B"


def _collect_entries(root, hidden, max_depth):
    records = []

    def visit(current_path, rel_prefix, depth):
        with os.scandir(current_path) as scanner:
            entries = sorted(scanner, key=lambda entry: entry.name)
            for entry in entries:
                if not hidden and entry.name.startswith("."):
                    continue

                rel_path = os.path.join(rel_prefix, entry.name) if rel_prefix else entry.name

                try:
                    if entry.is_symlink():
                        target = os.readlink(entry.path)
                        records.append((rel_path, f"  {rel_path} -> {target}"))
                    elif entry.is_dir(follow_symlinks=False):
                        records.append((rel_path + "/", f"  {rel_path}/"))
                        if depth < max_depth:
                            visit(entry.path, rel_path, depth + 1)
                    else:
                        records.append(
                            (
                                rel_path,
                                f"  {rel_path}  ({_format_size(entry.stat(follow_symlinks=False).st_size)})",
                            )
                        )
                except OSError:
                    records.append((rel_path, f"  {rel_path}  (?)"))

    visit(root, "", 1)
    records.sort(key=lambda item: item[0])
    return records


def handle(params):
    path = _resolve(params.get("path", "."))
    hidden = params.get("hidden", False)
    depth = params.get("depth", 1)
    offset = params.get("offset", 1)
    limit = params.get("limit", 200)

    try:
        if depth < 1:
            return f"(error: depth must be >= 1, got {depth})"
        if offset < 1:
            return f"(error: offset must be >= 1, got {offset})"
        if limit < 1:
            return f"(error: limit must be >= 1, got {limit})"
        if not os.path.isdir(path):
            return f"(error: {path} is not a directory)"

        records = _collect_entries(path, hidden, depth)
        total = len(records)
        if offset > max(total, 1):
            return (
                f"(error: offset ({offset}) exceeds directory length ({total} entries); "
                f"use an offset between 1 and {max(total, 1)})"
            )

        selected = records[offset - 1 : offset - 1 + limit]
        end_entry = min(offset - 1 + limit, total)

        header = f"[{path}: {total} entries"
        if depth > 1:
            header += f", depth={depth}"
        header += "]"
        if total and (offset > 1 or end_entry < total):
            header += f" (showing entries {offset}-{end_entry})"

        if not selected:
            return header + "\n  (empty)"

        lines = [line for _, line in selected]
        if end_entry < total:
            lines.append(f"  ... (truncated; use offset={end_entry + 1} to continue)")
        return header + "\n" + "\n".join(lines)
    except Exception as e:
        return f"(error listing {path}: {e})"
