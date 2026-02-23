"""delegate tool: spawn a subagent to handle a subtask."""

from llm_agent.formatting import bold, cyan, dim

SCHEMA = {
    "name": "delegate",
    "description": (
        "Delegate a task to a specialized subagent that runs independently "
        "and returns its findings. Available agents will be listed here after setup."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": "Name of the agent to delegate to.",
            },
            "task": {
                "type": "string",
                "description": "The task description for the subagent.",
            },
        },
        "required": ["agent", "task"],
    },
}


def log(params):
    from llm_agent.display import get_display
    agent = params.get("agent", "")
    task = params.get("task", "")
    preview = task[:80] + "..." if len(task) > 80 else task
    get_display().tool_log(f"  {bold('delegate')} → {cyan(agent)}: {dim(preview)}")

LOG = log

# Callback set at runtime by cli.py to avoid circular imports.
# Signature: _run_subagent(agent_name, task) -> str
_run_subagent = None


def handle(params):
    agent = params.get("agent", "")
    task = params.get("task", "")

    if not agent or not task:
        return "(error: both 'agent' and 'task' are required)"

    if _run_subagent is None:
        return "(error: delegate is not configured — subagent system not initialised)"

    return _run_subagent(agent, task)
