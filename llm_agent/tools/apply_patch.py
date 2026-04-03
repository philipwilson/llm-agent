"""apply_patch tool: structured multi-file edits with a constrained grammar."""

import os

from llm_agent.formatting import bold, cyan, dim, green, red
from llm_agent.tools.base import (
    _resolve,
    confirm_edit,
    count_text_lines,
    describe_text_format,
    detect_newline_style,
    FileObservationStore,
    find_omission_placeholder,
    normalize_newlines,
    read_text_file,
    write_text_file,
)


SCHEMA = {
    "name": "apply_patch",
    "description": (
        "Apply a structured multi-file patch. Use this for larger or multi-file edits "
        "where edit_file would be cumbersome. The patch must use a constrained grammar "
        "with *** Begin Patch / *** End Patch and Add File, Delete File, or Update File blocks."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "patch": {
                "type": "string",
                "description": (
                    "The structured patch to apply. Use a grammar with *** Begin Patch, "
                    "*** Add File, *** Delete File, *** Update File, optional *** Move to, "
                    "and +/-/space change lines."
                ),
            },
        },
        "required": ["patch"],
    },
}


NEEDS_CONFIRM = True
NEEDS_SEQUENTIAL = True

_BEGIN = "*** Begin Patch"
_END = "*** End Patch"
_ADD = "*** Add File: "
_DELETE = "*** Delete File: "
_UPDATE = "*** Update File: "
_MOVE = "*** Move to: "
_EOF = "*** End of File"
_PATCH_GRAMMAR_HINT = (
    "Use *** Begin Patch, then one or more *** Add File: / *** Delete File: / "
    "*** Update File: blocks, and finish with *** End Patch."
)


class PatchError(ValueError):
    """Raised when a structured patch cannot be parsed or applied safely."""


def _trim_excerpt(text, max_chars=80):
    excerpt = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    excerpt = excerpt.replace("\n", "\\n")
    if len(excerpt) <= max_chars:
        return excerpt
    return excerpt[:max_chars - 3] + "..."


def _summarize_hunk_anchor(hunk):
    anchor_lines = [text for prefix, text in hunk if prefix in (" ", "-")]
    if not anchor_lines:
        anchor_lines = [text for _, text in hunk if text]
    if not anchor_lines:
        return None
    return " | ".join(_trim_excerpt(line, 50) for line in anchor_lines[:2])


def _is_operation_header(line):
    return (
        line == _END
        or line.startswith(_ADD)
        or line.startswith(_DELETE)
        or line.startswith(_UPDATE)
    )


def _parse_add_block(path, body_lines):
    eof = False
    content_lines = []
    for line in body_lines:
        if line == _EOF:
            if eof:
                raise PatchError(f"duplicate {_EOF} in add block for {path}")
            eof = True
            continue
        if not line.startswith("+"):
            raise PatchError(f"add block for {path} can only contain '+' lines")
        content_lines.append(line[1:])
    if not content_lines:
        raise PatchError(f"add block for {path} must contain content")
    return {"type": "add", "path": path, "lines": content_lines, "eof": eof}


def _parse_update_block(path, body_lines):
    move_to = None
    hunks = []
    current = []
    eof = False
    saw_change = False

    idx = 0
    if idx < len(body_lines) and body_lines[idx].startswith(_MOVE):
        move_to = body_lines[idx][len(_MOVE):]
        if not move_to:
            raise PatchError(f"move target is required for update block {path}")
        idx += 1

    while idx < len(body_lines):
        line = body_lines[idx]
        idx += 1
        if line.startswith("@@"):
            if current:
                hunks.append(current)
                current = []
            continue
        if line == _EOF:
            if eof:
                raise PatchError(f"duplicate {_EOF} in update block for {path}")
            eof = True
            continue
        if not line or line[0] not in " +-":
            raise PatchError(
                f"invalid change line in update block for {path}: {line!r}; "
                "change lines must start with ' ', '+' or '-'"
            )
        current.append((line[0], line[1:]))
        saw_change = True

    if current:
        hunks.append(current)
    if not saw_change and move_to is None:
        raise PatchError(f"update block for {path} must contain changes")

    return {
        "type": "update",
        "path": path,
        "move_to": move_to,
        "hunks": hunks,
        "eof": eof,
    }


def _parse_patch(patch_text):
    lines = patch_text.splitlines()
    if not lines or lines[0] != _BEGIN:
        raise PatchError(f"patch must start with {_BEGIN}")

    idx = 1
    ops = []
    while idx < len(lines):
        line = lines[idx]
        if line == _END:
            if idx != len(lines) - 1:
                raise PatchError(f"{_END} must be the last line of the patch")
            if not ops:
                raise PatchError("patch contains no operations")
            return ops

        if line.startswith(_ADD):
            op_type = "add"
            path = line[len(_ADD):]
        elif line.startswith(_DELETE):
            op_type = "delete"
            path = line[len(_DELETE):]
        elif line.startswith(_UPDATE):
            op_type = "update"
            path = line[len(_UPDATE):]
        else:
            raise PatchError(f"unexpected patch header: {line}")

        if not path:
            raise PatchError("patch file path cannot be empty")

        idx += 1
        body_lines = []
        while idx < len(lines) and not _is_operation_header(lines[idx]):
            body_lines.append(lines[idx])
            idx += 1

        if op_type == "add":
            ops.append(_parse_add_block(path, body_lines))
        elif op_type == "delete":
            if body_lines:
                raise PatchError(f"delete block for {path} must not contain body lines")
            ops.append({"type": "delete", "path": path})
        else:
            ops.append(_parse_update_block(path, body_lines))

    raise PatchError(f"patch must end with {_END}")


def _split_normalized_lines(text):
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    trailing_newline = normalized.endswith("\n")
    if not normalized:
        return [], False
    if trailing_newline:
        body = normalized[:-1]
        lines = body.split("\n") if body else []
    else:
        lines = normalized.split("\n")
    return lines, trailing_newline


def _join_normalized_lines(lines, trailing_newline):
    if not lines:
        return ""
    text = "\n".join(lines)
    if trailing_newline:
        text += "\n"
    return text


def _find_hunk_position(lines, old_lines, start_index, hunk):
    if not old_lines:
        raise PatchError(
            "update hunk must include at least one context or removed line "
            "to anchor it in the file"
        )

    limit = len(lines) - len(old_lines) + 1
    if limit < 0:
        return None

    matches_from_cursor = [
        idx
        for idx in range(start_index, limit)
        if lines[idx:idx + len(old_lines)] == old_lines
    ]
    if len(matches_from_cursor) == 1:
        return matches_from_cursor[0]
    if len(matches_from_cursor) > 1:
        anchor = _summarize_hunk_anchor(hunk)
        message = "update hunk matches multiple locations"
        if anchor:
            message += f" near '{anchor}'"
        message += "; add more unchanged context lines"
        raise PatchError(message)

    matches = [
        idx for idx in range(0, limit)
        if lines[idx:idx + len(old_lines)] == old_lines
    ]
    if len(matches) > 1:
        anchor = _summarize_hunk_anchor(hunk)
        message = "update hunk matches multiple locations"
        if anchor:
            message += f" near '{anchor}'"
        message += "; add more unchanged context lines"
        raise PatchError(message)
    if len(matches) == 1:
        return matches[0]
    return None


def _apply_update_hunks(content, hunks, eof):
    lines, trailing_newline = _split_normalized_lines(content)
    cursor = 0
    touches_eof = False

    for hunk in hunks:
        old_lines = [text for prefix, text in hunk if prefix in (" ", "-")]
        new_lines = [text for prefix, text in hunk if prefix in (" ", "+")]
        position = _find_hunk_position(lines, old_lines, cursor, hunk)
        if position is None:
            anchor = _summarize_hunk_anchor(hunk)
            message = "update hunk could not be matched in the target file"
            if anchor:
                message += f" near '{anchor}'"
            message += "; re-read the file and regenerate the patch with more unchanged context lines"
            raise PatchError(message)
        end = position + len(old_lines)
        if end == len(lines):
            touches_eof = True
        lines[position:end] = new_lines
        cursor = position + len(new_lines)

    if eof:
        trailing_newline = False
    elif touches_eof:
        trailing_newline = True

    return _join_normalized_lines(lines, trailing_newline)


def _build_add_content(op):
    content = "\n".join(op["lines"])
    if not op["eof"]:
        content += "\n"
    return content


def _build_update_diff_lines(hunks):
    diff_lines = []
    for hunk in hunks:
        for prefix, text in hunk:
            if prefix == " ":
                continue
            marker = green("+ " + text) if prefix == "+" else red("- " + text)
            diff_lines.append(f"  {marker}")
    return diff_lines


def _append_preview_text(preview, lines, max_lines=12):
    if len(lines) <= max_lines:
        preview.extend(lines)
        return
    head = max_lines // 2
    tail = max_lines - head
    preview.extend(lines[:head])
    preview.append(f"  {dim(f'... ({len(lines) - max_lines} more lines) ...')}")
    preview.extend(lines[-tail:])


def _describe_result_format(content, encoding, preferred_newline_style=None):
    newline_style = preferred_newline_style
    if newline_style is None:
        newline_style = detect_newline_style(content)
    return describe_text_format(encoding, newline_style)


def _validate_patch_paths(ops):
    touched = set()
    for op in ops:
        path = _resolve(op["path"])
        if path in touched:
            raise PatchError(f"{path} is modified more than once in one patch")
        touched.add(path)
        op["resolved_path"] = path

        move_to = op.get("move_to")
        if move_to:
            resolved_move = _resolve(move_to)
            if resolved_move in touched:
                raise PatchError(f"{resolved_move} is modified more than once in one patch")
            touched.add(resolved_move)
            op["resolved_move_to"] = resolved_move


def _plan_add(op):
    path = op["resolved_path"]
    if os.path.exists(path):
        raise PatchError(f"{path} already exists")
    content = _build_add_content(op)
    placeholder = find_omission_placeholder(content)
    if placeholder:
        raise PatchError(
            f"content for {path} appears to contain an omission placeholder: {placeholder}"
        )
    line_count = count_text_lines(content)
    char_count = len(content)
    format_desc = _describe_result_format(content, "utf-8")

    preview = [
        f"  {bold('apply_patch')}: add {cyan(path)} ({line_count} lines, {char_count} chars)",
        f"  {dim(f'format: {format_desc}')}",
    ]
    diff_lines = [f"  {green('+ ' + line)}" for line in content.splitlines()]
    _append_preview_text(preview, diff_lines)

    return {
        "action": "add",
        "path": path,
        "content": content,
        "encoding": "utf-8",
        "line_count": line_count,
        "char_count": char_count,
        "format_desc": format_desc,
        "preview": preview,
    }


def _plan_delete(op, context):
    path = op["resolved_path"]
    if not os.path.exists(path):
        raise PatchError(f"{path} does not exist")

    stat_result = os.stat(path)
    observations = (context or {}).get("file_observations")
    if observations is not None:
        freshness_error = observations.validate_fresh(path, stat_result, "delete")
        if freshness_error:
            raise PatchError(freshness_error[1:-1])

    preview = [
        f"  {bold('apply_patch')}: delete {cyan(path)} ({stat_result.st_size} bytes)",
    ]
    try:
        file_info = read_text_file(path)
        line_count = count_text_lines(file_info["content"])
        preview.append(
            f"  {dim(f'format: {describe_text_format(file_info['encoding'], file_info['newline_style'])}, {line_count} lines')}"
        )
    except Exception:
        line_count = None

    return {
        "action": "delete",
        "path": path,
        "snapshot": FileObservationStore.snapshot_stat(stat_result),
        "line_count": line_count,
        "preview": preview,
    }


def _plan_update(op, context):
    path = op["resolved_path"]
    move_to = op.get("resolved_move_to")
    if not os.path.exists(path):
        raise PatchError(f"{path} does not exist")
    if move_to and os.path.exists(move_to):
        raise PatchError(f"move target already exists: {move_to}")

    file_info = read_text_file(path)
    content = file_info["content"]
    stat_result = file_info["stat"]
    encoding = file_info["encoding"]
    newline_style = file_info["newline_style"]
    observations = (context or {}).get("file_observations")
    if observations is not None:
        freshness_error = observations.validate_fresh(path, stat_result, "patch")
        if freshness_error:
            raise PatchError(freshness_error[1:-1])

    for hunk in op["hunks"]:
        added_text = "\n".join(text for prefix, text in hunk if prefix == "+")
        placeholder = find_omission_placeholder(added_text)
        if placeholder:
            raise PatchError(
                f"patch for {path} appears to contain an omission placeholder: {placeholder}"
            )

    if op["hunks"]:
        try:
            normalized_result = _apply_update_hunks(content, op["hunks"], op["eof"])
        except PatchError as e:
            raise PatchError(f"{path}: {e}")
        result_content = normalize_newlines(normalized_result, newline_style)
    else:
        result_content = content

    result_newline_style = newline_style or detect_newline_style(result_content)
    old_line_count = count_text_lines(content)
    new_line_count = count_text_lines(result_content)
    format_desc = describe_text_format(encoding, result_newline_style)

    action = "move" if move_to and not op["hunks"] else "update"
    if move_to and op["hunks"]:
        action = "move+update"

    target_path = move_to or path
    preview = [
        f"  {bold('apply_patch')}: {action} {cyan(path)}"
        + (f" -> {cyan(target_path)}" if move_to else ""),
        f"  {dim(f'summary: {old_line_count} lines -> {new_line_count} lines')}",
        f"  {dim(f'format: {format_desc}')}",
    ]
    if op["hunks"]:
        _append_preview_text(preview, _build_update_diff_lines(op["hunks"]))
    else:
        preview.append(f"  {dim('(move only)')}")

    return {
        "action": action,
        "path": path,
        "move_to": move_to,
        "snapshot": FileObservationStore.snapshot_stat(stat_result),
        "content": result_content,
        "encoding": encoding,
        "old_line_count": old_line_count,
        "new_line_count": new_line_count,
        "format_desc": format_desc,
        "preview": preview,
    }


def _build_preview(plans):
    preview = []
    for index, plan in enumerate(plans):
        if index:
            preview.append("  " + dim("----"))
        preview.extend(plan["preview"])
    return preview


def _build_result(plans):
    counts = {"add": 0, "update": 0, "delete": 0, "move": 0}
    lines = []
    for plan in plans:
        action = plan["action"]
        if action == "add":
            counts["add"] += 1
            lines.append(
                f"add {plan['path']} ({plan['line_count']} lines, {plan['char_count']} chars, format={plan['format_desc']})"
            )
        elif action == "delete":
            counts["delete"] += 1
            suffix = f", {plan['line_count']} lines" if plan["line_count"] is not None else ""
            lines.append(f"delete {plan['path']} ({suffix.lstrip(', ') or 'removed'})")
        else:
            counts["update"] += 1
            if plan.get("move_to"):
                counts["move"] += 1
            detail = f"{plan['path']}"
            if plan.get("move_to"):
                detail += f" -> {plan['move_to']}"
            lines.append(
                f"{action} {detail} ({plan['old_line_count']}->{plan['new_line_count']} lines, format={plan['format_desc']})"
            )

    summary = (
        f"(applied patch: {len(plans)} file(s); "
        f"added={counts['add']}, updated={counts['update']}, "
        f"deleted={counts['delete']}, moved={counts['move']})"
    )
    return summary + ("\n" + "\n".join(lines) if lines else "")


def _revalidate_existing(plan):
    path = plan["path"]
    try:
        current_stat = os.stat(path)
    except FileNotFoundError:
        raise PatchError(f"{path} changed while waiting for confirmation; read it again before patching")
    if not FileObservationStore.matches_snapshot(plan["snapshot"], current_stat):
        raise PatchError(f"{path} changed while waiting for confirmation; read it again before patching")


def _apply_plan(plan):
    action = plan["action"]
    if action == "add":
        if os.path.exists(plan["path"]):
            raise PatchError(f"{plan['path']} was created while waiting for confirmation")
        os.makedirs(os.path.dirname(plan["path"]) or ".", exist_ok=True)
        write_text_file(plan["path"], plan["content"], plan["encoding"])
        return

    if action == "delete":
        _revalidate_existing(plan)
        os.remove(plan["path"])
        return

    _revalidate_existing(plan)
    move_to = plan.get("move_to")
    if move_to and os.path.exists(move_to):
        raise PatchError(f"{move_to} was created while waiting for confirmation")

    if move_to:
        os.makedirs(os.path.dirname(move_to) or ".", exist_ok=True)
        if action == "move":
            os.rename(plan["path"], move_to)
            return
        write_text_file(move_to, plan["content"], plan["encoding"])
        os.remove(plan["path"])
        return

    write_text_file(plan["path"], plan["content"], plan["encoding"])


def handle(params, auto_approve=False, context=None):
    patch_text = params.get("patch", "")
    if not patch_text.strip():
        return "(error: patch is required)"

    try:
        ops = _parse_patch(patch_text)
    except PatchError as e:
        return f"(error: {e}. {_PATCH_GRAMMAR_HINT})"
    except Exception as e:
        return f"(error preparing patch: {e})"

    try:
        _validate_patch_paths(ops)
        plans = []
        for op in ops:
            if op["type"] == "add":
                plans.append(_plan_add(op))
            elif op["type"] == "delete":
                plans.append(_plan_delete(op, context))
            else:
                plans.append(_plan_update(op, context))
    except PatchError as e:
        return f"(error: {e})"
    except Exception as e:
        return f"(error preparing patch: {e})"

    preview = _build_preview(plans)
    if auto_approve:
        from llm_agent.display import get_display
        get_display().auto_approved(preview)
    elif not confirm_edit(preview):
        return "(user declined this patch)"

    try:
        for plan in plans:
            _apply_plan(plan)
        return _build_result(plans)
    except PatchError as e:
        return f"(error: {e})"
    except UnicodeEncodeError as e:
        return f"(error writing patched file: {e})"
    except Exception as e:
        return f"(error applying patch: {e})"
