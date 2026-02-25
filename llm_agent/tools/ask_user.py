"""ask_user tool: ask the user a clarifying question."""

from llm_agent.formatting import bold, dim

SCHEMA = {
    "name": "ask_user",
    "description": (
        "Ask the user a clarifying question when you need more information "
        "to proceed. Supports free-text and multiple-choice questions. "
        "Use when the request is ambiguous, when choosing between approaches, "
        "or when you need a decision from the user."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask the user.",
            },
            "choices": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["label"],
                },
                "description": "Optional choices. If omitted, user types free text.",
            },
        },
        "required": ["question"],
    },
}

NEEDS_SEQUENTIAL = True  # always runs on main thread (needs user input)


def log(params):
    from llm_agent.display import get_display
    question = params.get("question", "")
    preview = question[:80] + "..." if len(question) > 80 else question
    get_display().tool_log(f"  {bold('ask_user')}: {dim(preview)}")

LOG = log


def handle(params):
    from llm_agent.display import get_display
    question = params.get("question", "")
    choices = params.get("choices")
    if not question:
        return "(error: 'question' is required)"
    return get_display().ask_user(question, choices)
