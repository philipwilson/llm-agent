"""write_file tool: create or overwrite a file."""

import os

from llm_agent.formatting import bold, cyan, dim, green
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
    "name": "write_file",
    "description": (
        "Create or overwrite a file with the given content. "
        "Use this to create new files. Always requires user confirmation."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to write.",
            },
            "content": {
                "type": "string",
                "description": "The full content to write to the file.",
            },
        },
        "required": ["path", "content"],
    },
}


NEEDS_CONFIRM = True


def handle(params, auto_approve=False, context=None):
    path = _resolve(params.get("path", ""))
    content = params.get("content", "")
    placeholder = find_omission_placeholder(content)
    if placeholder:
        return (
            "(error: content appears to contain an omission placeholder; "
            f"replace it with the full text instead: {placeholder})"
        )
    exists = os.path.exists(path)
    existing_snapshot = None
    target_encoding = None
    target_newline_style = None
    if exists:
        try:
            file_info = read_text_file(path)
        except OSError as e:
            return f"(error reading {path}: {e})"
        except Exception as e:
            return f"(error reading {path}: {e})"
        stat_result = file_info["stat"]
        existing_snapshot = FileObservationStore.snapshot_stat(stat_result)
        target_encoding = file_info["encoding"]
        target_newline_style = file_info["newline_style"]
        if context:
            observations = context.get("file_observations")
            if observations is not None:
                freshness_error = observations.validate_fresh(path, stat_result, "overwrite")
                if freshness_error:
                    return freshness_error
        content = normalize_newlines(content, target_newline_style)
    action = "overwrite" if exists else "create"
    line_count = count_text_lines(content)
    char_count = len(content)

    preview = [
        f"  {bold('write_file')}: {action} {cyan(path)} ({line_count} lines, {char_count} chars)"
    ]
    if target_encoding is not None:
        preview.append(
            f"  {dim(f'format: {describe_text_format(target_encoding, target_newline_style)}')}"
        )
    # Show a compact preview -- first/last few lines
    lines = content.splitlines()
    if len(lines) <= 10:
        for line in lines:
            preview.append(f"  {green('+' + ' ' + line)}")
    else:
        for line in lines[:5]:
            preview.append(f"  {green('+' + ' ' + line)}")
        preview.append(f"  {dim(f'... ({len(lines) - 10} more lines) ...')}")
        for line in lines[-5:]:
            preview.append(f"  {green('+' + ' ' + line)}")

    if auto_approve:
        from llm_agent.display import get_display
        get_display().auto_approved(preview)
    elif not confirm_edit(preview):
        return "(user declined to write this file)"

    try:
        if existing_snapshot is None:
            if os.path.exists(path):
                return (
                    f"(error: {path} was created while waiting for confirmation; "
                    "read it with read_file before overwriting it)"
                )
        else:
            try:
                current_stat = os.stat(path)
            except FileNotFoundError:
                return (
                    f"(error: {path} changed while waiting for confirmation; "
                    "use read_file again before overwriting it)"
                )
            if not FileObservationStore.matches_snapshot(existing_snapshot, current_stat):
                return (
                    f"(error: {path} changed while waiting for confirmation; "
                    "use read_file again before overwriting it)"
                )
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if target_encoding is not None:
            write_text_file(path, content, target_encoding)
        else:
            with open(path, "w") as f:
                f.write(content)
        result = f"(wrote {line_count} lines ({char_count} chars) to {path}; action={action}"
        if target_encoding is not None:
            result += f"; format={describe_text_format(target_encoding, target_newline_style)}"
        result += ")"
        return result
    except UnicodeEncodeError as e:
        encoding = target_encoding or "the target file encoding"
        return f"(error writing {path}: content cannot be encoded as {encoding}: {e})"
    except Exception as e:
        return f"(error writing {path}: {e})"
