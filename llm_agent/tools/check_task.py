"""check_task tool: query background task status and output."""

from llm_agent.formatting import truncate
from llm_agent.tools.base import shell

SCHEMA = {
    "name": "check_task",
    "description": (
        "Check on background tasks started with run_command's run_in_background option. "
        "Pass a task_id to get status and output for a specific task, "
        "or omit it to list all background tasks."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The task ID to check (e.g. 'bg-1'). If omitted, lists all tasks.",
            },
        },
    },
}


def handle(params):
    task_id = params.get("task_id")

    if task_id:
        info = shell.get_task(task_id)
        if info is None:
            return f"(unknown task: {task_id})"
        lines = [
            f"Task: {info['task_id']}",
            f"Command: {info['command']}",
            f"Status: {info['status']}",
        ]
        if info["exit_code"] is not None:
            lines.append(f"Exit code: {info['exit_code']}")
        output = info["output"]
        if output:
            lines.append(f"\nOutput:\n{truncate(output)}")
        else:
            lines.append("\n(no output yet)")
        return "\n".join(lines)

    # List all tasks
    tasks = shell.list_tasks()
    if not tasks:
        return "(no background tasks)"
    lines = []
    for t in tasks:
        status = t["status"]
        if t["exit_code"] is not None:
            status += f" (exit {t['exit_code']})"
        lines.append(f"  {t['task_id']}: [{status}] {t['command']}")
    return "Background tasks:\n" + "\n".join(lines)
