"""Subagent definitions and execution."""

import json
import os

from llm_agent.formatting import bold, dim, format_tokens, yellow, red


# ---------- Built-in agent definitions ----------

BUILTIN_AGENTS = {
    "explore": {
        "name": "explore",
        "description": "Fast read-only research agent (uses haiku)",
        "model": "haiku",
        "tools": [
            "read_file", "list_directory", "search_files",
            "glob_files", "read_url", "web_search",
        ],
        "system_prompt": (
            "You are a research assistant. Your job is to explore the filesystem, "
            "read files, and search code to answer questions. You have read-only "
            "tools — you cannot modify files or run commands. Be thorough and "
            "report your findings clearly."
        ),
    },
    "code": {
        "name": "code",
        "description": "Full-capability coding agent (inherits parent model)",
        "model": None,  # inherit from parent
        "tools": [
            "read_file", "list_directory", "search_files", "glob_files",
            "read_url", "web_search", "write_file", "edit_file", "run_command",
        ],
        "system_prompt": None,  # inherit from parent
    },
}

# Model alias → full model name (same as cli.py MODELS)
MODEL_ALIASES = {
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5",
    "gemini-flash": "gemini-2.5-flash",
    "gemini-pro": "gemini-3.1-pro-preview",
    "gpt-4o": "gpt-4o",
    "gpt-4o-mini": "gpt-4o-mini",
    "gpt-5.2": "gpt-5.2",
    "o3": "o3",
    "o4-mini": "o4-mini",
}


def _load_custom_agents():
    """Load custom agent definitions from ~/.agents/ and .agents/ directories.

    Project-level (.agents/) takes priority over user-level (~/.agents/).
    """
    agents = {}
    dirs = [
        os.path.expanduser("~/.agents"),
        os.path.join(os.getcwd(), ".agents"),
    ]
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d)):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(d, fname)
            try:
                with open(path) as f:
                    defn = json.load(f)
                name = defn.get("name", fname[:-5])
                # Never allow delegate in subagent tools
                tools = defn.get("tools")
                if tools and "delegate" in tools:
                    tools = [t for t in tools if t != "delegate"]
                    defn["tools"] = tools
                agents[name] = defn
            except (json.JSONDecodeError, OSError) as e:
                print(f"{yellow(f'Warning: skipping {path}: {e}')}")
    return agents


def load_all_agents():
    """Return a dict of all available agents (built-in + custom)."""
    agents = dict(BUILTIN_AGENTS)
    agents.update(_load_custom_agents())
    return agents


def run_subagent(agent_name, task, client, model, auto_approve, thinking_level=None):
    """Run a subagent to completion and return its final text answer."""
    from llm_agent.tools import build_tool_set

    agents = load_all_agents()
    defn = agents.get(agent_name)
    if defn is None:
        available = ", ".join(sorted(agents.keys()))
        return f"(error: unknown agent '{agent_name}'. Available agents: {available})"

    # Resolve model
    sub_model = defn.get("model") or model  # None means inherit
    if sub_model in MODEL_ALIASES:
        sub_model = MODEL_ALIASES[sub_model]

    # Resolve tools
    tool_names = defn.get("tools")
    if tool_names:
        # Always exclude delegate from subagents
        tool_names = [t for t in tool_names if t != "delegate"]
        tools, tool_registry = build_tool_set(tool_names)
    else:
        # Default: all tools except delegate
        tools, tool_registry = build_tool_set(exclude=["delegate"])

    # Resolve system prompt
    system_prompt = defn.get("system_prompt")  # None means inherit parent default

    # Create a separate client if the provider differs
    sub_client = client
    def _provider(m):
        if m.startswith("gemini-"):
            return "gemini"
        if m in ("gpt-4o", "gpt-4o-mini", "gpt-5.2", "o3", "o4-mini", "o3-mini"):
            return "openai"
        return "anthropic"

    if _provider(sub_model) != _provider(model):
        from llm_agent.cli import make_client
        sub_client = make_client(sub_model)

    # Pick the right turn function
    if _provider(sub_model) == "openai":
        from llm_agent.openai_agent import openai_agent_turn
        turn_fn = openai_agent_turn
    elif _provider(sub_model) == "gemini":
        from llm_agent.gemini_agent import gemini_agent_turn
        turn_fn = gemini_agent_turn
    else:
        from llm_agent.agent import agent_turn
        turn_fn = agent_turn

    # Run the agent loop
    messages = [{"role": "user", "content": task}]
    sub_usage = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}
    max_steps = 20
    steps = 0

    print(dim(f"  [{agent_name} subagent starting (model: {sub_model})]"))

    extra_kwargs = {}
    if is_sub_gemini and thinking_level:
        extra_kwargs["thinking_level"] = thinking_level

    try:
        while True:
            messages, done = turn_fn(
                sub_client, sub_model, messages, auto_approve,
                usage_totals=sub_usage,
                tools=tools, tool_registry=tool_registry,
                system_prompt=system_prompt,
                **extra_kwargs,
            )
            if done:
                break
            steps += 1
            if steps >= max_steps:
                print(f"\n{yellow(f'  (subagent hit step limit of {max_steps})')}")
                break
    except KeyboardInterrupt:
        print(f"\n{dim('  (subagent interrupted)')}")
        return "(subagent was interrupted by the user)"

    # Print subagent usage
    cache_info = ""
    if sub_usage.get("cache_read", 0) > 0:
        cache_info = f", {format_tokens(sub_usage['cache_read'])} cached"
    print(dim(
        f"  [{agent_name} subagent done: "
        f"{format_tokens(sub_usage['input'])} in, "
        f"{format_tokens(sub_usage['output'])} out{cache_info}]"
    ))

    # Extract final text answer from the last assistant message
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                texts = [b["text"] for b in content if b.get("type") == "text" and b.get("text")]
                if texts:
                    return "\n".join(texts)
            break

    return "(subagent produced no text output)"
