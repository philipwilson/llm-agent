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
        },
    },
}


def log(params):
    from llm_agent.display import get_display
    get_display().tool_log(f"  {bold('list_directory')}: {cyan(params.get('path', '.'))}")

LOG = log


def handle(params):
    path = _resolve(params.get("path", "."))
    hidden = params.get("hidden", False)

    try:
        entries = sorted(os.listdir(path))
        if not hidden:
            entries = [e for e in entries if not e.startswith(".")]
        lines = []
        for name in entries:
            full = os.path.join(path, name)
            try:
                st = os.lstat(full)
                if os.path.islink(full):
                    target = os.readlink(full)
                    lines.append(f"  {name} -> {target}")
                elif os.path.isdir(full):
                    lines.append(f"  {name}/")
                else:
                    size = st.st_size
                    if size >= 1_000_000:
                        size_str = f"{size / 1_000_000:.1f}M"
                    elif size >= 1_000:
                        size_str = f"{size / 1_000:.1f}k"
                    else:
                        size_str = f"{size}B"
                    lines.append(f"  {name}  ({size_str})")
            except OSError:
                lines.append(f"  {name}  (?)")
        header = f"[{path}: {len(entries)} entries]"
        return header + "\n" + "\n".join(lines) if lines else header + "\n  (empty)"
    except Exception as e:
        return f"(error listing {path}: {e})"
