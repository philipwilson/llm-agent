"""glob_files tool: find files by glob pattern recursively."""

import glob
import os
from pathlib import Path

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
            "exclude": {
                "type": "array",
                "description": "Glob patterns to exclude, relative to the search directory.",
                "items": {"type": "string"},
            },
            "hidden": {
                "type": "boolean",
                "description": "Include hidden files and hidden-path matches (default: false).",
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


def _is_hidden_path(rel_path):
    parts = rel_path.split(os.sep)
    return any(part.startswith(".") for part in parts if part)


def handle(params):
    pattern = params.get("pattern", "")
    root = _resolve(params.get("path", "."))
    exclude_patterns = params.get("exclude") or []
    hidden = params.get("hidden", False)
    max_results = params.get("max_results", 200)

    if not os.path.isdir(root):
        return f"(error: directory not found: {root})"
    if max_results < 1:
        return f"(error: max_results must be >= 1, got {max_results})"

    files = []
    root_path = Path(root)
    matches = sorted(root_path.glob(pattern))
    for match in matches:
        if not match.is_file():
            continue
        normalized = os.path.normpath(str(match.relative_to(root_path)))
        if not hidden and _is_hidden_path(normalized):
            continue
        if any(glob.fnmatch.fnmatch(normalized, exclude) for exclude in exclude_patterns):
            continue
        files.append(normalized)

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
        result += (
            f"\n... ({total - max_results} more files not shown; "
            f"narrow the pattern/exclude filters or increase max_results from {max_results})"
        )
    return result
