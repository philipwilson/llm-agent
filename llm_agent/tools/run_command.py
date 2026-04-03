"""run_command tool: arbitrary shell command execution."""

import re

from llm_agent.formatting import bold, dim, yellow
from llm_agent.tools.base import shell

SCHEMA = {
    "name": "run_command",
    "description": (
        "Run an arbitrary shell command and return its stdout and stderr. "
        "Use this for anything the dedicated tools don't cover: "
        "pipelines, awk, curl, system inspection, etc."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute.",
            },
            "description": {
                "type": "string",
                "description": "A brief explanation of why you are running this command.",
            },
            "run_in_background": {
                "type": "boolean",
                "description": "If true, start the command in the background and return a task ID immediately. Use check_task to poll for results.",
                "default": False,
            },
        },
        "required": ["command"],
    },
}

NEEDS_CONFIRM = True

_SHELL_OPERATORS = re.compile(r"(&&|\|\||\||;)")

DANGEROUS_COMMANDS = frozenset({
    "rm", "rmdir", "mkfs", "dd", "mv", "chmod", "chown",
    "kill", "killall", "pkill",
    "shutdown", "reboot", "halt", "sudo",
})

DANGEROUS_PIPE_TARGETS = frozenset({
    "sh", "bash", "zsh", "fish", "dash",
    "python", "python3", "perl", "ruby", "node",
})

DANGEROUS_SUBSTRINGS = ("> /dev/",)


def is_dangerous(command):
    """Check whether a (possibly compound) command is dangerous.

    Splits on shell operators (&&, ||, |, ;) and checks each sub-command:
    - First word matches a known destructive/privileged command
    - Pipe target is a shell or interpreter (e.g. curl ... | sh)
    - Sub-command contains a dangerous substring (e.g. > /dev/...)
    """
    tokens = _SHELL_OPERATORS.split(command.strip())
    for i, token in enumerate(tokens):
        if i % 2 == 1:  # skip operator tokens
            continue
        part = token.strip()
        if not part:
            continue
        words = part.split()
        if not words:
            continue
        cmd = words[0]
        if cmd in DANGEROUS_COMMANDS:
            return True
        if i > 0 and tokens[i - 1] == "|" and cmd in DANGEROUS_PIPE_TARGETS:
            return True
        if any(s in part for s in DANGEROUS_SUBSTRINGS):
            return True
    return False


def confirm(command, description=None, auto_approve=False):
    from llm_agent.display import get_display
    display = get_display()
    preview = []
    if description:
        preview.append(f"  {dim('#')} {dim(description)}")
    preview.append(f"  {bold('$')} {bold(command)}")
    if auto_approve and not is_dangerous(command):
        display.auto_approved(preview)
        return True
    if auto_approve and is_dangerous(command):
        preview.append(f"  {yellow('⚠ dangerous command, requires confirmation')}")
    return display.confirm(preview, "Run? [Y/n]")


def handle(params, auto_approve=False):
    command = params.get("command", "")
    description = params.get("description")
    background = params.get("run_in_background", False)
    if confirm(command, description, auto_approve):
        if background:
            task_id = shell.start_background(command)
            info = shell.get_task(task_id)
            return (
                f"Background task started: {task_id} "
                f"(pid {info['pid']}, cwd {info['cwd']})\n"
                "Use check_task to inspect status and recent output."
            )
        return shell.run(command)
    return "(user declined to run this command)"
