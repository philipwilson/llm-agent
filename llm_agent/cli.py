#!/usr/bin/env python3
"""
Agent loop that uses LLMs to answer questions by exploring the filesystem
and running Unix commands. Supports Anthropic Claude (direct API and
Vertex AI), Google Gemini, and OpenAI models.
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
from llm_agent.display import get_display
from llm_agent.formatting import bold, dim, format_tokens
from llm_agent.tools import base
from llm_agent.tools.base import _resolve

MODELS = {
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

def set_terminal_title(title):
    """Set the terminal window title via OSC escape sequence."""
    sys.stdout.write(f"\033]0;{title}\007")
    sys.stdout.flush()

def update_terminal_title():
    """Set the terminal title to 'llm-agent — cwd'."""
    set_terminal_title(f"llm-agent — {base.shell.cwd}")

def reset_terminal_title():
    """Reset the terminal title to the default."""
    set_terminal_title("")
MAX_STEPS = 20
# Gemini models tend to make single tool calls per turn rather than batching,
# so they burn through steps faster and need a higher limit.
MAX_STEPS_GEMINI = 50
CONTEXT_WINDOWS = {
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5": 200_000,
    "gemini-2.5-flash": 1_000_000,
    "gemini-3.1-pro-preview": 1_000_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-5.2": 400_000,
    "o3": 200_000,
    "o4-mini": 200_000,
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


def _is_tool_result_message(msg):
    """Check if a message is a tool-result message (not a real user turn).

    Tool results use role "user" but contain tool_result content blocks.
    Trimming between a tool_use and its tool_result would produce an invalid
    message sequence that the API rejects.
    """
    if msg.get("role") != "user":
        return False
    content = msg.get("content")
    if isinstance(content, list):
        return any(b.get("type") == "tool_result" for b in content if isinstance(b, dict))
    return False


def trim_conversation(conversation, last_input_tokens, model, client=None):
    """Trim oldest message rounds to keep token usage within context budget.

    If client is provided, summarizes the dropped messages and prepends the
    summary to the trimmed conversation so the model retains key context.

    Rounds are defined as: a real user message (not a tool_result) followed
    by all subsequent messages until the next real user message.  This ensures
    we never split a tool_use/tool_result pair.
    """
    budget = int(CONTEXT_WINDOWS.get(model, 200_000) * CONTEXT_BUDGET)
    if last_input_tokens <= budget:
        return conversation
    excess = last_input_tokens - budget
    trimmed = list(conversation)
    dropped = []
    while trimmed and excess > 0:
        # Find the end of the current round: advance past the first message,
        # then skip until we hit a real user message (not a tool_result).
        round_end = 1
        while round_end < len(trimmed):
            msg = trimmed[round_end]
            if msg["role"] == "user" and not _is_tool_result_message(msg):
                break
            round_end += 1
        dropped.extend(trimmed[:round_end])
        excess -= estimate_tokens(trimmed[:round_end])
        trimmed = trimmed[round_end:]

    # Summarize dropped messages if we have a client and substantive content
    if client and dropped and estimate_tokens(dropped) > 200:
        summary = _summarize_dropped(client, model, dropped)
        if summary:
            trimmed.insert(0, {
                "role": "user",
                "content": f"[Earlier context summary]\n{summary}",
            })

    return trimmed


def _summarize_dropped(client, model, messages):
    """Ask the model to summarize dropped conversation messages."""
    # Build a text representation of the dropped messages
    text_parts = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, str):
            text_parts.append(f"{role}: {content[:2000]}")
        elif isinstance(content, list):
            for block in content:
                if block.get("type") == "text":
                    text_parts.append(f"{role}: {block['text'][:2000]}")
                elif block.get("type") == "tool_use":
                    text_parts.append(f"{role}: [tool: {block.get('name', '?')}]")
                elif block.get("type") == "tool_result":
                    result_text = str(block.get("content", ""))[:500]
                    text_parts.append(f"{role}: [tool result: {result_text}]")

    dropped_text = "\n".join(text_parts)
    # Cap the input to avoid a huge summarization request
    if len(dropped_text) > 8000:
        dropped_text = dropped_text[:8000] + "\n...(truncated)"

    prompt = (
        "Summarize the key decisions, findings, files discussed, and important "
        "context from this conversation segment in 3-5 concise bullet points. "
        "Focus on information that would be needed to continue the conversation.\n\n"
        f"{dropped_text}"
    )

    try:
        if is_openai_model(model):
            from llm_agent.openai_agent import openai_agent_turn
            msgs, _ = openai_agent_turn(
                client, model,
                [{"role": "user", "content": prompt}],
                auto_approve=True, tools=None, tool_registry={},
            )
        elif is_gemini_model(model):
            from llm_agent.gemini_agent import gemini_agent_turn
            msgs, _ = gemini_agent_turn(
                client, model,
                [{"role": "user", "content": prompt}],
                auto_approve=True, tools=None, tool_registry={},
            )
        else:
            from llm_agent.agent import agent_turn
            msgs, _ = agent_turn(
                client, model,
                [{"role": "user", "content": prompt}],
                auto_approve=True, tools=None, tool_registry={},
                system_prompt="You are a concise summarizer. Output only bullet points.",
            )

        # Extract the summary text from the response
        for msg in reversed(msgs):
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content
                elif isinstance(content, list):
                    texts = [b["text"] for b in content
                             if b.get("type") == "text" and b.get("text")]
                    if texts:
                        return "\n".join(texts)
    except Exception:
        pass
    return None


def is_gemini_model(model):
    return model.startswith("gemini-")


def is_openai_model(model):
    return model in ("gpt-4o", "gpt-4o-mini", "gpt-5.2", "o3", "o4-mini", "o3-mini")


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
            get_display().status(f"  attached: {token} ({size_str})")
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

    For OpenAI models, uses the openai SDK with OPENAI_API_KEY.
    For Gemini models, uses the google-genai SDK with GOOGLE_API_KEY.
    For Anthropic models, uses the direct API if ANTHROPIC_API_KEY is set,
    otherwise falls back to Vertex AI (requires ANTHROPIC_VERTEX_PROJECT_ID).
    """
    if is_openai_model(model):
        try:
            import openai
        except ImportError:
            get_display().error("Install openai: pip install 'llm-agent[openai]'")
            sys.exit(1)
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            get_display().error("Set OPENAI_API_KEY for OpenAI models.")
            sys.exit(1)
        return openai.OpenAI(api_key=api_key)

    if is_gemini_model(model):
        try:
            from google import genai
        except ImportError:
            get_display().error("Install google-genai: pip install 'llm-agent[gemini]'")
            sys.exit(1)
        api_key = (
            os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GOOGLE_GENERATIVE_AI_API_KEY")
        )
        if not api_key:
            get_display().error("Set GOOGLE_API_KEY for Gemini models.")
            sys.exit(1)
        return genai.Client(api_key=api_key)

    if os.environ.get("ANTHROPIC_API_KEY"):
        return anthropic.Anthropic()

    project_id = os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
    if project_id:
        region = os.environ.get("CLOUD_ML_REGION", "us-east5")
        return anthropic.AnthropicVertex(region=region, project_id=project_id)

    get_display().error("Set ANTHROPIC_API_KEY or ANTHROPIC_VERTEX_PROJECT_ID.")
    sys.exit(1)


def agent_loop(session):
    display = get_display()
    mode = "YOLO mode" if session.auto_approve else "confirm mode"
    display.info(f"{bold('Agent ready')} {dim(f'(model: {session.model}, {mode})')}")
    display.status("Type a question, /clear, /mcp, /model, /thinking, /skills, /version, or 'quit'.\n")
    update_terminal_title()

    while True:
        try:
            user_input = input("\001\033[1m\002>\001\033[0m\002 ").strip()
        except (EOFError, KeyboardInterrupt):
            display.info("\nBye.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit"):
            display.info("Bye.")
            break

        result = session.handle_command(user_input)
        if result is not None:
            messages, transformed = result
            for msg in messages:
                display.status(msg)
            if transformed is None:
                continue
            user_input = transformed

        success, turn_usage = session.run_question(user_input)

        if turn_usage["input"] > 0 or turn_usage["output"] > 0:
            cache_info = ""
            if turn_usage["cache_read"] > 0:
                cache_info += f", {format_tokens(turn_usage['cache_read'])} cached"
            context_info = ""
            last_input = turn_usage.get("last_input", 0)
            if last_input > 0:
                window = CONTEXT_WINDOWS.get(session.model, 200_000)
                remaining_pct = max(0, (window - last_input) / window * 100)
                context_info = f" | context: {remaining_pct:.0f}% remaining"
            display.status(
                f"  [{format_tokens(turn_usage['input'])} in, "
                f"{format_tokens(turn_usage['output'])} out{cache_info} | "
                f"session: {format_tokens(session.session_usage['input'])} in, "
                f"{format_tokens(session.session_usage['output'])} out{context_info}]"
            )

        if turn_usage.get("trimmed", 0) > 0:
            display.status(f"  (trimmed {turn_usage['trimmed']} old messages to fit context window)")
        update_terminal_title()

    reset_terminal_title()


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
    parser.add_argument(
        "--no-tui",
        action="store_true",
        help="Use readline REPL instead of Textual TUI in interactive mode",
    )
    args = parser.parse_args()

    base.COMMAND_TIMEOUT = args.timeout
    model = MODELS[args.model]
    thinking = args.thinking if args.thinking else DEFAULT_THINKING.get(model)
    client = make_client(model)

    from llm_agent.session import Session
    session = Session(client, model, auto_approve=args.yolo, thinking_level=thinking)

    # Initialize MCP servers (if configured)
    mcp_manager = None
    try:
        from llm_agent.mcp_client import load_mcp_config, get_mcp_manager
        config = load_mcp_config()
        if config:
            mcp_manager = get_mcp_manager()
            mcp_manager.start(config)
    except ImportError:
        pass  # mcp package not installed
    except Exception as e:
        get_display().error(f"MCP initialization failed: {e}")

    def _stop_mcp():
        if mcp_manager:
            mcp_manager.stop()

    if args.c:
        try:
            success, turn_usage = session.run_question(args.c)
            if turn_usage["input"] > 0 or turn_usage["output"] > 0:
                cache_info = ""
                if turn_usage["cache_read"] > 0:
                    cache_info += f", {format_tokens(turn_usage['cache_read'])} cached"
                get_display().info_stderr(dim(
                    f"  [{format_tokens(turn_usage['input'])} in, "
                    f"{format_tokens(turn_usage['output'])} out{cache_info}]"
                ))
        finally:
            _stop_mcp()
    else:
        use_tui = not args.no_tui
        try:
            if use_tui:
                try:
                    from llm_agent.tui import run_tui
                    run_tui(session)
                except ImportError:
                    # textual not installed, fall back to readline
                    setup_readline()
                    agent_loop(session)
            else:
                setup_readline()
                agent_loop(session)
        finally:
            _stop_mcp()


if __name__ == "__main__":
    main()
