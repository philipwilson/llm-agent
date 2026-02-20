#!/usr/bin/env python3
"""
A toy agent loop that uses Claude to answer questions
by running Unix CLI commands. Supports both the direct Anthropic API
and Google Vertex AI.
"""

import argparse
import atexit
import json
import os
import re
import readline
import subprocess
import sys
import time

import anthropic


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
DEFAULT_COMMAND_TIMEOUT = 30
COMMAND_TIMEOUT = DEFAULT_COMMAND_TIMEOUT
MAX_STEPS = 20
MAX_CONVERSATION_TURNS = 40
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
- read_url: Fetch a web page and return its text content. Prefer this over \
curl via run_command when you need to read a web page.
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
        "name": "read_url",
        "description": (
            "Fetch a web page and return its text content (HTML converted to "
            "plain text). Returns the page title and final URL after redirects. "
            "Prefer this over curl via run_command."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch (must be http:// or https://).",
                },
                "max_length": {
                    "type": "integer",
                    "description": "Maximum characters of content to return (default: 10000).",
                },
            },
            "required": ["url"],
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
                st = os.lstat(full)
                if os.path.islink(full):
                    target = os.readlink(full)
                    lines.append(f"  {name} -> {target}")
                elif os.path.isdir(full):
                    lines.append(f"  {name}/")
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
    cmd = ["rg", "-n", "--no-heading"]
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
        except subprocess.TimeoutExpired:
            return f"(search timed out after {COMMAND_TIMEOUT}s)"
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


WEB_TIMEOUT = 15
MAX_DOWNLOAD_BYTES = 1_000_000  # 1MB


def handle_read_url(params):
    url = params.get("url", "")
    max_length = params.get("max_length", 10000)

    # Only allow http/https
    if not url.startswith(("http://", "https://")):
        return "(error: only http:// and https:// URLs are supported)"

    # Fetch with curl
    try:
        fetch = subprocess.run(
            [
                "curl", "-sL",
                "--max-filesize", str(MAX_DOWNLOAD_BYTES),
                "--max-time", str(WEB_TIMEOUT),
                "-H", "User-Agent: agent.py/1.0",
                "-w", "\n__STATUS__:%{http_code}\n__URL__:%{url_effective}",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=WEB_TIMEOUT + 5,
        )
    except subprocess.TimeoutExpired:
        return f"(fetch timed out after {WEB_TIMEOUT}s)"
    except FileNotFoundError:
        return "(error: curl not found)"
    except Exception as e:
        return f"(error fetching URL: {e})"

    if fetch.returncode != 0:
        return f"(error: curl returned exit code {fetch.returncode}: {fetch.stderr.strip()})"

    # Parse status and final URL from curl -w output
    output = fetch.stdout
    status_code = ""
    final_url = url
    for line in output.splitlines()[-5:]:
        if line.startswith("__STATUS__:"):
            status_code = line.split(":", 1)[1]
        elif line.startswith("__URL__:"):
            final_url = line.split(":", 1)[1]

    if status_code and not status_code.startswith(("2", "3")):
        return f"(HTTP {status_code} error fetching {url})"

    # Strip the __STATUS__ and __URL__ lines from the content
    content_lines = []
    for line in output.splitlines():
        if line.startswith(("__STATUS__:", "__URL__:")):
            continue
        content_lines.append(line)
    html = "\n".join(content_lines)

    # Convert HTML to plain text using lynx, w3m, or fallback to basic stripping
    text = None
    for converter in [
        ["lynx", "-stdin", "-dump", "-nolist", "-width=120"],
        ["w3m", "-T", "text/html", "-dump", "-cols", "120"],
    ]:
        try:
            conv = subprocess.run(
                converter,
                input=html,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if conv.returncode == 0 and conv.stdout.strip():
                text = conv.stdout
                break
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    if text is None:
        # Basic fallback: strip HTML tags with a regex
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n", "\n\n", text)
        text = text.strip()

    # Extract title from HTML
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else "(no title)"

    # Truncate
    total_len = len(text)
    if total_len > max_length:
        text = text[:max_length] + f"\n\n... (truncated, {total_len} total characters)"

    header = f"[{title}]"
    if final_url != url:
        header += f"\n[redirected to: {final_url}]"
    header += f"\n[{total_len} characters]"

    return header + "\n\n" + text


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


def handle_run_command(params, auto_approve=False):
    command = params.get("command", "")
    description = params.get("description")
    if confirm(command, description, auto_approve):
        return run_command(command)
    return "(user declined to run this command)"


# --- Tool registry ---
# Each entry maps a tool name to a dict with:
#   "handler": callable(params, **kwargs) -> str
#   "log": callable(params) -> None (prints a log line before execution)
#   "needs_confirm": bool — if True, passes auto_approve to handler

TOOL_REGISTRY = {
    "read_file": {
        "handler": handle_read_file,
        "log": lambda p: print(f"  {bold('read_file')}: {cyan(p.get('path', ''))}"),
    },
    "list_directory": {
        "handler": handle_list_directory,
        "log": lambda p: print(f"  {bold('list_directory')}: {cyan(p.get('path', '.'))}"),
    },
    "search_files": {
        "handler": handle_search_files,
        "log": lambda p: print(
            f"  {bold('search_files')}: {p.get('pattern', '')} in {cyan(p.get('path', '.'))}"
        ),
    },
    "read_url": {
        "handler": handle_read_url,
        "log": lambda p: print(f"  {bold('read_url')}: {cyan(p.get('url', ''))}"),
    },
    "write_file": {
        "handler": handle_write_file,
    },
    "edit_file": {
        "handler": handle_edit_file,
    },
    "run_command": {
        "handler": handle_run_command,
        "needs_confirm": True,
    },
}


MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]  # seconds between retries (exponential backoff)

CACHE_CONTROL = {"type": "ephemeral"}

CACHED_SYSTEM = [{
    "type": "text",
    "text": SYSTEM_PROMPT,
    "cache_control": CACHE_CONTROL,
}]

CACHED_TOOLS = [*TOOLS[:-1], {**TOOLS[-1], "cache_control": CACHE_CONTROL}]


def _cache_messages(messages):
    """Add a cache breakpoint to the last message in the conversation.

    This ensures the growing conversation prefix is cached across
    successive calls within a single question.
    """
    if not messages:
        return messages
    msgs = [*messages[:-1]]
    last = messages[-1]
    content = last.get("content")
    if isinstance(content, str):
        # Simple string content — wrap in a content block to add cache_control
        msgs.append({
            **last,
            "content": [{
                "type": "text",
                "text": content,
                "cache_control": CACHE_CONTROL,
            }],
        })
    elif isinstance(content, list) and content:
        # List of content blocks — add cache_control to the last block
        cached_content = [*content[:-1], {**content[-1], "cache_control": CACHE_CONTROL}]
        msgs.append({**last, "content": cached_content})
    else:
        msgs.append(last)
    return msgs


def agent_turn(client, model, messages, auto_approve=False, usage_totals=None):
    # Stream the response with retry logic for transient API errors
    content_blocks = []
    printed_text = False
    cached_msgs = _cache_messages(messages)

    for attempt in range(MAX_RETRIES + 1):
        try:
            content_blocks = []
            current_text = ""
            current_tool_input_json = ""
            current_tool_id = None
            current_tool_name = None

            with client.messages.stream(
                model=model,
                max_tokens=65536,
                system=CACHED_SYSTEM,
                tools=CACHED_TOOLS,
                messages=cached_msgs,
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
                            current_tool_name = None
                            current_tool_input_json = ""

                # Get usage from the final message
                final = stream.get_final_message()
                if usage_totals is not None and final.usage:
                    usage_totals["input"] += final.usage.input_tokens
                    usage_totals["output"] += final.usage.output_tokens
                    cache_read = getattr(final.usage, "cache_read_input_tokens", 0) or 0
                    cache_create = getattr(final.usage, "cache_creation_input_tokens", 0) or 0
                    usage_totals["cache_read"] = usage_totals.get("cache_read", 0) + cache_read
                    usage_totals["cache_create"] = usage_totals.get("cache_create", 0) + cache_create

            break  # success, exit retry loop

        except (anthropic.RateLimitError, anthropic.InternalServerError) as e:
            if attempt < MAX_RETRIES:
                delay = RETRY_DELAYS[attempt]
                print(f"\n{yellow(f'API error: {e}. Retrying in {delay}s...')}")
                time.sleep(delay)
                # Reset state for retry — any partial text was already printed,
                # so we can't undo it, but the model will re-generate
                content_blocks = []
            else:
                print(f"\n{red(f'API error after {MAX_RETRIES + 1} attempts: {e}')}")
                return messages, True  # give up, return to prompt

        except anthropic.APIConnectionError as e:
            if attempt < MAX_RETRIES:
                delay = RETRY_DELAYS[attempt]
                print(f"\n{yellow(f'Connection error: {e}. Retrying in {delay}s...')}")
                time.sleep(delay)
                content_blocks = []
            else:
                print(f"\n{red(f'Connection error after {MAX_RETRIES + 1} attempts: {e}')}")
                return messages, True

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

        entry = TOOL_REGISTRY.get(name)
        if entry is None:
            output = f"(unknown tool: {name})"
        else:
            log_fn = entry.get("log")
            if log_fn:
                log_fn(params)
            if entry.get("needs_confirm"):
                output = entry["handler"](params, auto_approve=auto_approve)
            else:
                output = entry["handler"](params)

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
    print(dim("Type a question, /clear to reset, or 'quit' to exit.\n"))
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
            # Cancelled — don't update conversation history
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
        default=DEFAULT_COMMAND_TIMEOUT,
        metavar="SECONDS",
        help=f"Command timeout in seconds (default: {DEFAULT_COMMAND_TIMEOUT})",
    )
    args = parser.parse_args()

    global COMMAND_TIMEOUT
    COMMAND_TIMEOUT = args.timeout
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
