"""start_session tool: launch a PTY-backed interactive command."""

from llm_agent.formatting import bold, dim, yellow
from llm_agent.tools.base import shell
from llm_agent.tools.run_command import is_dangerous

SCHEMA = {
    "name": "start_session",
    "description": (
        "Start an interactive PTY-backed command and return a session ID. "
        "Use this when a command needs multiple stdin writes or keeps state "
        "between inputs, such as REPLs, database shells, or watch processes."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The interactive shell command to start.",
            },
            "description": {
                "type": "string",
                "description": "A brief explanation of why you are starting this interactive session.",
            },
            "wait_ms": {
                "type": "integer",
                "description": "How long to wait for initial output before returning.",
                "default": 200,
            },
            "max_output_chars": {
                "type": "integer",
                "description": "Maximum number of initial output characters to return.",
                "default": 12000,
            },
        },
        "required": ["command"],
    },
}

NEEDS_CONFIRM = True
NEEDS_SEQUENTIAL = True


def confirm(command, description=None):
    from llm_agent.display import get_display

    display = get_display()
    preview = []
    if description:
        preview.append(f"  {dim('#')} {dim(description)}")
    preview.append(f"  {bold('$')} {bold(command)}")
    if is_dangerous(command):
        preview.append(f"  {yellow('WARNING: dangerous command, requires confirmation')}")
    return display.confirm(preview, "Start interactive session? [Y/n]")


def handle(params, auto_approve=False):
    del auto_approve  # Interactive sessions always require explicit approval.

    command = params.get("command", "").strip()
    description = params.get("description")
    wait_ms = params.get("wait_ms", 200)
    max_output_chars = params.get("max_output_chars", 12000)

    if not command:
        return "(error: command is required)"
    if wait_ms is not None and wait_ms < 0:
        return "(error: wait_ms must be >= 0)"
    if max_output_chars is not None and max_output_chars < 1:
        return "(error: max_output_chars must be >= 1)"
    if not confirm(command, description):
        return "(user declined to start interactive session)"

    try:
        session_id = shell.start_session(command)
    except Exception as e:
        return f"(error starting interactive session: {e})"

    info = shell.write_session(
        session_id,
        wait_ms=wait_ms,
        max_output_chars=max_output_chars,
    )
    lines = [
        f"Interactive session started: {session_id}",
        f"Command: {info['command']}",
        f"PID: {info['pid']}",
        f"Working directory: {info['cwd']}",
        "Use write_stdin with this session_id to send input, poll for new output, or close the session.",
        "Interactive sessions keep their own shell state; changing directories inside one does not update the agent's global working directory.",
    ]
    output = info["output"]
    if output:
        lines.append(f"\nInitial output:\n{output}")
    else:
        lines.append("\n(no initial output)")
    return "\n".join(lines)
