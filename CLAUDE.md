# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Python agent loop that uses LLMs to answer user questions by exploring the filesystem and running Unix commands. Supports Anthropic Claude (direct API and Vertex AI) and Google Gemini models.

## Installation

```bash
pip install -e .              # editable install
pip install -e '.[vertex]'    # with Vertex AI support
pip install -e '.[gemini]'    # with Gemini support
pip install -e '.[all]'       # all providers
```

## Running

```bash
# Option 1: Direct Anthropic API
export ANTHROPIC_API_KEY="your-api-key"

# Option 2: Google Vertex AI
export ANTHROPIC_VERTEX_PROJECT_ID="your-gcp-project-id"
export CLOUD_ML_REGION="us-east5"  # optional, defaults to us-east5

# If both are set, ANTHROPIC_API_KEY takes priority.

# Option 3: Google Gemini
export GOOGLE_API_KEY="your-google-api-key"

# Run with default model (sonnet)
llm-agent

# Select model
llm-agent -m opus
llm-agent -m haiku
llm-agent -m sonnet
llm-agent -m gemini-flash
llm-agent -m gemini-pro

# Auto-approve safe commands
llm-agent -y
llm-agent --yolo

# Gemini thinking level (low/medium/high)
llm-agent -m gemini-pro --thinking high
llm-agent -m gemini-flash --thinking low -c "summarise this repo"

# Single-shot mode (non-interactive)
llm-agent -c "how much disk space is free?"
llm-agent -c "what's in /etc/hosts?" -m haiku --yolo

# Attach images or PDFs with @filepath
llm-agent -c "@photo.png what's in this image?"
llm-agent -c "@report.pdf summarize this document"
llm-agent -c "@a.png @b.png compare these two images"
```

## Package Structure

```
pyproject.toml          — package metadata and entry point
llm_agent/
    __init__.py         — VERSION and package metadata
    cli.py              — main, arg parsing, REPL, run_question
    agent.py            — agent_turn, streaming, caching, retry logic (Anthropic)
    gemini_agent.py     — gemini_agent_turn, Gemini streaming + format conversion
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

- **`cli.py`** — entry point, arg parsing, REPL loop, selects agent turn function based on model
- **`agent.py`** — Anthropic streaming API calls, prompt caching, retry logic
- **`gemini_agent.py`** — Gemini streaming, tool schema conversion, message format conversion
- **`formatting.py`** — ANSI colour helpers, output truncation, token formatting
- **`tools/`** — one file per tool, each exporting `SCHEMA`, `handle()`, and optional `LOG`/`NEEDS_CONFIRM`

The key flow:

1. **`main()`** (`cli.py`) — parses args (`-m`, `-y`, `-c`, `--thinking`), creates API client, dispatches to single-shot or interactive mode
2. **`run_question()`** (`cli.py`) — runs a single user question to completion: calls `agent_turn` (or `gemini_agent_turn`) in a loop until the model produces a final answer, with `MAX_STEPS` guard and Ctrl+C handling
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
- **Thinking levels** — Gemini models support `--thinking low|medium|high` to control reasoning depth. `gemini-pro` defaults to `high`; other models default to off. In interactive mode, `/thinking` shows or changes the level (`/thinking off` resets to no thinking config). Has no effect on Anthropic models.
- **Readline** — line editing and persistent history (`~/.agent_history`, 1000 entries) in interactive mode
- **Prompt caching** — system prompt, tool definitions, and conversation prefix are cached across API calls to reduce cost and latency
- **Token tracking** — per-turn and session totals printed after each answer (to stderr in `-c` mode for clean piping), includes cache hit stats
- **File attachments** — use `@filepath` in prompts to attach images (png, jpg, jpeg, gif, webp) or PDFs. The `@` must be at the start of a word (so `user@email.com` is left alone). Works in both interactive and `-c` mode. Attachments are base64-encoded and sent as multimodal content blocks.
- **Output truncation** — command output over 200 lines is cut to first/last 100 lines

## Model Names

Vertex AI Anthropic models use bare names without `@date` suffixes: `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-5`.

Gemini model aliases: `gemini-flash` → `gemini-2.5-flash`, `gemini-pro` → `gemini-3.1-pro-preview`.

## Provider Architecture

Messages are stored internally in Anthropic format. For Gemini models, `gemini_agent.py` converts messages at the API boundary:
- `role: "assistant"` → `role: "model"`
- `tool_use` blocks → `FunctionCall` parts
- `tool_result` blocks → `FunctionResponse` parts (tool name stashed in `_name` field)

The `google-genai` package is lazy-imported only when a Gemini model is selected. Switching providers via `/model` recreates the client and clears the conversation.
