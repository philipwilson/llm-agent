"""check_task tool: query background task status and output."""

from datetime import datetime

from llm_agent.formatting import format_tokens, truncate
from llm_agent.tools.base import shell

SCHEMA = {
    "name": "check_task",
    "description": (
        "Check on background tasks started with run_command's run_in_background option "
        "or delegate's run_in_background option. Pass a task_id to get status and "
        "output for a specific task, or omit it to list all background tasks."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": (
                    "The task ID to check (for example 'bg-1' or 'sub-1'). "
                    "If omitted, lists all background tasks."
                ),
            },
            "tail_lines": {
                "type": "integer",
                "description": (
                    "When checking a specific shell task, show only the last N lines "
                    "of combined output."
                ),
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


def _format_usage(usage):
    if not usage:
        return "0 in, 0 out"
    parts = [
        f"{format_tokens(usage.get('input', 0))} in",
        f"{format_tokens(usage.get('output', 0))} out",
    ]
    if usage.get("cache_read", 0):
        parts.append(f"{format_tokens(usage['cache_read'])} cached")
    return ", ".join(parts)


def _get_subagent_store(context):
    if not context:
        return None
    return context.get("subagent_tasks")


def _lookup_task(task_id, context, tail_lines=None):
    info = shell.get_task(task_id, tail_lines=tail_lines)
    if info is not None:
        info["type"] = "shell"
        return info
    store = _get_subagent_store(context)
    if store is not None:
        return store.get_task(task_id)
    return None


def _list_all_tasks(context):
    tasks = []
    for task in shell.list_tasks():
        task["type"] = "shell"
        tasks.append(task)
    store = _get_subagent_store(context)
    if store is not None:
        tasks.extend(store.list_tasks())
    return tasks


def _format_shell_task(info, tail_lines):
    lines = [
        f"Task: {info['task_id']}",
        "Type: shell",
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


def _format_delegate_task(info):
    lines = [
        f"Task: {info['task_id']}",
        "Type: delegated subagent",
        f"Agent: {info['agent']}",
        f"Model: {info.get('model') or '(resolving)'}",
        f"Status: {info['status']}",
        f"Started: {_format_timestamp(info['started_at'])}",
        f"Finished: {_format_timestamp(info['finished_at'])}",
        f"Runtime: {_format_duration(info['duration_seconds'])}",
        f"Steps: {info.get('steps', 0)}",
        f"Usage: {_format_usage(info.get('usage'))}",
        f"Delegated task: {info['task']}",
    ]
    result = info.get("result", "")
    if result:
        lines.append(f"\nResult:\n{truncate(result)}")
    else:
        lines.append("\n(no result yet)")
    return "\n".join(lines)


def handle(params, context=None):
    task_id = params.get("task_id")
    tail_lines = params.get("tail_lines")

    if tail_lines is not None and tail_lines < 1:
        return "(error: tail_lines must be >= 1)"

    if task_id:
        info = _lookup_task(task_id, context, tail_lines=tail_lines)
        if info is None:
            return f"(unknown task: {task_id})"
        if info.get("type") == "delegate":
            return _format_delegate_task(info)
        return _format_shell_task(info, tail_lines)

    tasks = _list_all_tasks(context)
    if not tasks:
        return "(no background tasks)"

    lines = []
    for task in tasks:
        if task.get("type") == "delegate":
            status_parts = [
                "delegate",
                task["status"],
                f"model {task.get('model') or '(resolving)'}",
                _format_duration(task["duration_seconds"]),
            ]
            lines.append(
                f"  {task['task_id']}: [{', '.join(status_parts)}] "
                f"{task['agent']}: {task['task']}"
            )
            continue

        status_parts = [
            task["status"],
            f"pid {task['pid']}",
            _format_duration(task["duration_seconds"]),
        ]
        if task["exit_code"] is not None:
            status_parts.append(f"exit {task['exit_code']}")
        lines.append(
            f"  {task['task_id']}: [{', '.join(status_parts)}] {task['command']}"
        )
    return "Background tasks:\n" + "\n".join(lines)
