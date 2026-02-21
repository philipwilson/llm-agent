"""run_command tool: arbitrary shell command execution."""

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
        },
        "required": ["command"],
    },
}

NEEDS_CONFIRM = True

DANGEROUS_PATTERNS = [
    "rm ", "rm\t", "rmdir", "mkfs", "dd ", "dd\t",
    "> /dev/", "mv ", "mv\t", "chmod", "chown",
    "kill ", "killall", "pkill",
    "shutdown", "reboot", "halt",
    "curl|", "wget|",  # piping downloaded content to shell
    "curl |", "wget |",
]


def is_dangerous(command):
    cmd = command.strip()
    return any(pat in cmd for pat in DANGEROUS_PATTERNS)


def confirm(command, description=None, auto_approve=False):
    if description:
        print(f"  {dim('#')} {dim(description)}")
    print(f"  {bold('$')} {bold(command)}")
    if auto_approve and not is_dangerous(command):
        print(f"  {dim('(auto-approved)')}")
        return True
    if auto_approve and is_dangerous(command):
        print(f"  {yellow('⚠ dangerous command, requires confirmation')}")
    answer = input(f"  {dim('Run? [Y/n]')} ").strip().lower()
    return answer in ("", "y", "yes")


def handle(params, auto_approve=False):
    command = params.get("command", "")
    description = params.get("description")
    if confirm(command, description, auto_approve):
        return shell.run(command)
    return "(user declined to run this command)"
