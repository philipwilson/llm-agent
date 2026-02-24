"""edit_file tool: targeted find-and-replace in an existing file."""

import re

from llm_agent.formatting import bold, cyan, dim, red, green
from llm_agent.tools.base import _resolve, confirm_edit


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
        if start_line is None or end_line is None:
            return None, None, None, None, False, "both start_line and end_line are required"
        if start_line < 1:
            return None, None, None, None, False, f"start_line must be >= 1, got {start_line}"
        if end_line < start_line:
            return None, None, None, None, False, f"end_line ({end_line}) must be >= start_line ({start_line})"
        if end_line > len(lines):
            return None, None, None, None, False, f"end_line ({end_line}) exceeds file length ({len(lines)} lines)"

        # Calculate byte offsets for the line range
        offset = sum(len(lines[i]) + 1 for i in range(start_line - 1))
        end_offset = sum(len(lines[i]) + 1 for i in range(end_line))
        # Handle file not ending with newline
        if not content.endswith("\n"):
            end_offset = min(end_offset, len(content))
        old_text = content[offset:end_offset]
        return old_text, new_string, offset, end_offset, False, None

    # String match mode
    count = content.count(old_string)
    if count == 0:
        # Try fuzzy match
        result = _fuzzy_find(content, old_string)
        if result is None:
            return None, None, None, None, False, "old_string not found"
        start, end = result
        old_text = content[start:end]
        return old_text, new_string, start, end, True, None
    if count > 1:
        return None, None, None, None, False, f"old_string matches {count} locations, must be unique"

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


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def handle(params, auto_approve=False):
    path = _resolve(params.get("path", ""))

    try:
        with open(path, "r") as f:
            content = f.read()
    except Exception as e:
        return f"(error reading {path}: {e})"

    lines = content.splitlines()
    edits_param = params.get("edits")

    # Build list of edit operations
    if edits_param:
        ops = edits_param
    else:
        ops = [params]

    # Validate all edits first
    validated = []
    for i, op in enumerate(ops):
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
        with open(path, "w") as f:
            f.write(result)
        if edits_param:
            return f"(applied {len(validated)} edits to {path})"
        return f"(edited {path})"
    except Exception as e:
        return f"(error writing {path}: {e})"
