"""check_task tool: query background task status and output."""

from datetime import datetime

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
            "tail_lines": {
                "type": "integer",
                "description": "When checking a specific task, show only the last N lines of combined output.",
            },
        },
    },
}


def _format_timestamp(ts):
    if ts is None:
        return "n/a"
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _format_duration(seconds):
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, seconds = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m {seconds:02d}s"


def handle(params):
    task_id = params.get("task_id")
    tail_lines = params.get("tail_lines")

    if tail_lines is not None and tail_lines < 1:
        return "(error: tail_lines must be >= 1)"

    if task_id:
        info = shell.get_task(task_id, tail_lines=tail_lines)
        if info is None:
            return f"(unknown task: {task_id})"
        lines = [
            f"Task: {info['task_id']}",
            f"Command: {info['command']}",
            f"PID: {info['pid']}",
            f"Working directory: {info['cwd']}",
            f"Status: {info['status']}",
            f"Started: {_format_timestamp(info['started_at'])}",
            f"Finished: {_format_timestamp(info['finished_at'])}",
            f"Runtime: {_format_duration(info['duration_seconds'])}",
            f"Output lines: {info['output_line_count']}",
        ]
        if info["exit_code"] is not None:
            lines.append(f"Exit code: {info['exit_code']}")
        output = info["output"]
        if output:
            heading = "Output"
            if tail_lines is not None and tail_lines < info["output_line_count"]:
                heading += f" (last {tail_lines} lines)"
            lines.append(f"\n{heading}:\n{truncate(output)}")
        else:
            lines.append("\n(no output yet)")
        return "\n".join(lines)

    tasks = shell.list_tasks()
    if not tasks:
        return "(no background tasks)"
    lines = []
    for t in tasks:
        status_parts = [
            t["status"],
            f"pid {t['pid']}",
            _format_duration(t["duration_seconds"]),
        ]
        if t["exit_code"] is not None:
            status_parts.append(f"exit {t['exit_code']}")
        status = ", ".join(status_parts)
        lines.append(f"  {t['task_id']}: [{status}] {t['command']}")
    return "Background tasks:\n" + "\n".join(lines)
