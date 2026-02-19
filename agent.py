#!/usr/bin/env python3
"""
A toy agent loop that uses Claude (via Vertex AI) to answer questions
by running Unix CLI commands.
"""

import argparse
import atexit
import fnmatch
import json
import os
import readline
import subprocess
import sys

from anthropic import AnthropicVertex


# --- Colour helpers (ANSI) ---

def _supports_color():
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

USE_COLOR = _supports_color()

def _ansi(code):
    def wrap(text):
        if not USE_COLOR:
            return text
        return f"\033[{code}m{text}\033[0m"
    return wrap

bold    = _ansi("1")
dim     = _ansi("2")
red     = _ansi("31")
green   = _ansi("32")
yellow  = _ansi("33")
cyan    = _ansi("36")

MODELS = {
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-6",
}
DEFAULT_MODEL = "sonnet"
HISTORY_FILE = os.path.expanduser("~/.agent_history")
HISTORY_SIZE = 1000
MAX_OUTPUT_LINES = 200
COMMAND_TIMEOUT = 30
MAX_STEPS = 20
DANGEROUS_PATTERNS = [
    "rm ", "rm\t", "rmdir", "mkfs", "dd ", "dd\t",
    "> /dev/", "mv ", "mv\t", "chmod", "chown",
    "kill ", "killall", "pkill",
    "shutdown", "reboot", "halt",
    "curl|", "wget|",  # piping downloaded content to shell
    "curl |", "wget |",
]

SYSTEM_PROMPT = """\
You are a Unix CLI assistant. You help users by running shell commands to \
gather information and analyze it.

You have these tools:
- read_file: Read file contents with optional line range. Prefer this over \
cat/head/tail via run_command.
- list_directory: List directory contents. Prefer this over ls via run_command.
- search_files: Search file contents with regex. Prefer this over grep/rg via \
run_command.
- write_file: Create or overwrite a file. Use for creating new files.
- edit_file: Make a targeted find-and-replace edit in an existing file. The \
old_string must match exactly and uniquely. Prefer this over write_file when \
modifying existing files — it's safer and shows a clearer diff.
- run_command: Run arbitrary shell commands. Use this for anything the other \
tools don't cover (pipelines, awk, curl, system inspection, etc.).

Guidelines:
- Prefer the dedicated tools over run_command when they fit the task.
- Prefer read-only, non-destructive commands.
- Analyze results before deciding what to do next.
- When you have enough information, give a clear, concise answer in plain text.
- If something fails, read the error and try a different approach.
- Do not guess at file contents or system state — use tools to check.
"""

TOOLS = [
    {
        "name": "read_file",
        "description": (
            "Read the contents of a file with line numbers. "
            "Returns the total line count and file size so you can decide "
            "whether to read more. Prefer this over cat/head/tail via run_command."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to read.",
                },
                "offset": {
                    "type": "integer",
                    "description": "Starting line number, 1-based (default: 1).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to return (default: 200).",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_directory",
        "description": (
            "List the contents of a directory with file types and sizes. "
            "Prefer this over ls via run_command."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory to list (default: current directory).",
                },
                "hidden": {
                    "type": "boolean",
                    "description": "Include hidden files/directories (default: false).",
                },
            },
        },
    },
    {
        "name": "search_files",
        "description": (
            "Search file contents using regex. Returns matching lines with "
            "file paths and line numbers. Respects .gitignore and skips "
            "binary files. Prefer this over grep/rg via run_command."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for.",
                },
                "path": {
                    "type": "string",
                    "description": "Directory or file to search in (default: current directory).",
                },
                "glob": {
                    "type": "string",
                    "description": "Filter to specific file types (e.g. '*.py', '*.js').",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of matching lines to return (default: 50).",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Create or overwrite a file with the given content. "
            "Use this to create new files. Always requires user confirmation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to write.",
                },
                "content": {
                    "type": "string",
                    "description": "The full content to write to the file.",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Make a targeted edit to an existing file by replacing an exact string "
            "match with new content. You must provide a string that uniquely "
            "identifies the location to edit. Always requires user confirmation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to edit.",
                },
                "old_string": {
                    "type": "string",
                    "description": "The exact string to find and replace. Must match uniquely in the file.",
                },
                "new_string": {
                    "type": "string",
                    "description": "The replacement string.",
                },
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
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
    },
]


def setup_readline():
    try:
        readline.read_history_file(HISTORY_FILE)
    except FileNotFoundError:
        pass
    readline.set_history_length(HISTORY_SIZE)
    atexit.register(readline.write_history_file, HISTORY_FILE)


def make_client():
    region = os.environ.get("CLOUD_ML_REGION", "us-east5")
    project_id = os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
    if not project_id:
        print("Set ANTHROPIC_VERTEX_PROJECT_ID to your GCP project ID.")
        sys.exit(1)
    return AnthropicVertex(region=region, project_id=project_id)


def truncate(text, max_lines=MAX_OUTPUT_LINES):
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    half = max_lines // 2
    kept = lines[:half] + [f"\n... ({len(lines) - max_lines} lines omitted) ...\n"] + lines[-half:]
    return "\n".join(kept)


def run_command(command):
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            if output:
                output += "\n"
            output += f"[stderr]\n{result.stderr}"
        if not output:
            output = "(no output)"
        return truncate(output)
    except subprocess.TimeoutExpired:
        return f"(command timed out after {COMMAND_TIMEOUT}s)"
    except Exception as e:
        return f"(error running command: {e})"


def handle_read_file(params):
    path = params.get("path", "")
    offset = max(params.get("offset", 1), 1)
    limit = params.get("limit", 200)

    try:
        size = os.path.getsize(path)
        with open(path, "r", errors="replace") as f:
            lines = f.readlines()
        total = len(lines)
        selected = lines[offset - 1 : offset - 1 + limit]
        numbered = []
        for i, line in enumerate(selected, start=offset):
            numbered.append(f"{i:6}\t{line.rstrip()}")
        header = f"[{path}: {total} lines, {size} bytes]"
        if offset > 1 or offset - 1 + limit < total:
            header += f" (showing lines {offset}-{min(offset - 1 + limit, total)})"
        return header + "\n" + "\n".join(numbered)
    except IsADirectoryError:
        return f"(error: {path} is a directory, use list_directory instead)"
    except Exception as e:
        return f"(error reading {path}: {e})"


def handle_list_directory(params):
    path = params.get("path", ".")
    hidden = params.get("hidden", False)

    try:
        entries = sorted(os.listdir(path))
        if not hidden:
            entries = [e for e in entries if not e.startswith(".")]
        lines = []
        for name in entries:
            full = os.path.join(path, name)
            try:
                st = os.stat(full)
                if os.path.isdir(full):
                    lines.append(f"  {name}/")
                elif os.path.islink(full):
                    target = os.readlink(full)
                    lines.append(f"  {name} -> {target}")
                else:
                    size = st.st_size
                    if size >= 1_000_000:
                        size_str = f"{size / 1_000_000:.1f}M"
                    elif size >= 1_000:
                        size_str = f"{size / 1_000:.1f}k"
                    else:
                        size_str = f"{size}B"
                    lines.append(f"  {name}  ({size_str})")
            except OSError:
                lines.append(f"  {name}  (?)")
        header = f"[{path}: {len(entries)} entries]"
        return header + "\n" + "\n".join(lines) if lines else header + "\n  (empty)"
    except Exception as e:
        return f"(error listing {path}: {e})"


def handle_search_files(params):
    pattern = params.get("pattern", "")
    path = params.get("path", ".")
    glob_filter = params.get("glob")
    max_results = params.get("max_results", 50)

    # Try ripgrep first, fall back to grep
    cmd = ["rg", "-n", "--no-heading", "--max-count", str(max_results)]
    if glob_filter:
        cmd += ["--glob", glob_filter]
    cmd += [pattern, path]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=COMMAND_TIMEOUT
        )
    except FileNotFoundError:
        # rg not installed, fall back to grep
        cmd = ["grep", "-rn"]
        if glob_filter:
            cmd += ["--include", glob_filter]
        cmd += [pattern, path]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=COMMAND_TIMEOUT
            )
        except Exception as e:
            return f"(error: {e})"
    except subprocess.TimeoutExpired:
        return f"(search timed out after {COMMAND_TIMEOUT}s)"

    if result.returncode == 1:
        return "(no matches found)"
    if result.returncode > 1:
        return f"(search error: {result.stderr.strip()})"

    lines = result.stdout.splitlines()
    if len(lines) > max_results:
        lines = lines[:max_results]
        lines.append(f"... (results capped at {max_results})")
    return f"[{len(lines)} matches]\n" + "\n".join(lines)


def confirm_edit(prompt_lines):
    """Show a preview and ask for Y/n confirmation."""
    for line in prompt_lines:
        print(line)
    answer = input(f"  {dim('Apply? [Y/n]')} ").strip().lower()
    return answer in ("", "y", "yes")


def handle_write_file(params):
    path = params.get("path", "")
    content = params.get("content", "")
    exists = os.path.exists(path)
    action = "overwrite" if exists else "create"
    lines = content.splitlines()

    preview = [f"  {bold('write_file')}: {action} {cyan(path)} ({len(lines)} lines)"]
    # Show a compact preview — first/last few lines
    if len(lines) <= 10:
        for line in lines:
            preview.append(f"  {green('+' + ' ' + line)}")
    else:
        for line in lines[:5]:
            preview.append(f"  {green('+' + ' ' + line)}")
        preview.append(f"  {dim(f'... ({len(lines) - 10} more lines) ...')}")
        for line in lines[-5:]:
            preview.append(f"  {green('+' + ' ' + line)}")

    if not confirm_edit(preview):
        return "(user declined to write this file)"

    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return f"(wrote {len(lines)} lines to {path})"
    except Exception as e:
        return f"(error writing {path}: {e})"


def handle_edit_file(params):
    path = params.get("path", "")
    old_string = params.get("old_string", "")
    new_string = params.get("new_string", "")

    try:
        with open(path, "r") as f:
            original = f.read()
    except Exception as e:
        return f"(error reading {path}: {e})"

    count = original.count(old_string)
    if count == 0:
        return f"(error: old_string not found in {path})"
    if count > 1:
        return f"(error: old_string matches {count} locations in {path}, must be unique)"

    preview = [f"  {bold('edit_file')}: {cyan(path)}"]
    for line in old_string.splitlines():
        preview.append(f"  {red('-' + ' ' + line)}")
    for line in new_string.splitlines():
        preview.append(f"  {green('+' + ' ' + line)}")

    if not confirm_edit(preview):
        return "(user declined this edit)"

    updated = original.replace(old_string, new_string, 1)
    try:
        with open(path, "w") as f:
            f.write(updated)
        return f"(edited {path})"
    except Exception as e:
        return f"(error writing {path}: {e})"


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


def agent_turn(client, model, messages, auto_approve=False, usage_totals=None):
    # Stream the response so text appears as it's generated
    content_blocks = []
    current_text = ""
    current_tool_input_json = ""
    current_tool_id = None
    current_tool_name = None
    printed_text = False

    with client.messages.stream(
        model=model,
        max_tokens=65536,
        system=SYSTEM_PROMPT,
        tools=TOOLS,
        messages=messages,
    ) as stream:
        for event in stream:
            if event.type == "content_block_start":
                if event.content_block.type == "text":
                    current_text = ""
                    if not printed_text:
                        print()  # blank line before model output
                        printed_text = True
                elif event.content_block.type == "tool_use":
                    current_tool_id = event.content_block.id
                    current_tool_name = event.content_block.name
                    current_tool_input_json = ""

            elif event.type == "content_block_delta":
                if event.delta.type == "text_delta":
                    print(event.delta.text, end="", flush=True)
                    current_text += event.delta.text
                elif event.delta.type == "input_json_delta":
                    current_tool_input_json += event.delta.partial_json

            elif event.type == "content_block_stop":
                if current_text:
                    content_blocks.append({"type": "text", "text": current_text})
                    current_text = ""
                if current_tool_id:
                    tool_input = json.loads(current_tool_input_json) if current_tool_input_json else {}
                    content_blocks.append({
                        "type": "tool_use",
                        "id": current_tool_id,
                        "name": current_tool_name,
                        "input": tool_input,
                    })
                    current_tool_id = None
                    current_tool_input_json = ""

        # Get usage from the final message
        final = stream.get_final_message()
        if usage_totals is not None and final.usage:
            usage_totals["input"] += final.usage.input_tokens
            usage_totals["output"] += final.usage.output_tokens

    # End streaming text with a newline if we printed anything
    if printed_text:
        print()

    # Separate text blocks from tool_use blocks
    tool_uses = [b for b in content_blocks if b["type"] == "tool_use"]

    # If no tool use, the model is done
    if not tool_uses:
        return messages, True  # done

    # Append the full assistant response to messages
    messages.append({"role": "assistant", "content": content_blocks})

    # Process each tool use
    tool_results = []
    for tool_use in tool_uses:
        name = tool_use["name"]
        params = tool_use["input"]

        if name == "run_command":
            command = params.get("command", "")
            description = params.get("description")
            if confirm(command, description, auto_approve):
                output = run_command(command)
            else:
                output = "(user declined to run this command)"
        elif name == "read_file":
            print(f"  {bold('read_file')}: {cyan(params.get('path', ''))}")
            output = handle_read_file(params)
        elif name == "list_directory":
            print(f"  {bold('list_directory')}: {cyan(params.get('path', '.'))}")
            output = handle_list_directory(params)
        elif name == "search_files":
            print(f"  {bold('search_files')}: {params.get('pattern', '')} in {cyan(params.get('path', '.'))}")
            output = handle_search_files(params)
        elif name == "write_file":
            output = handle_write_file(params)
        elif name == "edit_file":
            output = handle_edit_file(params)
        else:
            output = f"(unknown tool: {name})"

        print(dim(f"  → {len(output.splitlines())} lines of output"))
        tool_results.append(
            {
                "type": "tool_result",
                "tool_use_id": tool_use["id"],
                "content": output,
            }
        )

    messages.append({"role": "user", "content": tool_results})
    return messages, False  # not done, model needs to see results


def format_tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def agent_loop(client, model, auto_approve=False):
    mode = "YOLO mode" if auto_approve else "confirm mode"
    print(f"{bold('Agent ready')} {dim(f'(model: {model}, {mode})')}")
    print(dim("Type a question or 'quit' to exit.\n"))
    conversation = []
    session_usage = {"input": 0, "output": 0}

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

        conversation.append({"role": "user", "content": user_input})

        # Inner loop: let the model run commands until it produces a final answer
        messages = list(conversation)
        turn_usage = {"input": 0, "output": 0}
        steps = 0
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

        session_usage["input"] += turn_usage["input"]
        session_usage["output"] += turn_usage["output"]
        print(dim(
            f"  [{format_tokens(turn_usage['input'])} in, "
            f"{format_tokens(turn_usage['output'])} out | "
            f"session: {format_tokens(session_usage['input'])} in, "
            f"{format_tokens(session_usage['output'])} out]"
        ))

        # Keep the conversation history for follow-up questions
        conversation = messages


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
    args = parser.parse_args()

    model = MODELS[args.model]
    setup_readline()
    client = make_client()
    agent_loop(client, model, auto_approve=args.yolo)


if __name__ == "__main__":
    main()
