"""delegate tool: spawn a subagent to handle a subtask."""

from llm_agent.formatting import format_tokens
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
            "model": {
                "type": "string",
                "description": "Optional per-run model override for the delegated subagent.",
            },
            "run_in_background": {
                "type": "boolean",
                "description": "If true, start the delegated subagent in the background and return a task ID immediately. Use check_task to inspect status and results.",
                "default": False,
            },
        },
        "required": ["agent", "task"],
    },
}


def log(params):
    from llm_agent.display import get_display
    agent = params.get("agent", "")
    task = params.get("task", "")
    model = params.get("model", "")
    preview = task[:80] + "..." if len(task) > 80 else task
    model_suffix = f" {dim(f'(model: {model})')}" if model else ""
    get_display().tool_log(
        f"  {bold('delegate')} → {cyan(agent)}{model_suffix}: {dim(preview)}"
    )

LOG = log


def _format_delegate_result(metadata):
    usage = metadata.get("usage", {})
    token_summary = (
        f"{format_tokens(usage.get('input', 0))} in, "
        f"{format_tokens(usage.get('output', 0))} out"
    )
    if usage.get("cache_read", 0):
        token_summary += f", {format_tokens(usage['cache_read'])} cached"

    lines = [
        "[delegated run]",
        f"agent: {metadata.get('agent') or '(unknown)'}",
        f"model: {metadata.get('model') or '(unknown)'}",
        f"status: {metadata.get('status') or '(unknown)'}",
        f"steps: {metadata.get('steps', 0)}",
        f"max_steps: {metadata.get('max_steps') or '(unknown)'}",
        f"duration_seconds: {metadata.get('duration_seconds', 0):.2f}",
        f"usage: {token_summary}",
        "",
        "subagent result:",
        metadata.get("result") or "(subagent produced no text output)",
    ]
    return "\n".join(lines)


def handle(params, context=None):
    agent = params.get("agent", "")
    task = params.get("task", "")
    model_override = params.get("model", "")
    run_in_background = params.get("run_in_background", False)
    if model_override is not None:
        model_override = str(model_override).strip()
    if not model_override:
        model_override = None

    if not agent or not task:
        return "(error: both 'agent' and 'task' are required)"

    run_fn = (context or {}).get("run_subagent")
    if run_fn is None:
        return "(error: delegate is not configured — subagent system not initialised)"

    if run_in_background:
        start_fn = (context or {}).get("start_subagent")
        if start_fn is None:
            return "(error: background delegate is not configured)"
        info = start_fn(
            agent,
            task,
            model_override=model_override,
        )
        if info.get("status") == "error" and info.get("result"):
            return info["result"]
        model_text = info.get("model") or model_override or "(resolving)"
        return (
            f"Background delegated task started: {info['task_id']} "
            f"(agent {info['agent']}, model {model_text})\n"
            "Use check_task to inspect status and final result."
        )

    result = run_fn(
        agent,
        task,
        model_override=model_override,
        return_metadata=True,
    )
    if isinstance(result, dict):
        return _format_delegate_result(result)
    return result
