"""glob_files tool: find files by glob pattern recursively."""

import glob
import os

from llm_agent.formatting import bold, cyan
from llm_agent.tools.base import _resolve

SCHEMA = {
    "name": "glob_files",
    "description": (
        "Find files matching a glob pattern recursively. Returns file paths "
        "relative to the search directory. Supports ** for recursive matching. "
        "Prefer this over find/ls via run_command for file discovery."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern (e.g. '**/*.py', 'src/**/test_*.js').",
            },
            "path": {
                "type": "string",
                "description": "Directory to search in (default: current directory).",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of files to return (default: 200).",
            },
        },
        "required": ["pattern"],
    },
}


def log(params):
    from llm_agent.display import get_display
    get_display().tool_log(
        f"  {bold('glob_files')}: {params.get('pattern', '')} in {cyan(params.get('path', '.'))}"
    )

LOG = log


def handle(params):
    pattern = params.get("pattern", "")
    root = _resolve(params.get("path", "."))
    max_results = params.get("max_results", 200)

    if not os.path.isdir(root):
        return f"(error: directory not found: {root})"

    matches = glob.glob(pattern, root_dir=root, recursive=True)

    # Filter to files only (exclude directories)
    files = sorted(f for f in matches if os.path.isfile(os.path.join(root, f)))

    total = len(files)
    header = f"[{total} files matching {pattern} in {root}]"

    if total == 0:
        return header + "\n(no matches)"

    truncated = False
    if total > max_results:
        files = files[:max_results]
        truncated = True

    result = header + "\n" + "\n".join(files)
    if truncated:
        result += f"\n... ({total - max_results} more files not shown)"
    return result
