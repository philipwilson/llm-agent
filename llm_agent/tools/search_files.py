"""search_files tool: regex search over file contents."""

import subprocess

from llm_agent.formatting import bold, cyan
from llm_agent.tools.base import _resolve, COMMAND_TIMEOUT

SCHEMA = {
    "name": "search_files",
    "description": (
        "Search file contents using regex. Returns matching lines with "
        "file paths and line numbers. Respects .gitignore and skips "
        "binary files. Prefer this over grep/rg via run_command."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regex pattern to search for.",
            },
            "path": {
                "type": "string",
                "description": "Directory or file to search in (default: current directory).",
            },
            "glob": {
                "type": "string",
                "description": "Filter to specific file types (e.g. '*.py', '*.js').",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of matching lines to return (default: 50).",
            },
        },
        "required": ["pattern"],
    },
}


def log(params):
    print(
        f"  {bold('search_files')}: {params.get('pattern', '')} in {cyan(params.get('path', '.'))}"
    )

LOG = log


def handle(params):
    pattern = params.get("pattern", "")
    path = _resolve(params.get("path", "."))
    glob_filter = params.get("glob")
    max_results = params.get("max_results", 50)

    # Try ripgrep first, fall back to grep
    cmd = ["rg", "-n", "--no-heading"]
    if glob_filter:
        cmd += ["--glob", glob_filter]
    cmd += [pattern, path]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=COMMAND_TIMEOUT
        )
    except FileNotFoundError:
        # rg not installed, fall back to grep
        cmd = ["grep", "-rn"]
        if glob_filter:
            cmd += ["--include", glob_filter]
        cmd += [pattern, path]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=COMMAND_TIMEOUT
            )
        except subprocess.TimeoutExpired:
            return f"(search timed out after {COMMAND_TIMEOUT}s)"
        except Exception as e:
            return f"(error: {e})"
    except subprocess.TimeoutExpired:
        return f"(search timed out after {COMMAND_TIMEOUT}s)"

    if result.returncode == 1:
        return "(no matches found)"
    if result.returncode > 1:
        return f"(search error: {result.stderr.strip()})"

    lines = result.stdout.splitlines()
    if len(lines) > max_results:
        lines = lines[:max_results]
        lines.append(f"... (results capped at {max_results})")
    return f"[{len(lines)} matches]\n" + "\n".join(lines)
