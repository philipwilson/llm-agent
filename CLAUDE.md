# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Python agent loop that uses LLMs to answer user questions by exploring the filesystem and running Unix commands. Supports Anthropic Claude (direct API and Vertex AI), Google Gemini, and OpenAI models.

## Installation

```bash
pip install -e .              # editable install
pip install -e '.[vertex]'    # with Vertex AI support
pip install -e '.[gemini]'    # with Gemini support
pip install -e '.[openai]'    # with OpenAI support
pip install -e '.[tui]'       # with Textual TUI
pip install -e '.[all]'       # all providers + TUI
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

# Option 4: OpenAI
export OPENAI_API_KEY="your-openai-api-key"

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
    agent.py            — agent_turn, streaming, caching, retry logic (Anthropic)
    gemini_agent.py     — gemini_agent_turn, Gemini streaming + format conversion
    openai_agent.py     — openai_agent_turn, OpenAI streaming + format conversion
    agents.py           — subagent definitions, custom agent loading, run_subagent
    skills.py           — skill parsing, discovery, rendering (/slash commands)
    formatting.py       — colour helpers, output truncation, token formatting
    display.py          — Display protocol, get_display/set_display singleton
    tui.py              — Textual TUI app, TUIDisplay, ReadlineInput, light theme
    system_prompt.txt   — system prompt (edit without touching Python)
    tools/
        __init__.py     — collects TOOLS list + TOOL_REGISTRY, build_tool_set()
        base.py         — ShellState, _resolve, confirm_edit, COMMAND_TIMEOUT
        read_file.py    — SCHEMA + handle
        list_directory.py — SCHEMA + handle
        search_files.py — SCHEMA + handle
        glob_files.py   — SCHEMA + handle
        read_url.py     — SCHEMA + handle
        web_search.py   — SCHEMA + handle
        write_file.py   — SCHEMA + handle
        edit_file.py    — SCHEMA + handle
        run_command.py  — SCHEMA + handle + NEEDS_CONFIRM
        delegate.py     — SCHEMA + handle (subagent delegation)
```

- Package name: `llm-agent` (import name: `llm_agent`)
- Console entry point: `llm-agent` command
- Version is defined in `llm_agent/__init__.py` (`VERSION`). `pyproject.toml` reads it dynamically — do not add a version there.

## Architecture

The agent is split across several modules:

- **`cli.py`** — entry point, arg parsing, REPL loop, selects agent turn function based on model
- **`agent.py`** — Anthropic streaming API calls, prompt caching, retry logic
- **`gemini_agent.py`** — Gemini streaming, tool schema conversion, message format conversion
- **`openai_agent.py`** — OpenAI streaming, tool schema/message format conversion
- **`display.py`** — Display protocol abstracting all user-facing output (print/input)
- **`tui.py`** — Textual TUI application, `TUIDisplay`, `ReadlineInput`, light theme
- **`formatting.py`** — ANSI colour helpers, output truncation, token formatting
- **`tools/`** — one file per tool, each exporting `SCHEMA`, `handle()`, and optional `LOG`/`NEEDS_CONFIRM`

The key flow:

1. **`main()`** (`cli.py`) — parses args (`-m`, `-y`, `-c`, `--thinking`, `--no-tui`), creates API client, dispatches to single-shot, TUI, or readline REPL mode
2. **`run_question()`** (`cli.py`) — runs a single user question to completion: calls `agent_turn`, `gemini_agent_turn`, or `openai_agent_turn` in a loop until the model produces a final answer, with `MAX_STEPS` guard and Ctrl+C handling
3. **`agent_loop()`** (`cli.py`) — readline REPL that calls `run_question` repeatedly, maintains conversation history, session-level token stats, and skill routing
4. **`AgentApp`** (`tui.py`) — Textual TUI alternative to `agent_loop()`. Runs `run_question()` in a worker thread, routes output via `TUIDisplay`
5. **`agent_turn()`** (`agent.py`) — streams a single model API call, dispatches tool use via `TOOL_REGISTRY`, returns when the model produces a final text answer or requests tool results
6. **`TOOL_REGISTRY`** (`tools/__init__.py`) — auto-collected from tool modules; adding a new tool requires creating a tool file and adding one import line

## Tools

The model has ten tools. Read-only tools run without confirmation; mutating tools always require it.

**Read-only (no confirmation):**
- **`read_file`** — reads file contents with line numbers, supports `offset`/`limit` for paging. Reports total line count and file size.
- **`list_directory`** — lists directory entries with type indicators and file sizes. Optional `hidden` flag.
- **`search_files`** — regex search over file contents using ripgrep (falls back to grep). Supports glob filtering and result cap.
- **`glob_files`** — finds files matching a glob pattern recursively using Python's `glob.glob()`. Supports `**` for recursive matching. Returns sorted relative paths, capped at 200 results by default.
- **`read_url`** — fetches a web page via curl, converts HTML to plain text via lynx/w3m (regex fallback). Returns title, final URL, and content truncated to `max_length` (default 10k chars). http/https only, 1MB download cap.
- **`web_search`** — searches the web via DuckDuckGo HTML (no API key needed). Returns numbered results with titles, URLs, and snippets. Default 8 results.

**Mutating (always require confirmation):**
- **`write_file`** — creates or overwrites a file. Shows a content preview and prompts `Apply? [Y/n]`. Creates parent directories automatically.
- **`edit_file`** — targeted find-and-replace in an existing file. `old_string` must match exactly once (fails if not found or ambiguous). Shows a `-`/`+` diff preview.
- **`run_command`** — arbitrary shell command execution. Prompts `Run? [Y/n]`. In yolo mode (`-y`), auto-approves unless the command matches `DANGEROUS_PATTERNS`.

**Delegation (no confirmation):**
- **`delegate`** — spawns a subagent with its own conversation, filtered tool set, and optional model override. No confirmation needed (the subagent's own tools handle it). Two built-in agents: `explore` (read-only, haiku) and `code` (full tools, inherits model). Custom agents can be defined via JSON files in `~/.agents/` or `.agents/`.

## Subagent System

The `delegate` tool lets the model spawn child agents for subtasks. Each subagent gets:
- A **fresh conversation** (isolated from the parent)
- A **filtered tool set** (e.g. read-only for `explore`)
- An optional **model override** (e.g. haiku for speed)
- An optional **custom system prompt**
- **No access to `delegate`** itself (no nesting)

**Built-in agents:**
- `explore` — read-only tools, uses haiku, research-focused system prompt
- `code` — all tools except delegate, inherits parent model and system prompt

**Custom agents:** Place `.json` files in `~/.agents/` (user-level) or `.agents/` (project-level, higher priority). Each defines `name`, `description`, optional `model`, optional `tools` list, optional `system_prompt`. The `delegate` tool is always excluded from subagent tool lists.

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

Built-in commands (`/clear`, `/copy`, `/model`, `/thinking`, `/version`) cannot be shadowed by skills. `/copy` is TUI-only (copies last response to clipboard).

**Key files:**
- `skills.py` — `parse_skill()`, `load_all_skills()`, `render_skill()`, `format_skill_list()`
- `cli.py` — loads skills in `agent_loop()`, routes `/name` commands to skills

## Display Protocol

All user-facing output goes through a `Display` protocol (`display.py`), accessed via a module-level singleton (`get_display()` / `set_display()`). This decouples output from `print()`/`input()` calls, allowing the TUI to route output to widgets without changing tool or agent code.

**`Display` methods:**
- `stream_start()` / `stream_token(text)` / `stream_end()` — model response streaming
- `tool_log(message)` — tool invocation logging (already ANSI-formatted)
- `tool_result(line_count)` — tool output summary
- `confirm(preview_lines, prompt_text) -> bool` — show preview, ask Y/n
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
| > [ReadlineInput]                         |
+━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━+
| model (mode) |   token usage  | context % |
+-------------------------------------------+
```

**Key components:**
- **`AgentApp(App)`** — main Textual app; composes `RichLog`, `ReadlineInput`, status bar
- **`TUIDisplay(Display)`** — routes all output to widgets via `call_from_thread()`
- **`ReadlineInput(Input)`** — Emacs/readline keybindings (Ctrl+A/E/F/B/D/K/U/W/H/P/N)
- **Worker thread** — `run_question()` runs synchronously in a Textual `@work(thread=True)` worker
- **Light theme** — white background, green accents, light gray status bar (`agent-light`)

**Confirmation flow (TUI):**
1. Worker calls `TUIDisplay.confirm()` → posts preview to RichLog, switches input to Y/n mode
2. Worker blocks on `threading.Event.wait()`
3. User presses y/n/Enter → app signals the event
4. Worker resumes with the result

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
- Ctrl+D — quit when input is empty; delete character under cursor otherwise (standard Unix EOF behaviour)

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
- **Token-budget conversation trimming** — after each question, if the API-reported input token count exceeds 80% of the model's context window, the oldest message rounds are trimmed to bring usage under budget. This replaces a fixed message-count limit with a budget that adapts to both model capacity and actual message sizes.
- **File attachments** — use `@filepath` in prompts to attach images (png, jpg, jpeg, gif, webp) or PDFs. The `@` must be at the start of a word (so `user@email.com` is left alone). Works in both interactive and `-c` mode. Attachments are base64-encoded and sent as multimodal content blocks.
- **Output truncation** — command output over 200 lines is cut to first/last 100 lines

## Model Names

Vertex AI Anthropic models use bare names without `@date` suffixes: `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-5`.

Gemini model aliases: `gemini-flash` → `gemini-2.5-flash`, `gemini-pro` → `gemini-3.1-pro-preview`.

OpenAI model aliases pass through directly: `gpt-4o`, `gpt-4o-mini`, `gpt-5.2`, `o3`, `o4-mini`.

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

Provider SDKs (`google-genai`, `openai`) are lazy-imported only when the corresponding model is selected. Switching providers via `/model` recreates the client and clears the conversation.
