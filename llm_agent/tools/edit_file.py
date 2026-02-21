"""edit_file tool: targeted find-and-replace in an existing file."""

from llm_agent.formatting import bold, cyan, dim, red, green
from llm_agent.tools.base import _resolve, confirm_edit

SCHEMA = {
    "name": "edit_file",
    "description": (
        "Make a targeted edit to an existing file by replacing an exact string "
        "match with new content. You must provide a string that uniquely "
        "identifies the location to edit. Always requires user confirmation."
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
        },
        "required": ["path", "old_string", "new_string"],
    },
}


NEEDS_CONFIRM = True


def handle(params, auto_approve=False):
    path = _resolve(params.get("path", ""))
    old_string = params.get("old_string", "")
    new_string = params.get("new_string", "")

    try:
        with open(path, "r") as f:
            original = f.read()
    except Exception as e:
        return f"(error reading {path}: {e})"

    count = original.count(old_string)
    if count == 0:
        return f"(error: old_string not found in {path})"
    if count > 1:
        return f"(error: old_string matches {count} locations in {path}, must be unique)"

    preview = [f"  {bold('edit_file')}: {cyan(path)}"]
    for line in old_string.splitlines():
        preview.append(f"  {red('-' + ' ' + line)}")
    for line in new_string.splitlines():
        preview.append(f"  {green('+' + ' ' + line)}")

    if auto_approve:
        for line in preview:
            print(line)
        print(f"  {dim('(auto-approved)')}")
    elif not confirm_edit(preview):
        return "(user declined this edit)"

    updated = original.replace(old_string, new_string, 1)
    try:
        with open(path, "w") as f:
            f.write(updated)
        return f"(edited {path})"
    except Exception as e:
        return f"(error writing {path}: {e})"
