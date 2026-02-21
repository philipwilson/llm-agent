#!/usr/bin/env python3
"""
A toy agent loop that uses Claude to answer questions
by running Unix CLI commands. Supports both the direct Anthropic API
and Google Vertex AI.
"""

import argparse
import atexit
import os
import readline
import sys

import anthropic

from llm_agent import VERSION
from llm_agent.formatting import bold, dim, yellow, format_tokens
from llm_agent.agent import agent_turn
from llm_agent.tools import base

MODELS = {
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5",
}
DEFAULT_MODEL = "sonnet"
HISTORY_FILE = os.path.expanduser("~/.agent_history")
HISTORY_SIZE = 1000
MAX_STEPS = 20
MAX_CONVERSATION_TURNS = 40


def setup_readline():
    try:
        readline.read_history_file(HISTORY_FILE)
    except FileNotFoundError:
        pass
    readline.set_history_length(HISTORY_SIZE)
    atexit.register(readline.write_history_file, HISTORY_FILE)


def make_client():
    """Create an Anthropic client, auto-detecting the backend.

    Uses the direct Anthropic API if ANTHROPIC_API_KEY is set,
    otherwise falls back to Vertex AI (requires ANTHROPIC_VERTEX_PROJECT_ID).
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        return anthropic.Anthropic()

    project_id = os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
    if project_id:
        region = os.environ.get("CLOUD_ML_REGION", "us-east5")
        return anthropic.AnthropicVertex(region=region, project_id=project_id)

    print("Set ANTHROPIC_API_KEY or ANTHROPIC_VERTEX_PROJECT_ID.")
    sys.exit(1)


def run_question(client, model, conversation, user_input, auto_approve=False):
    """Run a single question through the agent loop.

    Returns (updated_conversation, turn_usage) or (None, turn_usage) if cancelled.
    """
    conversation.append({"role": "user", "content": user_input})
    messages = list(conversation)
    turn_usage = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}
    steps = 0

    try:
        while True:
            messages, done = agent_turn(
                client, model, messages, auto_approve, usage_totals=turn_usage
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


def agent_loop(client, model, auto_approve=False):
    mode = "YOLO mode" if auto_approve else "confirm mode"
    print(f"{bold('Agent ready')} {dim(f'(model: {model}, {mode})')}")
    print(dim("Type a question, /clear, /model, /version, or 'quit'.\n"))
    conversation = []
    session_usage = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}

    while True:
        try:
            user_input = input(f"{bold('>')} ").strip()
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
                model = MODELS[parts[1]]
                print(dim(f"(switched to {model})"))
            else:
                print(dim(f"(unknown model '{parts[1]}', available: {', '.join(MODELS.keys())})"))
            continue

        result, turn_usage = run_question(
            client, model, conversation, user_input, auto_approve
        )

        for key in ("input", "output", "cache_read", "cache_create"):
            session_usage[key] += turn_usage[key]
        if turn_usage["input"] > 0 or turn_usage["output"] > 0:
            cache_info = ""
            if turn_usage["cache_read"] > 0:
                cache_info += f", {format_tokens(turn_usage['cache_read'])} cached"
            print(dim(
                f"  [{format_tokens(turn_usage['input'])} in, "
                f"{format_tokens(turn_usage['output'])} out{cache_info} | "
                f"session: {format_tokens(session_usage['input'])} in, "
                f"{format_tokens(session_usage['output'])} out]"
            ))

        if result is None:
            # Cancelled -- don't update conversation history
            continue

        # Keep the conversation history for follow-up questions,
        # but trim old turns to avoid unbounded context growth.
        # Keep the most recent turns, always starting with a user message.
        conversation = result
        if len(conversation) > MAX_CONVERSATION_TURNS:
            conversation = conversation[-MAX_CONVERSATION_TURNS:]
            # Ensure we start with a user message
            while conversation and conversation[0]["role"] != "user":
                conversation.pop(0)


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
    args = parser.parse_args()

    base.COMMAND_TIMEOUT = args.timeout
    model = MODELS[args.model]
    client = make_client()

    if args.c:
        _, turn_usage = run_question(
            client, model, [], args.c, auto_approve=args.yolo
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
        agent_loop(client, model, auto_approve=args.yolo)


if __name__ == "__main__":
    main()
