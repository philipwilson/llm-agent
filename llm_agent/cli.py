#!/usr/bin/env python3
"""
A toy agent loop that uses Claude to answer questions
by running Unix CLI commands. Supports both the direct Anthropic API
and Google Vertex AI.
"""

import argparse
import atexit
import base64
import os
import re
import readline
import sys

import anthropic

from llm_agent import VERSION
from llm_agent.formatting import bold, dim, red, yellow, format_tokens
from llm_agent.agent import agent_turn, invalidate_tool_cache
from llm_agent.tools import base
from llm_agent.tools.base import _resolve

MODELS = {
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5",
    "gemini-flash": "gemini-2.5-flash",
    "gemini-pro": "gemini-3.1-pro-preview",
}
DEFAULT_MODEL = "sonnet"
DEFAULT_THINKING = {
    "gemini-3.1-pro-preview": "high",
}
ATTACHMENT_TYPES = {
    ".png":  ("image/png",  "image"),
    ".jpg":  ("image/jpeg", "image"),
    ".jpeg": ("image/jpeg", "image"),
    ".gif":  ("image/gif",  "image"),
    ".webp": ("image/webp", "image"),
    ".pdf":  ("application/pdf", "document"),
}
HISTORY_FILE = os.path.expanduser("~/.agent_history")
HISTORY_SIZE = 1000
MAX_STEPS = 20
CONTEXT_WINDOWS = {
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5": 200_000,
    "gemini-2.5-flash": 1_000_000,
    "gemini-3.1-pro-preview": 1_000_000,
}
CONTEXT_BUDGET = 0.80


def estimate_tokens(messages):
    """Estimate the token count of a list of messages using chars/4 heuristic."""
    total_chars = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for block in content:
                total_chars += len(str(block.get("text", "")))
                total_chars += len(str(block.get("content", "")))
    return total_chars // 4


def trim_conversation(conversation, last_input_tokens, model):
    """Trim oldest message rounds to keep token usage within context budget."""
    budget = int(CONTEXT_WINDOWS.get(model, 200_000) * CONTEXT_BUDGET)
    if last_input_tokens <= budget:
        return conversation
    excess = last_input_tokens - budget
    trimmed = list(conversation)
    while trimmed and excess > 0:
        round_end = 1
        while round_end < len(trimmed) and trimmed[round_end]["role"] != "user":
            round_end += 1
        excess -= estimate_tokens(trimmed[:round_end])
        trimmed = trimmed[round_end:]
    return trimmed


def is_gemini_model(model):
    return model.startswith("gemini-")


def parse_attachments(text):
    """Parse @filepath tokens from user input and build multimodal content blocks.

    Returns (cleaned_text, attachment_blocks, error_message).
    attachment_blocks is a list of Anthropic-format image/document source blocks.
    error_message is set if a recognized extension points to a missing file, or
    a file exists but has an unsupported extension.
    """
    blocks = []
    tokens_to_remove = []

    for match in re.finditer(r"(?<!\S)@(\S+)", text):
        token = match.group(1)
        path = _resolve(token)
        ext = os.path.splitext(token)[1].lower()

        if ext in ATTACHMENT_TYPES:
            if not os.path.isfile(path):
                return text, [], f"File not found: {token}"
            media_type, block_type = ATTACHMENT_TYPES[ext]
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")
            size = os.path.getsize(path)
            if size >= 1_000_000:
                size_str = f"{size / 1_000_000:.1f} MB"
            elif size >= 1_000:
                size_str = f"{size / 1_000:.1f} KB"
            else:
                size_str = f"{size} B"
            print(dim(f"  attached: {token} ({size_str})"))
            blocks.append({
                "type": block_type,
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": data,
                },
            })
            tokens_to_remove.append(match)
        elif os.path.isfile(path):
            # File exists but unsupported extension
            return text, [], f"Unsupported file type: {ext} ({token})"
        # else: not a file reference, leave as literal text (e.g. @username)

    if not blocks:
        return text, [], None

    # Remove matched @tokens from text (process in reverse to preserve offsets)
    cleaned = text
    for match in reversed(tokens_to_remove):
        cleaned = cleaned[:match.start()] + cleaned[match.end():]
    cleaned = cleaned.strip()
    if not cleaned:
        cleaned = "Describe this."

    return cleaned, blocks, None


def setup_readline():
    try:
        readline.read_history_file(HISTORY_FILE)
    except FileNotFoundError:
        pass
    readline.set_history_length(HISTORY_SIZE)
    atexit.register(readline.write_history_file, HISTORY_FILE)


def make_client(model):
    """Create an API client for the given model.

    For Gemini models, uses the google-genai SDK with GOOGLE_API_KEY.
    For Anthropic models, uses the direct API if ANTHROPIC_API_KEY is set,
    otherwise falls back to Vertex AI (requires ANTHROPIC_VERTEX_PROJECT_ID).
    """
    if is_gemini_model(model):
        try:
            from google import genai
        except ImportError:
            print("Install google-genai: pip install 'llm-agent[gemini]'")
            sys.exit(1)
        api_key = (
            os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GOOGLE_GENERATIVE_AI_API_KEY")
        )
        if not api_key:
            print("Set GOOGLE_API_KEY for Gemini models.")
            sys.exit(1)
        return genai.Client(api_key=api_key)

    if os.environ.get("ANTHROPIC_API_KEY"):
        return anthropic.Anthropic()

    project_id = os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
    if project_id:
        region = os.environ.get("CLOUD_ML_REGION", "us-east5")
        return anthropic.AnthropicVertex(region=region, project_id=project_id)

    print("Set ANTHROPIC_API_KEY or ANTHROPIC_VERTEX_PROJECT_ID.")
    sys.exit(1)


def run_question(client, model, conversation, user_input, auto_approve=False,
                 thinking_level=None):
    """Run a single question through the agent loop.

    Returns (updated_conversation, turn_usage) or (None, turn_usage) if cancelled.
    """
    turn_usage = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}

    text, attachment_blocks, error = parse_attachments(user_input)
    if error:
        print(red(error))
        return None, turn_usage

    if attachment_blocks:
        content = attachment_blocks + [{"type": "text", "text": text}]
    else:
        content = user_input

    conversation.append({"role": "user", "content": content})
    messages = list(conversation)
    steps = 0

    if is_gemini_model(model):
        from llm_agent.gemini_agent import gemini_agent_turn
        turn_fn = gemini_agent_turn
    else:
        turn_fn = agent_turn

    extra_kwargs = {}
    if is_gemini_model(model) and thinking_level:
        extra_kwargs["thinking_level"] = thinking_level

    try:
        while True:
            messages, done = turn_fn(
                client, model, messages, auto_approve, usage_totals=turn_usage,
                **extra_kwargs
            )
            if done:
                break
            steps += 1
            if steps >= MAX_STEPS:
                print(f"\n{yellow(f'(hit step limit of {MAX_STEPS}, stopping)')}")
                break
    except KeyboardInterrupt:
        print(f"\n{dim('(interrupted)')}")
        return None, turn_usage

    return messages, turn_usage


def agent_loop(client, model, auto_approve=False, thinking_level=None):
    mode = "YOLO mode" if auto_approve else "confirm mode"
    print(f"{bold('Agent ready')} {dim(f'(model: {model}, {mode})')}")
    print(dim("Type a question, /clear, /model, /thinking, /version, or 'quit'.\n"))
    conversation = []
    session_usage = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}

    while True:
        try:
            user_input = input("\001\033[1m\002>\001\033[0m\002 ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit"):
            print("Bye.")
            break
        if user_input.strip() == "/clear":
            conversation = []
            session_usage = {"input": 0, "output": 0}
            print(dim("(conversation cleared)"))
            continue
        if user_input.strip() == "/version":
            print(dim(f"llm-agent v{VERSION} (model: {model})"))
            continue
        if user_input.strip().startswith("/model"):
            parts = user_input.strip().split()
            if len(parts) == 1:
                print(dim(f"(model: {model})"))
                print(dim(f"  available: {', '.join(MODELS.keys())}"))
            elif parts[1] in MODELS:
                new_model = MODELS[parts[1]]
                if is_gemini_model(new_model) != is_gemini_model(model):
                    client = make_client(new_model)
                    conversation = []
                    print(dim(f"(switched to {new_model}, conversation cleared)"))
                else:
                    print(dim(f"(switched to {new_model})"))
                model = new_model
                # Apply per-model thinking default unless user has explicitly set one
                default_thinking = DEFAULT_THINKING.get(new_model)
                if default_thinking and not thinking_level:
                    thinking_level = default_thinking
                    print(dim(f"(thinking: {thinking_level})"))
                setup_delegate(client, model, auto_approve, thinking_level)
            else:
                print(dim(f"(unknown model '{parts[1]}', available: {', '.join(MODELS.keys())})"))
            continue
        if user_input.strip().startswith("/thinking"):
            parts = user_input.strip().split()
            if len(parts) == 1:
                level = thinking_level or "off (model default)"
                print(dim(f"(thinking: {level})"))
            elif parts[1] == "off":
                thinking_level = None
                print(dim("(thinking: off, model decides)"))
            elif parts[1] in ("low", "medium", "high"):
                if not is_gemini_model(model):
                    print(dim("(warning: --thinking is only supported for Gemini models)"))
                thinking_level = parts[1]
                print(dim(f"(thinking: {thinking_level})"))
            else:
                print(dim(f"(unknown thinking level '{parts[1]}', use low/medium/high/off)"))
            continue

        result, turn_usage = run_question(
            client, model, conversation, user_input, auto_approve,
            thinking_level=thinking_level
        )

        for key in ("input", "output", "cache_read", "cache_create"):
            session_usage[key] += turn_usage[key]
        if turn_usage["input"] > 0 or turn_usage["output"] > 0:
            cache_info = ""
            if turn_usage["cache_read"] > 0:
                cache_info += f", {format_tokens(turn_usage['cache_read'])} cached"
            context_info = ""
            last_input = turn_usage.get("last_input", 0)
            if last_input > 0:
                window = CONTEXT_WINDOWS.get(model, 200_000)
                remaining_pct = max(0, (window - last_input) / window * 100)
                context_info = f" | context: {remaining_pct:.0f}% remaining"
            print(dim(
                f"  [{format_tokens(turn_usage['input'])} in, "
                f"{format_tokens(turn_usage['output'])} out{cache_info} | "
                f"session: {format_tokens(session_usage['input'])} in, "
                f"{format_tokens(session_usage['output'])} out{context_info}]"
            ))

        if result is None:
            # Cancelled -- don't update conversation history
            continue

        # Keep the conversation history for follow-up questions,
        # but trim old turns to avoid exceeding the model's context window.
        conversation = result
        last_input = turn_usage.get("last_input", 0)
        old_len = len(conversation)
        conversation = trim_conversation(conversation, last_input, model)
        if len(conversation) < old_len:
            removed = old_len - len(conversation)
            print(dim(f"  (trimmed {removed} old messages to fit context window)"))


def setup_delegate(client, model, auto_approve, thinking_level):
    """Configure the delegate tool with available agents and a run callback."""
    from llm_agent.agents import load_all_agents, run_subagent
    from llm_agent.tools import delegate

    agents = load_all_agents()

    # Update the delegate tool description with available agents
    agent_lines = []
    for name, defn in sorted(agents.items()):
        desc = defn.get("description", "")
        agent_model = defn.get("model") or "(inherits parent)"
        agent_lines.append(f"  - {name}: {desc} [model: {agent_model}]")
    agent_list = "\n".join(agent_lines)

    delegate.SCHEMA["description"] = (
        "Delegate a task to a specialized subagent that runs independently "
        "and returns its findings. Use 'explore' for fast read-only research. "
        "Use 'code' for tasks that need file writes or commands.\n\n"
        f"Available agents:\n{agent_list}"
    )

    # Invalidate cached tools so the updated description is picked up
    invalidate_tool_cache()

    # Set up the callback closure
    def _callback(agent_name, task):
        return run_subagent(
            agent_name, task, client, model, auto_approve,
            thinking_level=thinking_level,
        )

    delegate._run_subagent = _callback


def main():
    parser = argparse.ArgumentParser(description="Unix CLI agent powered by Claude")
    parser.add_argument(
        "-m", "--model",
        choices=list(MODELS.keys()),
        default=DEFAULT_MODEL,
        help=f"Model to use (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "-y", "--yolo",
        action="store_true",
        help="Auto-approve commands (dangerous commands still require confirmation)",
    )
    parser.add_argument(
        "-c",
        metavar="QUESTION",
        help="Run a single question and exit (non-interactive mode)",
    )
    parser.add_argument(
        "-t", "--timeout",
        type=int,
        default=base.DEFAULT_COMMAND_TIMEOUT,
        metavar="SECONDS",
        help=f"Command timeout in seconds (default: {base.DEFAULT_COMMAND_TIMEOUT})",
    )
    parser.add_argument(
        "--thinking",
        choices=["low", "medium", "high"],
        default=None,
        help="Thinking level for Gemini models (default: high for gemini-pro, off for others)",
    )
    args = parser.parse_args()

    base.COMMAND_TIMEOUT = args.timeout
    model = MODELS[args.model]
    thinking = args.thinking if args.thinking else DEFAULT_THINKING.get(model)
    client = make_client(model)
    setup_delegate(client, model, auto_approve=args.yolo, thinking_level=thinking)

    if args.c:
        _, turn_usage = run_question(
            client, model, [], args.c, auto_approve=args.yolo,
            thinking_level=thinking
        )
        if turn_usage["input"] > 0 or turn_usage["output"] > 0:
            cache_info = ""
            if turn_usage["cache_read"] > 0:
                cache_info += f", {format_tokens(turn_usage['cache_read'])} cached"
            print(dim(
                f"  [{format_tokens(turn_usage['input'])} in, "
                f"{format_tokens(turn_usage['output'])} out{cache_info}]"
            ), file=sys.stderr)
    else:
        setup_readline()
        agent_loop(client, model, auto_approve=args.yolo, thinking_level=thinking)


if __name__ == "__main__":
    main()
