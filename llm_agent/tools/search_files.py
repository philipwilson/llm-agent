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
            "mode": {
                "type": "string",
                "description": "Result mode: 'content' for matching lines (default) or 'files' for matching file paths only.",
            },
            "context_lines": {
                "type": "integer",
                "description": "Number of surrounding context lines to include in content mode.",
            },
            "max_matches_per_file": {
                "type": "integer",
                "description": "Maximum number of matches to return per file in content mode.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of result lines or file paths to return (default: 50).",
            },
        },
        "required": ["pattern"],
    },
}


def log(params):
    from llm_agent.display import get_display
    get_display().tool_log(
        f"  {bold('search_files')}: {params.get('pattern', '')} in {cyan(params.get('path', '.'))}"
    )

LOG = log


def _build_rg_command(pattern, path, glob_filter, mode, context_lines, max_matches_per_file):
    cmd = ["rg", "--color", "never", "--no-heading"]
    if mode == "files":
        cmd.append("-l")
    else:
        cmd.append("-n")
        if context_lines:
            cmd += ["-C", str(context_lines)]
        if max_matches_per_file is not None:
            cmd += ["--max-count", str(max_matches_per_file)]
    if glob_filter:
        cmd += ["--glob", glob_filter]
    cmd += [pattern, path]
    return cmd


def _build_grep_command(pattern, path, glob_filter, mode, context_lines, max_matches_per_file):
    cmd = ["grep", "-rI"]
    if mode == "files":
        cmd.append("-l")
    else:
        cmd.append("-n")
        if context_lines:
            cmd += ["-C", str(context_lines)]
        if max_matches_per_file is not None:
            cmd += ["-m", str(max_matches_per_file)]
    if glob_filter:
        cmd += ["--include", glob_filter]
    cmd += [pattern, path]
    return cmd


def handle(params):
    pattern = params.get("pattern", "")
    path = _resolve(params.get("path", "."))
    glob_filter = params.get("glob")
    mode = params.get("mode", "content")
    context_lines = params.get("context_lines", 0)
    max_matches_per_file = params.get("max_matches_per_file")
    max_results = params.get("max_results", 50)

    if not pattern:
        return "(error: pattern is required)"
    if mode not in ("content", "files"):
        return f"(error: unsupported mode '{mode}', expected 'content' or 'files')"
    if context_lines < 0:
        return f"(error: context_lines must be >= 0, got {context_lines})"
    if max_matches_per_file is not None and max_matches_per_file < 1:
        return f"(error: max_matches_per_file must be >= 1, got {max_matches_per_file})"
    if max_results < 1:
        return f"(error: max_results must be >= 1, got {max_results})"

    # Try ripgrep first, fall back to grep
    cmd = _build_rg_command(pattern, path, glob_filter, mode, context_lines, max_matches_per_file)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=COMMAND_TIMEOUT
        )
    except FileNotFoundError:
        # rg not installed, fall back to grep
        cmd = _build_grep_command(
            pattern,
            path,
            glob_filter,
            mode,
            context_lines,
            max_matches_per_file,
        )
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
    total_results = len(lines)
    if len(lines) > max_results:
        lines = lines[:max_results]
        lines.append(
            f"... (results capped at {max_results}; narrow the pattern or increase max_results)"
        )

    label = "files" if mode == "files" else "matches"
    header_count = min(total_results, max_results)
    return f"[{header_count} {label}]\n" + "\n".join(lines)
