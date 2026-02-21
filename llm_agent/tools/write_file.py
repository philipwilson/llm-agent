"""write_file tool: create or overwrite a file."""

import os

from llm_agent.formatting import bold, cyan, dim, green
from llm_agent.tools.base import _resolve, confirm_edit

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


def handle(params, auto_approve=False):
    path = _resolve(params.get("path", ""))
    content = params.get("content", "")
    exists = os.path.exists(path)
    action = "overwrite" if exists else "create"
    lines = content.splitlines()

    preview = [f"  {bold('write_file')}: {action} {cyan(path)} ({len(lines)} lines)"]
    # Show a compact preview -- first/last few lines
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
        for line in preview:
            print(line)
        print(f"  {dim('(auto-approved)')}")
    elif not confirm_edit(preview):
        return "(user declined to write this file)"

    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return f"(wrote {len(lines)} lines to {path})"
    except Exception as e:
        return f"(error writing {path}: {e})"
