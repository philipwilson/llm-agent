"""edit_file tool: targeted find-and-replace in an existing file."""

import difflib
import os
import re

from llm_agent.formatting import bold, cyan, dim, red, green
from llm_agent.tools.base import (
    _resolve,
    count_text_lines,
    confirm_edit,
    describe_text_format,
    FileObservationStore,
    find_omission_placeholder,
    normalize_newlines,
    read_text_file,
    write_text_file,
)


SCHEMA = {
    "name": "edit_file",
    "description": (
        "Make a targeted edit to an existing file. Three modes:\n"
        "1. String match: provide old_string + new_string (old_string must match "
        "uniquely; whitespace differences are tolerated as a fallback).\n"
        "2. Line range: provide start_line + end_line + new_string to replace a "
        "range of lines by number (1-based, inclusive).\n"
        "3. Batch: provide an edits array of multiple operations to apply atomically.\n"
        "Always requires user confirmation."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to edit.",
            },
            "old_string": {
                "type": "string",
                "description": "The exact string to find and replace. Must match uniquely in the file.",
            },
            "new_string": {
                "type": "string",
                "description": "The replacement string.",
            },
            "start_line": {
                "type": "integer",
                "description": "First line to replace (1-based, inclusive). Use with end_line instead of old_string.",
            },
            "end_line": {
                "type": "integer",
                "description": "Last line to replace (1-based, inclusive). Use with start_line.",
            },
            "edits": {
                "type": "array",
                "description": (
                    "List of edit operations to apply atomically. Each item is an "
                    "object with either old_string+new_string or start_line+end_line+new_string."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"},
                        "start_line": {"type": "integer"},
                        "end_line": {"type": "integer"},
                    },
                },
            },
        },
        "required": ["path"],
    },
}


NEEDS_CONFIRM = True


# ---------------------------------------------------------------------------
# Fuzzy (whitespace-normalized) matching
# ---------------------------------------------------------------------------

def _normalize_ws(text):
    """Collapse runs of horizontal whitespace to single space, strip trailing per line."""
    lines = text.split("\n")
    return "\n".join(re.sub(r"[ \t]+", " ", line).rstrip() for line in lines)


def _fuzzy_find(content, old_string):
    """Try whitespace-normalized matching. Returns (start, end) in original content or None."""
    norm_old = _normalize_ws(old_string)
    norm_content = _normalize_ws(content)

    count = norm_content.count(norm_old)
    if count != 1:
        return None

    # Find position in normalized content
    norm_pos = norm_content.index(norm_old)

    # Map normalized position back to original content position.
    orig_lines = content.split("\n")
    norm_lines = norm_content.split("\n")

    # Rebuild character offset mapping line-by-line: for each char in
    # normalized content, track the corresponding position in the original.
    orig_offsets = []  # orig_offsets[norm_char_index] = orig_char_index
    orig_pos = 0
    for orig_line, norm_line in zip(orig_lines, norm_lines):
        oi = 0
        ni = 0
        while ni < len(norm_line):
            orig_offsets.append(orig_pos + oi)
            if norm_line[ni] == " ":
                # Skip all whitespace in original that collapsed to this space
                while oi < len(orig_line) and orig_line[oi] in " \t":
                    oi += 1
            else:
                oi += 1
            ni += 1
        # Newline character
        orig_offsets.append(orig_pos + len(orig_line))
        orig_pos += len(orig_line) + 1  # +1 for \n

    start = orig_offsets[norm_pos] if norm_pos < len(orig_offsets) else None
    end_norm = norm_pos + len(norm_old)
    end = orig_offsets[end_norm] if end_norm < len(orig_offsets) else len(content)

    if start is None:
        return None
    return (start, end)


def _line_number_for_offset(content, offset):
    line_entries = content.splitlines(keepends=True)
    if not line_entries:
        return 1

    cursor = 0
    for index, line in enumerate(line_entries, start=1):
        next_cursor = cursor + len(line)
        if offset < next_cursor:
            return index
        cursor = next_cursor
    return len(line_entries)


def _format_line_range(start_line, end_line):
    if start_line == end_line:
        return f"line {start_line}"
    return f"lines {start_line}-{end_line}"


def _trim_excerpt(text, max_chars=80):
    excerpt = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    excerpt = excerpt.replace("\n", "\\n")
    if len(excerpt) <= max_chars:
        return excerpt
    return excerpt[:max_chars - 3] + "..."


def _find_close_match_windows(content, old_string, max_results=3):
    normalized_old = _normalize_ws(old_string).strip()
    if not normalized_old:
        return []

    content_lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if content_lines and content_lines[-1] == "":
        content_lines = content_lines[:-1]
    if not content_lines:
        return []

    target_lines = max(1, old_string.replace("\r\n", "\n").replace("\r", "\n").count("\n") + 1)
    window_sizes = []
    for size in (target_lines - 1, target_lines, target_lines + 1):
        if size >= 1 and size not in window_sizes:
            window_sizes.append(size)

    candidates = []
    seen_ranges = set()
    for window_size in window_sizes:
        if window_size > len(content_lines):
            continue
        for start in range(0, len(content_lines) - window_size + 1):
            end = start + window_size
            snippet = "\n".join(content_lines[start:end])
            score = difflib.SequenceMatcher(
                None,
                normalized_old,
                _normalize_ws(snippet).strip(),
            ).ratio()
            if score < 0.55:
                continue
            line_range = (start + 1, end)
            if line_range in seen_ranges:
                continue
            seen_ranges.add(line_range)
            candidates.append((score, start + 1, end, snippet))

    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    return candidates[:max_results]


def _format_not_found_error(content, old_string):
    message = "old_string not found"
    close_matches = _find_close_match_windows(content, old_string)
    if close_matches:
        rendered_matches = "; ".join(
            f"{_format_line_range(start_line, end_line)}: '{_trim_excerpt(snippet)}'"
            for _, start_line, end_line, snippet in close_matches
        )
        message += f". Closest matches: {rendered_matches}"
    message += ". Re-read the file and copy the exact text, or use start_line/end_line or apply_patch."
    return message


def _format_ambiguous_match_error(content, old_string, count):
    matches = []
    start = 0
    while len(matches) < 3:
        position = content.find(old_string, start)
        if position == -1:
            break
        end = position + len(old_string)
        start_line = _line_number_for_offset(content, position)
        end_line = _line_number_for_offset(content, max(position, end - 1))
        matches.append(_format_line_range(start_line, end_line))
        start = position + 1

    message = f"old_string matches {count} locations, must be unique"
    if matches:
        extra = count - len(matches)
        rendered_matches = ", ".join(matches)
        if extra > 0:
            rendered_matches += f", +{extra} more"
        message += f". Matches at {rendered_matches}"
    message += ". Add more surrounding context, use start_line/end_line, or use apply_patch."
    return message


# ---------------------------------------------------------------------------
# Single edit validation and positioning
# ---------------------------------------------------------------------------

def _validate_single_edit(edit, content, lines):
    """Validate and locate a single edit operation.

    Returns (old_text, new_text, start_offset, end_offset, fuzzy, error).
    On error, only error is set; on success, error is None.
    """
    old_string = edit.get("old_string")
    new_string = edit.get("new_string", "")
    start_line = edit.get("start_line")
    end_line = edit.get("end_line")

    has_old = old_string is not None and old_string != ""
    has_lines = start_line is not None or end_line is not None

    if has_old and has_lines:
        return None, None, None, None, False, "cannot combine old_string with start_line/end_line"
    if not has_old and not has_lines:
        return None, None, None, None, False, "must provide either old_string or start_line+end_line"

    if has_lines:
        line_entries = content.splitlines(keepends=True)
        if start_line is None or end_line is None:
            return None, None, None, None, False, "both start_line and end_line are required"
        if start_line < 1:
            return None, None, None, None, False, f"start_line must be >= 1, got {start_line}"
        if end_line < start_line:
            return None, None, None, None, False, f"end_line ({end_line}) must be >= start_line ({start_line})"
        if end_line > len(line_entries):
            return (
                None,
                None,
                None,
                None,
                False,
                f"end_line ({end_line}) exceeds file length ({len(line_entries)} lines); "
                "use read_file to confirm the current line numbers",
            )

        # Calculate offsets against the decoded text while preserving native line endings.
        offset = sum(len(line_entries[i]) for i in range(start_line - 1))
        end_offset = sum(len(line_entries[i]) for i in range(end_line))
        old_text = content[offset:end_offset]
        return old_text, new_string, offset, end_offset, False, None

    # String match mode
    count = content.count(old_string)
    if count == 0:
        # Try fuzzy match
        result = _fuzzy_find(content, old_string)
        if result is None:
            return None, None, None, None, False, _format_not_found_error(content, old_string)
        start, end = result
        old_text = content[start:end]
        return old_text, new_string, start, end, True, None
    if count > 1:
        return None, None, None, None, False, _format_ambiguous_match_error(content, old_string, count)

    pos = content.index(old_string)
    return old_string, new_string, pos, pos + len(old_string), False, None


# ---------------------------------------------------------------------------
# Preview generation
# ---------------------------------------------------------------------------

def _build_preview(path, edits_info, header_suffix=""):
    """Build a diff preview from a list of (old_text, new_text, fuzzy) tuples."""
    preview = [f"  {bold('edit_file')}: {cyan(path)}{header_suffix}"]
    for old_text, new_text, fuzzy in edits_info:
        if fuzzy:
            preview.append(f"  {dim('(matched after whitespace normalization)')}")
        for line in old_text.splitlines():
            preview.append(f"  {red('-' + ' ' + line)}")
        for line in (new_text or "").splitlines():
            preview.append(f"  {green('+' + ' ' + line)}")
    return preview


def _summarize_edits(validated):
    old_lines = sum(count_text_lines(old_text) for old_text, _, _, _, _ in validated)
    new_lines = sum(count_text_lines(new_text or "") for _, new_text, _, _, _ in validated)
    return {
        "edit_count": len(validated),
        "old_lines": old_lines,
        "new_lines": new_lines,
    }


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def handle(params, auto_approve=False, context=None):
    path = _resolve(params.get("path", ""))

    try:
        file_info = read_text_file(path)
    except Exception as e:
        return f"(error reading {path}: {e})"

    content = file_info["content"]
    stat_result = file_info["stat"]
    newline_style = file_info["newline_style"]
    encoding = file_info["encoding"]

    if context:
        observations = context.get("file_observations")
        if observations is not None:
            freshness_error = observations.validate_fresh(path, stat_result, "edit")
            if freshness_error:
                return freshness_error

    initial_snapshot = FileObservationStore.snapshot_stat(stat_result)
    lines = content.splitlines(keepends=True)
    edits_param = params.get("edits")

    # Build list of edit operations
    if edits_param:
        ops = [
            {
                **op,
                **(
                    {"old_string": normalize_newlines(op["old_string"], newline_style)}
                    if op.get("old_string") is not None else {}
                ),
                "new_string": normalize_newlines(op.get("new_string", ""), newline_style),
            }
            for op in edits_param
        ]
    else:
        ops = [{
            **params,
            **(
                {"old_string": normalize_newlines(params["old_string"], newline_style)}
                if params.get("old_string") is not None else {}
            ),
            "new_string": normalize_newlines(params.get("new_string", ""), newline_style),
        }]

    # Validate all edits first
    validated = []
    for i, op in enumerate(ops):
        placeholder = find_omission_placeholder(op.get("new_string", ""))
        if placeholder:
            prefix = f"edit {i + 1}: " if edits_param else ""
            return (
                f"(error: {prefix}new_string appears to contain an omission placeholder; "
                f"replace it with the full text instead: {placeholder})"
            )
        old_text, new_text, start, end, fuzzy, error = _validate_single_edit(op, content, lines)
        if error:
            prefix = f"edit {i + 1}: " if edits_param else ""
            return f"(error: {prefix}{error} in {path})"
        validated.append((old_text, new_text, start, end, fuzzy))

    # Check for overlapping edits
    validated.sort(key=lambda v: v[2])  # sort by start offset
    for i in range(len(validated) - 1):
        if validated[i][3] > validated[i + 1][2]:
            return f"(error: overlapping edits in {path})"

    # Build preview
    header = ""
    if edits_param:
        header = f" ({len(validated)} edits)"
    elif params.get("start_line") is not None:
        header = f" (lines {params['start_line']}-{params['end_line']})"
    preview_info = [(old_text, new_text, fuzzy) for old_text, new_text, _, _, fuzzy in validated]
    preview = _build_preview(path, preview_info, header)
    summary = _summarize_edits(validated)
    preview.insert(
        1,
        f"  {dim(f'summary: {summary['edit_count']} edits, {summary['old_lines']} lines -> {summary['new_lines']} lines')}",
    )
    preview.insert(
        2,
        f"  {dim(f'format: {describe_text_format(encoding, newline_style)}')}",
    )

    # Confirm
    if auto_approve:
        from llm_agent.display import get_display
        get_display().auto_approved(preview)
    elif not confirm_edit(preview):
        return "(user declined this edit)"

    # Apply edits bottom-to-top so offsets remain valid
    result = content
    for old_text, new_text, start, end, fuzzy in reversed(validated):
        result = result[:start] + (new_text or "") + result[end:]

    try:
        try:
            current_stat = os.stat(path)
        except FileNotFoundError:
            return (
                f"(error: {path} changed while waiting for confirmation; "
                "use read_file again before editing)"
            )
        if not FileObservationStore.matches_snapshot(initial_snapshot, current_stat):
            return (
                f"(error: {path} changed while waiting for confirmation; "
                "use read_file again before editing)"
            )
        write_text_file(path, result, encoding)
        if edits_param:
            return (
                f"(applied {summary['edit_count']} edits to {path}; "
                f"lines={summary['old_lines']}->{summary['new_lines']}; "
                f"format={describe_text_format(encoding, newline_style)})"
            )
        return (
            f"(edited {path}; lines={summary['old_lines']}->{summary['new_lines']}; "
            f"format={describe_text_format(encoding, newline_style)})"
        )
    except Exception as e:
        return f"(error writing {path}: {e})"
