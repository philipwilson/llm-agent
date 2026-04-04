# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Python agent loop that uses LLMs to answer user questions by exploring the filesystem and running Unix commands. Supports Anthropic Claude (direct API and Vertex AI), Google Gemini, OpenAI, and local Ollama models.

## Installation

```bash
pip install -e .              # editable install
pip install -e '.[vertex]'    # with Vertex AI support
pip install -e '.[gemini]'    # with Gemini support
pip install -e '.[openai]'    # with OpenAI support
pip install -e '.[ollama]'    # with Ollama support (local models)
pip install -e '.[tui]'       # with Textual TUI
pip install -e '.[mcp]'       # with MCP client support
pip install -e '.[all]'       # all providers + TUI + MCP
pip install -e '.[test]'      # pytest + plugins
```

## Testing

```bash
pip install -e '.[test]'      # install test dependencies
python -m pytest              # run all tests
python -m pytest -v           # verbose output
python -m pytest tests/test_is_dangerous.py  # run one file
python -m pytest -k fuzzy     # run tests matching a pattern
python -m pytest --cov=llm_agent  # with coverage report
```

Tests live in `tests/` and use pytest with a 10-second per-test timeout (configured in `pyproject.toml`).

**Key fixtures** (in `tests/conftest.py`):
- `mock_display` (autouse) — injects a `MockDisplay` via `set_display()` so tests never touch stdout/stdin. Captures all calls (`.logs`, `.confirms`, `.errors`, `.statuses`, etc.) for assertions.
- `declining_display` — a `MockDisplay` that returns `False` from `confirm()`, for testing rejection paths.

**Tool fixtures** (in `tests/tools/conftest.py`):
- `reset_shell_cwd` (autouse) — resets the global `shell` singleton to a `tmp_path` before each tool test and restores it after. Also clears background tasks.

**Test structure:**
```
tests/
    conftest.py               — MockDisplay, shared fixtures
    test_formatting.py         — truncate(), format_tokens()
    test_is_dangerous.py       — is_dangerous() security checks (~30 cases)
    test_cli_utils.py          — estimate_tokens, parse_attachments, model detection, trim_conversation
    test_config.py             — config file loading, validation, type checking
    test_debug.py              — debug logger events, truncation, no-op mode
    test_models.py             — alias resolution, provider detection, context windows, max tokens
    test_persistence.py        — session save/load, clean messages, list/find sessions
    test_context.py            — project type detection, config parsers
    test_skills.py             — skill parsing, rendering, discovery
    test_session.py            — Session command routing, state management
    test_agents.py             — agent definitions, custom agent loading, tool filtering
    test_display.py            — Display protocol, streaming suppression, confirm/ask
    tools/
        conftest.py            — tool-specific fixtures (shell reset)
        test_run_command.py    — ShellState, background tasks, cwd tracking
        test_check_task.py     — background task polling
        test_start_session.py  — PTY-backed interactive sessions
        test_write_stdin.py    — PTY stdin writes, polling, close flow
        test_edit_file.py      — fuzzy matching, line ranges, batch edits
        test_apply_patch.py    — structured multi-file patch grammar and application
        test_read_file.py      — line ranges, offset/limit, truncation guidance, error handling
        test_read_many_files.py — batched file reads with include/exclude filtering
        test_write_file.py     — create, overwrite, parent dirs, preview
        test_list_directory.py — entries, hidden files, sizes, symlinks, depth, pagination
        test_glob_files.py     — patterns, recursive **, max_results
        test_file_outline.py   — symbol extraction for Python/JS/Go/Rust
        test_lsp_navigate.py   — fake-session tests for document symbols, definition, references, hover
        test_search_files.py   — regex search, glob filter, file-only mode, max_results
        test_delegate.py       — missing params, context, run_subagent callback
        test_ask_user.py       — free-text, choices, numeric resolution
        test_tool_dispatch.py  — parallel/sequential routing, timeouts
    integration/
        test_agent_turn.py     — fake Anthropic streaming, tool dispatch, multi-turn
        test_trim_conversation.py — trimming with mock summarization
        test_subagent.py       — tool filtering, model override, streaming suppression
        test_ollama.py         — Ollama agent turn, model name stripping, tool dispatch
        test_mcp_registration.py — register/unregister, build_tool_set, format helpers
```

**Writing new tests:**
- The `MockDisplay` is injected automatically — no setup needed for stdout isolation.
- Tool tests get a `tmp_path` as the shell's working directory via the autouse `reset_shell_cwd` fixture.
- For tools needing confirmation, use `auto_approve=True` in `handle()` or use the `declining_display` fixture to test rejection.
- Pure functions (`is_dangerous`, `truncate`, `format_tokens`, etc.) need no mocking at all.

## Configuration

Settings can be persisted in `~/.config/llm-agent/config.toml`. CLI flags override config values. All keys are optional:

```toml
model = "opus"        # model alias or ollama:<name>
yolo = true           # auto-approve safe commands
timeout = 60          # command timeout in seconds
thinking = "high"     # Gemini thinking level (low/medium/high)
no_tui = false        # use readline REPL instead of TUI
debug = false         # enable debug trace logging
```

Precedence: CLI flag > config file > hardcoded default. Unknown keys and type mismatches produce warnings on stderr. The config file is read once at startup.

**Key files:**
- `config.py` — `load_config()`, `VALID_KEYS`, `CONFIG_PATH`
- `cli.py` — merge logic in `main()` after argparse

## Debug / Trace Mode

`--debug` (or `debug = true` in config) writes structured JSON-lines to `~/.local/share/llm-agent/debug/<timestamp>-<pid>.jsonl`. Each line has a UTC timestamp, elapsed seconds, event type, and event data. The log file path is printed at startup.

**Events logged:**
- `session_start` / `session_end` — PID and cwd
- `system_prompt` — length and first 2000 chars
- `api_request` — model, provider, message/tool counts, extras (thinking level, reasoning flag)
- `api_response` — model, usage stats, content types, duration
- `api_error` — error type/message, attempt number, retry decision
- `tool_call` — tool name and params (long strings truncated)
- `tool_result` — tool name, output line count, duration, error if any
- `trim` — dropped message count, old/new token estimates

When debug is disabled (the default), `get_debug()` returns a `_NoOpDebug` singleton whose methods are all no-ops, so there is zero overhead.

**Key files:**
- `debug.py` — `DebugLogger`, `_NoOpDebug`, `enable_debug()`, `get_debug()`
- `agent.py`, `gemini_agent.py`, `openai_agent.py`, `ollama_agent.py` — API request/response/error instrumentation
- `tools/__init__.py` — tool call/result instrumentation in `_run_one()`
- `session.py` — system prompt and trim logging

## Session Persistence

Sessions are auto-saved to `~/.local/share/llm-agent/sessions/` as JSON files after each turn. Single-shot mode (`-c`) does not save sessions.

**File format:** `{YYYYMMDD-HHMMSS}-{session_id}.json` containing model, cwd, timestamps, usage stats, first question, and the full message list. Non-serializable data (`_gemini_parts`) and base64 attachments are stripped. Writes are atomic (tmp + rename).

**Resuming:** `--resume` loads the most recent session; `--resume ID` loads by ID prefix. The `/sessions` command lists recent sessions.

**Key files:**
- `persistence.py` — `save_session()`, `load_session()`, `list_sessions()`, `find_session()`
- `session.py` — `Session._save()`, `Session.load_from()`, `/sessions` command
- `cli.py` — `--resume` flag and resume flow in `main()`

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

# Option 4: OpenAI
export OPENAI_API_KEY="your-openai-api-key"

# Option 5: Ollama (local models, no API key needed)
# Just have Ollama running: ollama serve
export OLLAMA_HOST="http://localhost:11434"  # optional, this is the default

# Run with default model (sonnet)
llm-agent

# Select model
llm-agent -m opus
llm-agent -m haiku
llm-agent -m sonnet
llm-agent -m gemini-flash
llm-agent -m gemini-pro
llm-agent -m gpt-4o
llm-agent -m gpt-4o-mini
llm-agent -m gpt-5.2
llm-agent -m o3
llm-agent -m o4-mini
llm-agent -m qwen3                                # Ollama: qwen3.5:122b
llm-agent -m qwen3-cloud                          # Ollama: qwen3.5:cloud
llm-agent -m qwen3-coder                          # Ollama: qwen3.5:35b-a3b-coding-nvfp4
llm-agent -m gemma4-31b                           # Ollama: gemma4:31b
llm-agent -m nemotron-nano                        # Ollama: nemotron-3-nano:latest
llm-agent -m ollama:llama3.2                      # Ollama: any model by name
llm-agent -m ollama:deepseek-coder-v2:16b-lite-instruct-q4_0  # Ollama: with quantization tag

# Auto-approve safe commands
llm-agent -y
llm-agent --yolo

# Gemini thinking level (low/medium/high)
llm-agent -m gemini-pro --thinking high
llm-agent -m gemini-flash --thinking low -c "summarise this repo"

# Single-shot mode (non-interactive)
llm-agent -c "how much disk space is free?"
llm-agent -c "what's in /etc/hosts?" -m haiku --yolo

# Disable TUI, use readline REPL
llm-agent --no-tui

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
    cli.py              — main, arg parsing, REPL, run_question, setup_delegate
    config.py           — user config file (~/.config/llm-agent/config.toml)
    debug.py            — debug/trace logging (DebugLogger, _NoOpDebug, get_debug)
    models.py           — canonical model registry (aliases, providers, context windows, max tokens)
    persistence.py      — session persistence (save/load/list/find sessions)
    agent.py            — agent_turn, streaming, caching, retry logic (Anthropic)
    gemini_agent.py     — gemini_agent_turn, Gemini streaming + format conversion
    openai_agent.py     — openai_agent_turn, OpenAI streaming + format conversion
    ollama_agent.py     — ollama_agent_turn, Ollama via OpenAI-compat API
    agents.py           — subagent definitions, custom agent loading, run_subagent
    context.py          — project context detection (project type, git, .agent.md)
    skills.py           — skill parsing, discovery, rendering (/slash commands)
    mcp_client.py       — MCP client: server lifecycle, tool discovery, async bridge
    formatting.py       — colour helpers, output truncation, token formatting
    display.py          — Display protocol, get_display/set_display singleton
    tui.py              — Textual TUI app, TUIDisplay, PromptInput, light theme
    session.py          — Session class (conversation state, token tracking, command routing)
    system_prompt.txt   — system prompt (edit without touching Python)    tools/
        __init__.py     — collects TOOLS list + TOOL_REGISTRY, build_tool_set(), dispatch_tool_calls(), register/unregister_mcp_tools()
        base.py         — ShellState, BackgroundTask, _resolve, confirm_edit, COMMAND_TIMEOUT
        read_file.py    — SCHEMA + handle
        read_many_files.py — SCHEMA + handle
        list_directory.py — SCHEMA + handle
        search_files.py — SCHEMA + handle
        glob_files.py   — SCHEMA + handle
        file_outline.py — SCHEMA + handle (file structure with line numbers)
        lsp_navigate.py — SCHEMA + handle (optional local language-server navigation)
        read_url.py     — SCHEMA + handle
        web_search.py   — SCHEMA + handle
        write_file.py   — SCHEMA + handle
        edit_file.py    — SCHEMA + handle
        apply_patch.py  — SCHEMA + handle + NEEDS_CONFIRM + NEEDS_SEQUENTIAL
        run_command.py  — SCHEMA + handle + NEEDS_CONFIRM (supports run_in_background)
        check_task.py   — SCHEMA + handle (poll background tasks)
        start_session.py — SCHEMA + handle + NEEDS_CONFIRM (starts PTY session)
        write_stdin.py  — SCHEMA + handle + NEEDS_CONFIRM + NEEDS_SEQUENTIAL (interactive PTY I/O)
        delegate.py     — SCHEMA + handle (subagent delegation)
        ask_user.py     — SCHEMA + handle + NEEDS_SEQUENTIAL (user questions)
tests/                  — pytest test suite (see Testing section)
```

- Package name: `llm-agent` (import name: `llm_agent`)
- Console entry point: `llm-agent` command
- Version is defined in `llm_agent/__init__.py` (`VERSION`). `pyproject.toml` reads it dynamically — do not add a version there.

## Architecture

The agent is split across several modules:

- **`models.py`** — canonical model registry: aliases, provider detection, context windows, max output tokens, step limits. All other modules import from here.
- **`cli.py`** — entry point, arg parsing, REPL loop, selects agent turn function based on model
- **`agent.py`** — Anthropic streaming API calls, prompt caching, retry logic
- **`gemini_agent.py`** — Gemini streaming, tool schema conversion, message format conversion
- **`openai_agent.py`** — OpenAI streaming, tool schema/message format conversion
- **`ollama_agent.py`** — Ollama streaming via OpenAI-compatible API, usage estimation fallback
- **`mcp_client.py`** — MCP client manager: server lifecycle, tool discovery, async-to-sync bridge
- **`display.py`** — Display protocol abstracting all user-facing output (print/input)
- **`tui.py`** — Textual TUI application, `TUIDisplay`, `ReadlineInput`, light theme
- **`session.py`** — `Session` class managing conversation history, token usage, and `/slash` commands- **`formatting.py`** — ANSI colour helpers, output truncation, token formatting
- **`tools/`** — one file per tool, each exporting `SCHEMA`, `handle()`, and optional `LOG`/`NEEDS_CONFIRM`/`NEEDS_SEQUENTIAL`. `dispatch_tool_calls()` handles parallel execution of safe tools via `ThreadPoolExecutor`

The key flow:

1. **`main()`** (`cli.py`) — parses args, creates API client and `Session`, initializes MCP servers, dispatches to single-shot, TUI, or readline REPL mode
2. **`Session.run_question()`** (`session.py`) — runs a single user question to completion: calls `agent_turn`, `gemini_agent_turn`, or `openai_agent_turn` in a loop until the model produces a final answer, tracks token usage, and trims the conversation if needed.
3. **`agent_loop()`** (`cli.py`) — readline REPL that delegates to `Session.handle_command()` and `Session.run_question()` repeatedly
4. **`AgentApp`** (`tui.py`) — Textual TUI alternative to `agent_loop()`. Runs `Session.run_question()` in a worker thread, routes output via `TUIDisplay`5. **`agent_turn()`** (`agent.py`) — streams a single model API call, dispatches tool use via `TOOL_REGISTRY`, returns when the model produces a final text answer or requests tool results
6. **`TOOL_REGISTRY`** (`tools/__init__.py`) — auto-collected from tool modules; adding a new tool requires creating a tool file and adding one import line

## Tools

The model has eighteen tools. Read-only tools run without confirmation; mutating and interactive shell tools require confirmation.

**Read-only (no confirmation):**
- **`read_file`** — reads file contents with line numbers, supports `offset`/`limit` for paging, and tells the model what `offset` to use next when output is truncated. Reports total line count and file size.
- **`read_many_files`** — reads a small focused set of files in one tool call. Supports explicit `paths`, include/exclude glob patterns relative to an optional base `path`, plus per-file `offset`/`limit` and `max_files`.
- **`list_directory`** — lists directory entries with type indicators and file sizes. Supports optional `hidden`, recursive `depth`, and paginated `offset`/`limit`.
- **`search_files`** — regex search over file contents using ripgrep (falls back to grep). Supports glob filtering, file-only mode, surrounding context lines, per-file match caps, and a global result cap.
- **`glob_files`** — finds files matching a glob pattern recursively. Supports `**` for recursive matching, optional exclude patterns, hidden-file inclusion, and a result cap. Returns sorted relative paths.
- **`file_outline`** — shows the structure of a file (classes, functions, methods with line numbers) without reading the full content. Uses regex-based parsing for Python, JavaScript/TypeScript, Go, Rust, Java, Ruby, C/C++. Supports optional kind filtering and `max_symbols` truncation guidance.
- **`lsp_navigate`** — semantic code navigation via a local language server. Supports `document_symbols`, `definition`, `references`, and `hover`. Works when a compatible language server is installed locally for the file type (currently Python, JS/TS, Go, Rust).
- **`read_url`** — fetches a URL and returns cleaned content. HTML is converted to markdown; plain text, markdown, and JSON are returned as text. Returns title, final URL after safe redirects, content type, and content truncated to `max_length` (default 10k chars). http/https only, 1MB download cap.
- **`web_search`** — searches the web via provider-native search when available (Anthropic, OpenAI, Gemini), with DuckDuckGo HTML fallback. Returns numbered results with titles, URLs, and snippets. Default 8 results.
- **`check_task`** — polls background tasks started via `run_command` with `run_in_background: true` or `delegate` with `run_in_background: true`. Pass a `task_id` for shell-task status, PID, cwd, timestamps, runtime, and output, or for delegated-subagent status, model, usage, and final result; pass `tail_lines` to show only the last N lines of shell output; or omit `task_id` to list all tasks.

**Mutating (always require confirmation):**
- **`write_file`** — creates or overwrites a file. Shows a content preview and prompts `Apply? [Y/n]`. Creates parent directories automatically. Overwriting an existing file requires a fresh `read_file` in the current session; if the file changed after it was read, the overwrite is rejected until it is read again. Existing-file overwrites preserve encoding and newline style when possible, surface format metadata in the preview/result, and reject obvious omission placeholders such as `... existing code ...`.
- **`edit_file`** — targeted edit in an existing file. Three modes: (1) **string match** — `old_string` + `new_string`, must match uniquely (whitespace-normalized fuzzy match used as fallback), (2) **line range** — `start_line` + `end_line` + `new_string` to replace lines by number (1-based, inclusive), (3) **batch** — `edits` array of multiple operations applied atomically. Shows a `-`/`+` diff preview. Requires a fresh `read_file` in the current session, rejects stale files, preserves existing encoding/newlines when possible, surfaces summary metadata in the preview/result, and rejects obvious omission placeholders.
- **`apply_patch`** — structured multi-file mutation tool using a constrained grammar with `*** Begin Patch` / `*** End Patch`, `Add File`, `Delete File`, `Update File`, and optional `Move to` blocks. Existing-file changes require a fresh `read_file` in the current session, reuse the same stale-file checks as other mutation tools, and show one confirmation preview for the whole patch.
- **`run_command`** — arbitrary shell command execution. Prompts `Run? [Y/n]`. In yolo mode (`-y`), auto-approves unless the command matches dangerous patterns. Supports `run_in_background: true` to start long-running commands without blocking — returns a task ID plus task metadata for inspection via `check_task`.

**Interactive shell (always sequential; writes require confirmation):**
- **`start_session`** — starts a PTY-backed interactive command and returns a session ID plus initial output. Use this for REPLs, database shells, or commands that need multiple stdin writes. Interactive sessions keep their own cwd; `cd` inside one does not update the agent's global working directory.
- **`write_stdin`** — sends input to a PTY session started via `start_session`, polls for new output when `chars` is empty, or terminates the session with `close: true`. Non-empty writes and closes always prompt, even in yolo mode.

**Interactive (always sequential):**
- **`ask_user`** — asks the user a clarifying question. Supports the legacy single-question form plus one to three short structured questions with stable IDs, headers, and optional multiple-choice options. Always prompts, even in yolo mode. Not available to subagents. Marked `NEEDS_SEQUENTIAL` so it always runs on the main thread.

**Delegation (no confirmation):**
- **`delegate`** — spawns a subagent with its own conversation, filtered tool set, and optional model override. No confirmation needed (the subagent's own tools handle it). Delegated runs return agent/model/status/step metadata plus the final subagent result, including the configured `max_steps` budget, and the display layer logs start/progress/done status lines. `run_in_background: true` starts the subagent in a daemon thread and returns a task ID for `check_task`. Two built-in agents: `explore` (read-only, haiku) and `code` (full tools, inherits model). Both now default to `100` steps. Both include `read_many_files`, `file_outline`, and `lsp_navigate` alongside the existing navigation tools. Custom agents can be defined via JSON files in `~/.agents/` or `.agents/`.

## MCP Client Support

The agent can connect to external [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) servers, making their tools available alongside built-in tools across all providers (Anthropic, Gemini, OpenAI).

**Installation:** `pip install -e '.[mcp]'` (or `'.[all]'`). Without the `mcp` package, MCP features are silently unavailable.

**Configuration:** Uses the same `mcpServers` format as Claude Desktop, read from two locations (project-level takes priority):
- `.mcp.json` (project root)
- `~/.mcp.json` (user-level)

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
      "env": {}
    }
  }
}
```

**Architecture:** The MCP Python SDK is fully async, but the agent runs synchronously in threads. `MCPManager` solves this with a dedicated asyncio event loop in a daemon thread. Sync wrappers submit coroutines via `asyncio.run_coroutine_threadsafe()` and block for results — this is thread-safe and works with the parallel `dispatch_tool_calls()` executor.

**Tool registration:** MCP tools are added to the global `TOOLS` and `TOOL_REGISTRY` via `register_mcp_tools()`. This means `build_tool_set()` includes them automatically, so subagents get MCP tools by default. Subagent definitions can reference MCP tool names in their `"tools"` list to include/exclude specific ones.

**Name collisions:** If an MCP tool has the same name as a built-in tool or a tool from another server, it is skipped with a warning log (first registration wins).

**Lifecycle:**
1. `main()` calls `load_mcp_config()` → `MCPManager.start(config)` after `setup_delegate()` and `refresh_project_context()`
2. On startup: connects to all servers via stdio, discovers tools, registers them in the global registry
3. During operation: `call_tool()` dispatches to the correct server session
4. On exit: `MCPManager.stop()` closes all sessions, stops the event loop, and unregisters MCP tools

**Key files:**
- `mcp_client.py` — `MCPManager`, `load_mcp_config()`, `get_mcp_manager()`, schema/result conversion
- `tools/__init__.py` — `register_mcp_tools()`, `unregister_mcp_tools()`
- `cli.py` — MCP startup in `main()`, cleanup via `_stop_mcp()` in all exit paths
- `tui.py` — MCP cleanup before `os._exit(0)`

## Subagent System

The `delegate` tool lets the model spawn child agents for subtasks. Each subagent gets:
- A **fresh conversation** (isolated from the parent)
- A **filtered tool set** (e.g. read-only for `explore`)
- An optional **model override** (e.g. haiku for speed)
- An optional **custom system prompt**
- **No access to `delegate`** itself (no nesting) or **`ask_user`** (cannot prompt the user)

**Built-in agents:**
- `explore` — read-only tools, uses haiku, research-focused system prompt, `max_steps: 100`
- `code` — all tools except delegate and ask_user, inherits parent model and system prompt, `max_steps: 100`

**Custom agents:** Place `.json` files in `~/.agents/` (user-level) or `.agents/` (project-level, higher priority). Each defines `name`, `description`, optional `model`, optional `tools` list, optional `system_prompt`, and optional `max_steps` (positive integer; `max_turns` is also accepted as an alias). The `delegate` and `ask_user` tools are always excluded from subagent tool lists.

**Key files:**
- `agents.py` — `BUILTIN_AGENTS`, `load_all_agents()`, `run_subagent()`
- `tools/delegate.py` — tool schema and handler (calls `_run_subagent` callback)
- `tools/__init__.py` — `build_tool_set()` for filtering tools
- `cli.py` — `setup_delegate()` wires the callback and updates the tool description

## Skill System

Skills are reusable prompt templates invoked as `/slash` commands in interactive mode. They let users define project-specific workflows (code review, issue fixing, deployment checklists) as markdown files.

**Discovery:** Skills are loaded from `~/.skills/` (user-level) and `.skills/` (project-level, higher priority). Each skill lives in a subdirectory containing a `SKILL.md` file:

```
.skills/
    review/
        SKILL.md
    deploy-check/
        SKILL.md
```

**File format:** YAML frontmatter with `---` delimiters, followed by a prompt body:

```yaml
---
name: review
description: Code review a file for bugs and style issues
argument-hint: <filepath>
---
Review the following file for bugs, style issues, and improvements.

File: $0
Branch: !`git branch --show-current`
```

**Frontmatter fields:** `name` (required), `description`, `argument-hint`.

**Variable substitution:** `$ARGUMENTS` expands to the full args string. `$0`, `$1`, etc. expand to positional args (space-split).

**Dynamic injection:** Lines matching `` !`command` `` are replaced with the command's stdout (runs in the agent's working directory, 5-second timeout).

**Interactive commands:**
- `/skills` — list available skills
- `/name [args]` — invoke a skill (e.g. `/review src/main.py`)
- `/mcp` — list connected MCP servers and their tools

Built-in commands (`/clear`, `/copy`, `/mcp`, `/model`, `/sessions`, `/thinking`, `/version`) cannot be shadowed by skills. `/copy` is TUI-only (copies last response to clipboard).

**Key files:**
- `skills.py` — `parse_skill()`, `load_all_skills()`, `render_skill()`, `format_skill_list()`
- `cli.py` — loads skills in `agent_loop()`, routes `/name` commands to skills

### Bundled Skills

The project ships 10 skills in `.skills/`, adapted from [anthropics/skills](https://github.com/anthropics/skills) for llm-agent's tool names and environment:

**Document skills:**
- `/pdf` — PDF reading, extraction, merging, splitting, creation, form filling. Dependencies: `pypdf`, `pdfplumber`, `reportlab`. Note: the agent's `read_file` tool handles basic PDF reading natively.
- `/xlsx` — Spreadsheet creation, editing, analysis with formulas and formatting. Dependencies: `openpyxl`, `pandas`. Includes a zero-dependency stdlib fallback for reading. Note: `read_file` cannot handle binary `.xlsx` files.
- `/docx` — Word document creation (via docx-js/Node.js), editing (unzip → edit XML → rezip), tracked changes, comments. Dependencies: `pandoc`, `npm install -g docx`, LibreOffice (optional).
- `/pptx` — Presentation reading, editing, creation, with design guidelines and QA workflow. Dependencies: `markitdown[pptx]`, `python-pptx`. Includes a zero-dependency stdlib fallback for reading. Note: `read_file` cannot handle binary `.pptx` files.
- `/doc-coauthoring` — Structured workflow for co-authoring documentation through context gathering, iterative refinement, and reader testing via subagent delegation.

**Development skills:**
- `/mcp-builder` — Guide for creating MCP (Model Context Protocol) servers in TypeScript or Python, with research, implementation, testing, and evaluation phases.
- `/webapp-testing` — Playwright-based web application testing with server lifecycle management, reconnaissance patterns, and form interaction examples. Dependencies: `playwright`.
- `/web-artifacts-builder` — React + TypeScript + Vite + Tailwind + shadcn/ui project scaffolding and single-HTML bundling.
- `/skill-creator` — Guide for creating new skills for llm-agent, covering the SKILL.md format, variable substitution, dynamic injection, and design principles.

**Other:**
- `/imagegen` — Gemini image generation via the `gemini-imagegen` CLI.

## Display Protocol

All user-facing output goes through a `Display` protocol (`display.py`), accessed via a module-level singleton (`get_display()` / `set_display()`). This decouples output from `print()`/`input()` calls, allowing the TUI to route output to widgets without changing tool or agent code.

**`Display` methods:**
- `stream_start()` / `stream_token(text)` / `stream_end()` — model response streaming
- `tool_log(message)` — tool invocation logging (already ANSI-formatted)
- `tool_result(line_count)` — tool output summary
- `confirm(preview_lines, prompt_text) -> bool` — show preview, ask Y/n
- `ask_user(question, choices=None) -> str | dict[str, str]` — ask a clarifying question (legacy single question) or a short structured set of questions
- `auto_approved(preview_lines)` — show preview for auto-approved actions
- `status(message)` / `error(message)` / `info(message)` — output categories

The default `Display` class prints to stdout — identical to the original readline behaviour. `TUIDisplay` overrides these methods to route output to Textual widgets via `call_from_thread()`.

**Key files:**
- `display.py` — `Display` base class, `get_display()`, `set_display()`
- `tui.py` — `TUIDisplay(Display)` subclass

## TUI (Textual)

Interactive mode uses a Textual-based TUI by default (falls back to readline if `textual` is not installed, or when `--no-tui` is passed). Single-shot mode (`-c`) always uses the default `Display`.

**Layout:**
```
+-------------------------------------------+
|     RichLog (scrollable conversation)     |
+━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━+
| > [PromptInput — wraps, auto-grows]       |
+━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━+
| model (mode) |   token usage  | context % |
+-------------------------------------------+
```

**Key components:**
- **`AgentApp(App)`** — main Textual app; composes `RichLog`, `PromptInput`, status bar
- **`TUIDisplay(Display)`** — routes all output to widgets via `call_from_thread()`
- **`PromptInput(TextArea)`** — wrapping, auto-growing input with Emacs/readline keybindings (Ctrl+A/E/F/B/D/K/U/W/H). Enter submits, Shift+Enter inserts newline. Auto-grows up to 8 lines.
- **Worker thread** — `run_question()` runs synchronously in a Textual `@work(thread=True)` worker
- **Light theme** — white background, green accents, light gray status bar (`agent-light`)

**Confirmation flow (TUI):**
1. Worker calls `TUIDisplay.confirm()` → posts preview to RichLog, switches input to Y/n mode
2. Worker blocks on `threading.Event.wait()`
3. User presses y/n/Enter → app signals the event
4. Worker resumes with the result

**Ask flow (TUI):** Same threading pattern as confirmation — `TUIDisplay.ask_user()` writes each question to RichLog or a selection panel, blocks on a separate `threading.Event`, and returns either a single answer or an ID-keyed answer mapping for structured prompts.

**Streaming:** Tokens are accumulated in a buffer during streaming. The full response is written to `RichLog` as a single `write()` call on `stream_end()` (each `write()` creates an independent wrapping block, so per-batch writes would cause narrow paragraphs). A `~` indicator replaces the `>` prompt marker during streaming.

**Status bar:** Three `Static` widgets in a `Horizontal` container:
- Left: model name and mode (confirm/YOLO)
- Centre: turn token counts + session totals
- Right: context window remaining percentage

Updated after each agent turn via `_update_status_bar()`.

**Copying text:**
- `/copy` — copies the last assistant response to the system clipboard
- **Shift+click-drag** — bypasses Textual's mouse capture for terminal-native text selection (works in iTerm2, Terminal.app, etc.)
- Textual's built-in mouse selection does not work with `RichLog` (virtual `ScrollView` rendering)

**Keyboard shortcuts:**
- Ctrl+C / Ctrl+Q — quit the app
- Ctrl+D — press twice on empty input to quit; delete character under cursor otherwise

**Key files:**
- `tui.py` — `AgentApp`, `TUIDisplay`, `ReadlineInput`, `LIGHT_THEME`, `run_tui()`
- `cli.py` — `--no-tui` flag, TUI launch path with `ImportError` fallback

## Key behaviours

- **Working directory tracking** — `ShellState` tracks `cwd` across commands (like Claude Code: working directory persists, other shell state does not). All file-based tools resolve relative paths against it via `_resolve()`
- **Streaming** — model responses stream to the terminal as they're generated. In TUI mode, tokens are accumulated and rendered as a single block on completion.
- **Thinking levels** — Gemini models support `--thinking low|medium|high` to control reasoning depth. `gemini-pro` defaults to `high`; other models default to off. In interactive mode, `/thinking` shows or changes the level (`/thinking off` resets to no thinking config). Has no effect on Anthropic models.
- **Readline** — Emacs-style line editing and persistent history (`~/.agent_history`, 1000 entries) in both TUI and readline REPL modes
- **Prompt caching** — system prompt, tool definitions, and conversation prefix are cached across API calls to reduce cost and latency
- **Token tracking** — per-turn and session totals printed after each answer (to stderr in `-c` mode for clean piping), includes cache hit stats
- **Token-budget conversation trimming** — after each question, if the API-reported input token count exceeds 80% of the model's context window, the oldest message rounds are trimmed to bring usage under budget. Before discarding, the dropped messages are summarized by the model and the summary is prepended as a `[Earlier context summary]` message so the agent retains key decisions and findings.
- **Project context auto-detection** — at startup, the system prompt is augmented with project context detected from the working directory: project type (from `pyproject.toml`, `package.json`, `Cargo.toml`, etc.), git branch/status/recent commits, and `AGENTS.md` convention file if present. This is handled by `context.py` → `agent.py:refresh_project_context()`.
- **File attachments** — use `@filepath` in prompts to attach images (png, jpg, jpeg, gif, webp) or PDFs. The `@` must be at the start of a word (so `user@email.com` is left alone). Works in both interactive and `-c` mode. Attachments are base64-encoded and sent as multimodal content blocks.
- **Output truncation** — command output over 200 lines is cut to first/last 100 lines
- **Parallel tool execution** — when the model emits multiple tool calls in one response, `dispatch_tool_calls()` runs read-only and auto-approved tools concurrently via `ThreadPoolExecutor(max_workers=4)`. Tools requiring confirmation run sequentially in the main thread. Tools marked `NEEDS_SEQUENTIAL` (e.g. `ask_user`) always run sequentially regardless of auto-approve mode. All three provider modules share this dispatch function.
- **MCP tool servers** — external tools from MCP servers (configured via `.mcp.json`) are registered in the global tool registry at startup and available to the model alongside built-in tools. The `mcp` package is optional; without it, MCP features are silently skipped.

## Model Names

Vertex AI Anthropic models use bare names without `@date` suffixes: `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-5`.

Gemini model aliases: `gemini-flash` → `gemini-2.5-flash`, `gemini-pro` → `gemini-3.1-pro-preview`.

OpenAI model aliases pass through directly: `gpt-4o`, `gpt-4o-mini`, `gpt-5.2`, `o3`, `o4-mini`.

Ollama models use `ollama:` prefix: `ollama:qwen3.5:122b`, `ollama:mistral`, etc. Aliases: `qwen3` → `ollama:qwen3.5:122b`, `qwen3-cloud` → `ollama:qwen3.5:cloud`, `qwen3-coder` → `ollama:qwen3.5:35b-a3b-coding-nvfp4`, `gemma4-31b` → `ollama:gemma4:31b`, `nemotron-nano` → `ollama:nemotron-3-nano:latest`.

## Provider Architecture

Messages are stored internally in Anthropic format. Provider-specific modules convert at the API boundary:

**Gemini** (`gemini_agent.py`):
- `role: "assistant"` → `role: "model"`
- `tool_use` blocks → `FunctionCall` parts
- `tool_result` blocks → `FunctionResponse` parts (tool name stashed in `_name` field)

**OpenAI** (`openai_agent.py`):
- System prompt → `role: "developer"` message (works for both standard and reasoning models)
- `tool_use` blocks → `tool_calls` array on assistant message (arguments as JSON string)
- `tool_result` blocks → separate `role: "tool"` messages with `tool_call_id`
- `input_schema` → `parameters` in function tool format
- Reasoning models (`gpt-5.2`, `o3`, `o4-mini`) use `max_completion_tokens` instead of `max_tokens`

**Ollama** (`ollama_agent.py`):
- Reuses OpenAI message/tool conversion functions (Ollama exposes an OpenAI-compatible API)
- Client created with `openai.OpenAI(api_key="ollama", base_url="http://localhost:11434/v1")`
- `ollama:` prefix stripped before sending model name to the API
- Falls back to token estimation when Ollama doesn't report usage stats
- Helpful error messages when Ollama server is unreachable

Provider SDKs (`google-genai`, `openai`) are lazy-imported only when the corresponding model is selected. Switching providers via `/model` recreates the client and clears the conversation.
