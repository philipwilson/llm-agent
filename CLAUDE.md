# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Python agent loop that uses Claude to answer user questions by exploring the filesystem and running Unix commands. Supports both the direct Anthropic API and Google Vertex AI.

## Installation

```bash
pip install -e .              # editable install
pip install -e '.[vertex]'    # with Vertex AI support
```

## Running

```bash
# Option 1: Direct Anthropic API
export ANTHROPIC_API_KEY="your-api-key"

# Option 2: Google Vertex AI
export ANTHROPIC_VERTEX_PROJECT_ID="your-gcp-project-id"
export CLOUD_ML_REGION="us-east5"  # optional, defaults to us-east5

# If both are set, ANTHROPIC_API_KEY takes priority.

# Run with default model (sonnet)
llm-agent

# Select model
llm-agent -m opus
llm-agent -m haiku
llm-agent -m sonnet

# Auto-approve safe commands
llm-agent -y
llm-agent --yolo

# Single-shot mode (non-interactive)
llm-agent -c "how much disk space is free?"
llm-agent -c "what's in /etc/hosts?" -m haiku --yolo
```

## Package Structure

```
pyproject.toml          — package metadata and entry point
llm_agent/
    __init__.py         — VERSION and package metadata
    cli.py              — main, arg parsing, REPL, run_question
    agent.py            — agent_turn, streaming, caching, retry logic
    formatting.py       — colour helpers, output truncation, token formatting
    system_prompt.txt   — system prompt (edit without touching Python)
    tools/
        __init__.py     — collects TOOLS list + TOOL_REGISTRY from modules
        base.py         — ShellState, _resolve, confirm_edit, COMMAND_TIMEOUT
        read_file.py    — SCHEMA + handle
        list_directory.py — SCHEMA + handle
        search_files.py — SCHEMA + handle
        read_url.py     — SCHEMA + handle
        write_file.py   — SCHEMA + handle
        edit_file.py    — SCHEMA + handle
        run_command.py  — SCHEMA + handle + NEEDS_CONFIRM
```

- Package name: `llm-agent` (import name: `llm_agent`)
- Console entry point: `llm-agent` command

## Architecture

The agent is split across several modules:

- **`cli.py`** — entry point, arg parsing, REPL loop
- **`agent.py`** — streaming API calls, prompt caching, retry logic
- **`formatting.py`** — ANSI colour helpers, output truncation, token formatting
- **`tools/`** — one file per tool, each exporting `SCHEMA`, `handle()`, and optional `LOG`/`NEEDS_CONFIRM`

The key flow:

1. **`main()`** (`cli.py`) — parses args (`-m`, `-y`, `-c`), creates API client, dispatches to single-shot or interactive mode
2. **`run_question()`** (`cli.py`) — runs a single user question to completion: calls `agent_turn` in a loop until the model produces a final answer, with `MAX_STEPS` guard and Ctrl+C handling
3. **`agent_loop()`** (`cli.py`) — interactive REPL that calls `run_question` repeatedly, maintains conversation history and session-level token stats
4. **`agent_turn()`** (`agent.py`) — streams a single model API call, dispatches tool use via `TOOL_REGISTRY`, returns when the model produces a final text answer or requests tool results
5. **`TOOL_REGISTRY`** (`tools/__init__.py`) — auto-collected from tool modules; adding a new tool requires creating a tool file and adding one import line

## Tools

The model has seven tools. Read-only tools run without confirmation; mutating tools always require it.

**Read-only (no confirmation):**
- **`read_file`** — reads file contents with line numbers, supports `offset`/`limit` for paging. Reports total line count and file size.
- **`list_directory`** — lists directory entries with type indicators and file sizes. Optional `hidden` flag.
- **`search_files`** — regex search over file contents using ripgrep (falls back to grep). Supports glob filtering and result cap.
- **`read_url`** — fetches a web page via curl, converts HTML to plain text via lynx/w3m (regex fallback). Returns title, final URL, and content truncated to `max_length` (default 10k chars). http/https only, 1MB download cap.

**Mutating (always require confirmation):**
- **`write_file`** — creates or overwrites a file. Shows a content preview and prompts `Apply? [Y/n]`. Creates parent directories automatically.
- **`edit_file`** — targeted find-and-replace in an existing file. `old_string` must match exactly once (fails if not found or ambiguous). Shows a `-`/`+` diff preview.
- **`run_command`** — arbitrary shell command execution. Prompts `Run? [Y/n]`. In yolo mode (`-y`), auto-approves unless the command matches `DANGEROUS_PATTERNS`.

## Key behaviours

- **Working directory tracking** — `ShellState` tracks `cwd` across commands (like Claude Code: working directory persists, other shell state does not). All file-based tools resolve relative paths against it via `_resolve()`
- **Streaming** — model responses stream to the terminal as they're generated
- **Readline** — line editing and persistent history (`~/.agent_history`, 1000 entries) in interactive mode
- **Prompt caching** — system prompt, tool definitions, and conversation prefix are cached across API calls to reduce cost and latency
- **Token tracking** — per-turn and session totals printed after each answer (to stderr in `-c` mode for clean piping), includes cache hit stats
- **Output truncation** — command output over 200 lines is cut to first/last 100 lines

## Model Names

Vertex AI Anthropic models use bare names without `@date` suffixes: `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-6`.
