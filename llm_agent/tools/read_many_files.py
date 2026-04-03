"""read_many_files tool: batch file reads with include/exclude controls."""

import glob
import os

from llm_agent.formatting import bold, cyan
from llm_agent.tools.base import _resolve, read_text_file
from llm_agent.tools.read_file import DEFAULT_READ_LIMIT, format_file_excerpt


SCHEMA = {
    "name": "read_many_files",
    "description": (
        "Read a small focused set of files in one tool call. Provide explicit paths, "
        "include glob patterns, or both. Supports exclude patterns plus per-file "
        "offset/limit controls. Prefer this over repeated read_file calls when you "
        "already know the small set of files you need."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Base directory for include/exclude glob patterns (default: current directory).",
            },
            "paths": {
                "type": "array",
                "description": "Explicit file paths to read.",
                "items": {"type": "string"},
            },
            "include": {
                "type": "array",
                "description": "Glob patterns to include, relative to path (e.g. ['src/**/*.py']).",
                "items": {"type": "string"},
            },
            "exclude": {
                "type": "array",
                "description": "Glob patterns to exclude, relative to path.",
                "items": {"type": "string"},
            },
            "offset": {
                "type": "integer",
                "description": f"Starting line number for each file, 1-based (default: 1).",
            },
            "limit": {
                "type": "integer",
                "description": f"Maximum number of lines to return per file (default: {DEFAULT_READ_LIMIT}).",
            },
            "max_files": {
                "type": "integer",
                "description": "Maximum number of files to read (default: 10).",
            },
        },
    },
}


def log(params):
    from llm_agent.display import get_display

    base = params.get("path", ".")
    get_display().tool_log(f"  {bold('read_many_files')}: {cyan(base)}")


LOG = log


def _matches_exclude(candidate_path, root, exclude_patterns):
    if not exclude_patterns:
        return False
    try:
        rel_path = os.path.relpath(candidate_path, root)
    except ValueError:
        return False
    return any(glob.fnmatch.fnmatch(rel_path, pattern) for pattern in exclude_patterns)


def _resolve_explicit_paths(root, paths):
    resolved = []
    for raw_path in paths:
        if os.path.isabs(raw_path):
            resolved.append(os.path.normpath(raw_path))
        else:
            resolved.append(os.path.normpath(os.path.join(root, raw_path)))
    return resolved


def _resolve_include_matches(root, include_patterns):
    matches = []
    for pattern in include_patterns:
        found = glob.glob(pattern, root_dir=root, recursive=True)
        for rel_path in found:
            candidate = os.path.normpath(os.path.join(root, rel_path))
            if os.path.isfile(candidate):
                matches.append(candidate)
    return sorted(matches)


def handle(params, context=None):
    root = _resolve(params.get("path", "."))
    explicit_paths = params.get("paths") or []
    include_patterns = params.get("include") or []
    exclude_patterns = params.get("exclude") or []
    offset = params.get("offset", 1)
    limit = params.get("limit", DEFAULT_READ_LIMIT)
    max_files = params.get("max_files", 10)

    if not os.path.isdir(root):
        return f"(error: directory not found: {root})"
    if not explicit_paths and not include_patterns:
        return "(error: provide at least one explicit path or include glob pattern)"
    if max_files < 1:
        return f"(error: max_files must be >= 1, got {max_files})"

    candidates = []
    seen = set()

    for candidate in _resolve_explicit_paths(root, explicit_paths):
        if not os.path.exists(candidate):
            return f"(error: file not found: {candidate})"
        if not os.path.isfile(candidate):
            return f"(error: not a file: {candidate})"
        if _matches_exclude(candidate, root, exclude_patterns):
            continue
        if candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)

    for candidate in _resolve_include_matches(root, include_patterns):
        if _matches_exclude(candidate, root, exclude_patterns):
            continue
        if candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)

    total_files = len(candidates)
    if total_files == 0:
        return "(no files matched)"

    truncated = total_files > max_files
    selected = candidates[:max_files]

    output_lines = [
        f"[read_many_files: {len(selected)} file(s)"
        + (f" shown of {total_files}" if truncated else "")
        + f" from {root}]"
    ]

    for index, path in enumerate(selected):
        try:
            file_info = read_text_file(path)
        except IsADirectoryError:
            return f"(error: {path} is a directory, use list_directory instead)"
        except Exception as e:
            return f"(error reading {path}: {e})"

        if context:
            observations = context.get("file_observations")
            if observations is not None:
                observations.record_read(path, file_info["stat"])

        if index:
            output_lines.append("")
        try:
            output_lines.append(format_file_excerpt(path, file_info, offset=offset, limit=limit))
        except ValueError as e:
            return f"(error reading {path}: {e})"

    if truncated:
        output_lines.append("")
        output_lines.append(f"(truncated file list; narrow include patterns or increase max_files from {max_files})")

    return "\n".join(output_lines)
