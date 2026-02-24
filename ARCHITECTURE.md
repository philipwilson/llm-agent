# llm-agent — Features & Architecture

A terminal-based AI agent that answers questions by exploring your filesystem, running shell commands, and searching the web. Supports Anthropic Claude (direct API and Vertex AI), Google Gemini, and OpenAI models.

**Version:** 0.14.0 · **License:** MIT · **Python:** ≥ 3.9

---

## Table of Contents

- [Features](#features)
- [Supported Models](#supported-models)
- [Installation & Setup](#installation--setup)
- [Usage](#usage)
- [Interactive Commands](#interactive-commands)
- [Tools](#tools)
- [Package Structure](#package-structure)
- [Architecture](#architecture)
  - [The Agent Loop](#the-agent-loop)
  - [Key Functions](#key-functions)
  - [Provider Architecture](#provider-architecture)
  - [Display Protocol](#display-protocol)
  - [TUI (Textual)](#tui-textual)
- [Working Directory Tracking](#working-directory-tracking)
- [Prompt Caching](#prompt-caching)
- [Token Tracking & Context Trimming](#token-tracking--context-trimming)
- [Parallel Tool Execution](#parallel-tool-execution)
- [MCP Client Support](#mcp-client-support)
- [Project Context Auto-Detection](#project-context-auto-detection)
- [Subagent System](#subagent-system)
- [Skill System](#skill-system)
- [File Attachments](#file-attachments)
- [Error Handling & Retry Logic](#error-handling--retry-logic)
- [Safety](#safety)
- [Extending the Agent](#extending-the-agent)

---

## Features

| Feature | Description |
|---------|-------------|
| **Multi-provider** | Anthropic Claude (direct + Vertex AI), Google Gemini, OpenAI — switch mid-session with `/model` |
| **Streaming** | Responses stream to the terminal as they're generated |
| **Interactive TUI** | Textual-based terminal UI (light theme) with readline fallback |
| **11+ tools** | Read files, search code, browse the web, edit files, run commands, delegate to subagents, plus external MCP tools |
| **Working directory tracking** | `cd` in one command persists to the next |
| **Prompt caching** | System prompt and conversation prefix cached across Anthropic API calls |
| **Token tracking** | Per-turn and session totals after each answer, cache hit stats |
| **Context trimming** | Oldest messages summarised and dropped when nearing context window limits |
| **File attachments** | `@filepath` syntax for images (PNG, JPG, GIF, WebP) and PDFs |
| **Skills** | Reusable prompt templates invoked as `/slash` commands |
| **Subagent delegation** | Spawn child agents for read-only research or full coding tasks |
| **MCP tool servers** | Connect to external MCP servers for additional tools (filesystem, databases, APIs, etc.) |
| **Parallel tool calls** | Read-only tools execute concurrently via thread pool |
| **Output truncation** | Command output over 200 lines trimmed to first/last 100 lines |
| **Readline history** | Persistent history (`~/.agent_history`, 1000 entries) with Emacs keybindings |
| **Project context** | Auto-detects project type, git status, and `AGENTS.md` instructions at startup |
| **Thinking levels** | Gemini models support `--thinking low|medium|high` for reasoning depth control |

---

## Supported Models

| Alias | Full Model Name | Provider | Context Window | Max Output Tokens |
|-------|----------------|----------|---------------:|------------------:|
| `sonnet` (default) | claude-sonnet-4-6 | Anthropic | 200k | 64k |
| `opus` | claude-opus-4-6 | Anthropic | 200k | 128k |
| `haiku` | claude-haiku-4-5 | Anthropic | 200k | 64k |
| `gemini-flash` | gemini-2.5-flash | Google | 1M | (model default) |
| `gemini-pro` | gemini-3.1-pro-preview | Google | 1M | (model default) |
| `gpt-4o` | gpt-4o | OpenAI | 128k | 16k |
| `gpt-4o-mini` | gpt-4o-mini | OpenAI | 128k | 16k |
| `gpt-5.2` | gpt-5.2 | OpenAI | 400k | 128k |
| `o3` | o3 | OpenAI | 200k | 100k |
| `o4-mini` | o4-mini | OpenAI | 200k | 100k |

---

## Installation & Setup

```bash
pip install -e .              # base (Anthropic direct API)
pip install -e '.[vertex]'    # + Vertex AI
pip install -e '.[gemini]'    # + Gemini
pip install -e '.[openai]'    # + OpenAI
pip install -e '.[tui]'       # + Textual TUI
pip install -e '.[mcp]'       # + MCP tool servers
pip install -e '.[all]'       # everything
```

Configure at least one provider via environment variables:

```bash
export ANTHROPIC_API_KEY="..."              # Anthropic direct
export ANTHROPIC_VERTEX_PROJECT_ID="..."    # Anthropic via Vertex AI
export CLOUD_ML_REGION="us-east5"           # Vertex region (optional)
export GOOGLE_API_KEY="..."                 # Gemini
export OPENAI_API_KEY="..."                 # OpenAI
```

If both `ANTHROPIC_API_KEY` and `ANTHROPIC_VERTEX_PROJECT_ID` are set, the direct API takes priority.

---

## Usage

```bash
llm-agent                                    # interactive (default: sonnet)
llm-agent -m opus                            # choose model
llm-agent -c "how much disk space is free?"  # single-shot
llm-agent -y                                 # auto-approve safe commands (yolo mode)
llm-agent -m gemini-pro --thinking high      # Gemini thinking level
llm-agent --no-tui                           # readline REPL instead of TUI
llm-agent -c "@photo.png what's in this?"    # attach files
llm-agent -t 60                              # 60-second command timeout
```

---

## Interactive Commands

| Command | Description |
|---------|-------------|
| `/model <name>` | Switch model mid-session (clears history if provider changes) |
| `/thinking [low\|medium\|high\|off]` | Show or set Gemini thinking level |
| `/skills` | List available skills |
| `/name [args]` | Invoke a skill (e.g. `/review src/main.py`) |
| `/clear` | Clear conversation history |
| `/copy` | Copy last assistant response to clipboard (TUI only) |
| `/version` | Show version and current model |
| `Ctrl+C` | Cancel current response |
| `Ctrl+D` | Exit (or delete char under cursor if input is non-empty) |

---

## Tools

The agent has **11 tools** it can use autonomously. Read-only tools run without confirmation; mutating tools prompt before executing.

### Read-Only (no confirmation)

| Tool | Description |
|------|-------------|
| `read_file` | Read file contents with line numbers. Supports `offset`/`limit` for paging. Reports total line count and file size. |
| `list_directory` | List directory entries with type indicators and human-readable file sizes. Optional `hidden` flag. |
| `search_files` | Regex search over file contents using ripgrep (fallback: grep). Supports glob filtering and result cap. Respects `.gitignore`, skips binary files. |
| `glob_files` | Find files matching a glob pattern recursively. Supports `**` for recursive matching. Returns sorted relative paths capped at 200. |
| `file_outline` | Show file structure (classes, functions, methods with line numbers) without reading full content. Supports Python, JS/TS, Go, Rust, Java, Ruby, C/C++. |
| `read_url` | Fetch a web page, convert HTML to plain text. Returns title, final URL, and content truncated to `max_length` (default 10k chars). 1MB download cap. |
| `web_search` | Search the web via DuckDuckGo HTML (no API key). Returns titles, URLs, and snippets. Default 8 results. |

### Mutating (always require confirmation)

| Tool | Description |
|------|-------------|
| `write_file` | Create or overwrite a file. Shows content preview, prompts `Apply? [Y/n]`. Creates parent directories automatically. |
| `edit_file` | Targeted edit in an existing file. Three modes: **(1)** string match — `old_string` + `new_string` (must match uniquely, whitespace-normalised fuzzy fallback), **(2)** line range — `start_line` + `end_line` + `new_string`, **(3)** batch — `edits` array of multiple operations applied atomically. Shows diff preview. |
| `run_command` | Execute a shell command. Prompts `Run? [Y/n]`. In yolo mode (`-y`), auto-approves unless the command matches dangerous patterns. |

### Delegation (no confirmation)

| Tool | Description |
|------|-------------|
| `delegate` | Spawn a subagent with its own conversation, filtered tool set, and optional model override. Built-in agents: `explore` (read-only, haiku) and `code` (full tools, inherits model). |

---

## Package Structure

```
pyproject.toml                 # package metadata, entry point, optional deps
llm_agent/
    __init__.py                # VERSION = "0.14.0"
    cli.py                     # main(), arg parsing, REPL, run_question()
    agent.py                   # agent_turn() — Anthropic streaming + retry
    gemini_agent.py            # gemini_agent_turn() — Gemini streaming + format conversion
    openai_agent.py            # openai_agent_turn() — OpenAI streaming + format conversion
    agents.py                  # subagent definitions, run_subagent()
    context.py                 # project context detection (project type, git, AGENTS.md)
    skills.py                  # skill parsing, discovery, rendering
    mcp_client.py              # MCP client: server lifecycle, tool discovery, async bridge
    formatting.py              # ANSI colours, output truncation, token formatting
    display.py                 # Display protocol, get_display()/set_display() singleton
    tui.py                     # Textual TUI app, TUIDisplay, PromptInput
    system_prompt.txt          # system prompt template
    tools/
        __init__.py            # TOOLS list, TOOL_REGISTRY, dispatch_tool_calls(), build_tool_set(), register/unregister_mcp_tools()
        base.py                # ShellState (cwd tracking), _resolve(), COMMAND_TIMEOUT
        read_file.py           # SCHEMA + handle
        list_directory.py      # SCHEMA + handle
        search_files.py        # SCHEMA + handle
        glob_files.py          # SCHEMA + handle
        file_outline.py        # SCHEMA + handle
        read_url.py            # SCHEMA + handle
        web_search.py          # SCHEMA + handle
        write_file.py          # SCHEMA + handle
        edit_file.py           # SCHEMA + handle
        run_command.py         # SCHEMA + handle + NEEDS_CONFIRM + DANGEROUS_PATTERNS
        delegate.py            # SCHEMA + handle (subagent delegation)
.skills/                       # 10 bundled skills (see Skill System section)
```

---

## Architecture

### The Agent Loop

```
main() — cli.py
├── parse args (-m, -y, -c, --thinking, --no-tui, -t)
├── make_client(model) → Anthropic / Gemini / OpenAI client
├── setup_delegate() → wire subagent callback
├── refresh_project_context() → detect project type, git, AGENTS.md
├── load_mcp_config() → connect to MCP servers, register tools
└── dispatch to:
    ├── single-shot: run_question() then exit
    ├── readline REPL: agent_loop() → run_question() in a loop
    └── Textual TUI: run_tui() → AgentApp → run_question() in worker thread

run_question(client, model, conversation, user_input, ...)
├── parse_attachments() → extract @filepath as base64 content blocks
├── append user message to conversation
└── loop (up to MAX_STEPS=20):
    ├── call agent_turn() / gemini_agent_turn() / openai_agent_turn()
    ├── if model returned final text (no tool calls): break
    ├── dispatch_tool_calls() → execute tools, collect results
    └── append tool results, continue

agent_turn(client, model, messages, ...) — one API call
├── stream response via provider SDK
├── accumulate content blocks (text + tool_use)
├── if tool_use blocks present:
│   ├── dispatch_tool_calls() → parallel safe / sequential confirm
│   └── append tool results to messages
└── return (messages, done=True if final answer, False if tools need results)
```

### Key Functions

| Function | Module | Purpose |
|----------|--------|---------|
| `main()` | cli.py | Entry point — arg parsing, client creation, mode dispatch |
| `run_question()` | cli.py | Run one user question to completion (agent turn loop with MAX_STEPS=20 guard) |
| `agent_loop()` | cli.py | Readline REPL — loads skills, routes commands, calls run_question() |
| `agent_turn()` | agent.py | Single Anthropic API call — stream, dispatch tools, return |
| `gemini_agent_turn()` | gemini_agent.py | Single Gemini API call with format conversion |
| `openai_agent_turn()` | openai_agent.py | Single OpenAI API call with format conversion |
| `dispatch_tool_calls()` | tools/\_\_init\_\_.py | Execute tool calls — parallel for safe, sequential for confirm |
| `build_tool_set()` | tools/\_\_init\_\_.py | Filter tools by include/exclude list (for subagents) |
| `register_mcp_tools()` | tools/\_\_init\_\_.py | Add MCP tools to global TOOLS + TOOL_REGISTRY |
| `MCPManager.start()` | mcp_client.py | Connect to MCP servers, discover and register tools |
| `load_mcp_config()` | mcp_client.py | Load .mcp.json / ~/.mcp.json config |
| `run_subagent()` | agents.py | Run a subagent with isolated conversation and filtered tools |
| `detect_project_context()` | context.py | Detect project type, git status, AGENTS.md |
| `load_all_skills()` | skills.py | Scan .skills/ and ~/.skills/ for SKILL.md files |
| `render_skill()` | skills.py | Substitute variables and run dynamic injection in skill body |

### Provider Architecture

Messages are stored internally in **Anthropic format**. Provider-specific modules convert at the API boundary.

**Anthropic** (`agent.py`):
- Native format — no conversion needed
- Prompt caching via `cache_control` blocks
- Streaming via `client.messages.stream()`
- Retry: 3 retries with exponential backoff (1s, 2s, 4s) on `RateLimitError`, `InternalServerError`, `APIConnectionError`

**Gemini** (`gemini_agent.py`):
- `role: "assistant"` → `role: "model"`
- `tool_use` blocks → `FunctionCall` parts
- `tool_result` blocks → `FunctionResponse` parts
- Supports thinking levels (low/medium/high)
- Preserves raw `_gemini_parts` on messages for faithful replay
- SDK: `google-genai` (lazy-imported)

**OpenAI** (`openai_agent.py`):
- System prompt → `role: "developer"` message (works for reasoning models too)
- `tool_use` blocks → `tool_calls` array on assistant message
- `tool_result` blocks → separate `role: "tool"` messages with `tool_call_id`
- Reasoning models (`gpt-5.2`, `o3`, `o4-mini`) use `max_completion_tokens` instead of `max_tokens`
- SDK: `openai` (lazy-imported)

Switching providers mid-session via `/model` recreates the client and clears conversation history.

### Display Protocol

All user-facing output goes through a **`Display` protocol** (`display.py`), accessed via a module-level singleton (`get_display()` / `set_display()`). This decouples tool and agent code from `print()`/`input()`.

```python
class Display:
    def stream_start(self) / stream_token(text) / stream_end()   # response streaming
    def tool_log(message)                                          # tool invocation log
    def tool_result(line_count)                                    # tool output summary
    def confirm(preview_lines, prompt_text) -> bool                # Y/n confirmation
    def auto_approved(preview_lines)                               # auto-approved preview
    def status(message) / error(message) / info(message)           # output categories
```

The default `Display` class prints to stdout. `TUIDisplay` overrides all methods to route output to Textual widgets via `call_from_thread()`.

### TUI (Textual)

Interactive mode uses a Textual-based TUI by default (falls back to readline if `textual` is not installed or `--no-tui` is passed).

**Layout:**
```
┌─────────────────────────────────────────┐
│      RichLog (scrollable conversation)  │
├─────────────────────────────────────────┤
│ > [PromptInput — wraps, auto-grows]     │
├─────────────────────────────────────────┤
│ model (mode) │  token usage  │ context% │
└─────────────────────────────────────────┘
```

**Key components:**

| Component | Description |
|-----------|-------------|
| `AgentApp(App)` | Main Textual app — composes RichLog, PromptInput, status bar |
| `TUIDisplay(Display)` | Routes output to widgets; accumulates streamed tokens, writes full response as single block on `stream_end()` |
| `PromptInput(TextArea)` | Multi-line input with Emacs keybindings (Ctrl+A/E/F/B/D/K/U/W/H). Enter submits, Shift+Enter for newline. Auto-grows up to 8 lines. |
| Status bar | Three `Static` widgets: model+mode, turn/session token counts, context remaining % |

**Confirmation flow:** Worker thread posts preview to RichLog → switches input to Y/n mode → blocks on `threading.Event.wait()` → user responds → main thread signals event → worker resumes.

**Theme:** Light theme (`agent-light`) — white background, sea-green accents, light gray status bar.

**Keyboard shortcuts:** Ctrl+C / Ctrl+Q to quit. Shift+click-drag for terminal-native text selection (bypasses Textual's mouse capture).

---

## Working Directory Tracking

`ShellState` (in `tools/base.py`) tracks the current working directory across commands. When `run_command` executes, it appends `echo "__CWD__:$(pwd)"` to the command, extracts the final cwd from output, and updates `shell.cwd`. All file-based tools resolve relative paths against `shell.cwd` via `_resolve()`.

This means `cd /tmp && ls` in one tool call persists the `/tmp` working directory to subsequent tool calls.

---

## Prompt Caching

Anthropic prompt caching reduces cost and latency on follow-up turns:

- **System prompt** is wrapped in a `cache_control: {type: "ephemeral"}` block
- **Tool definitions** are cached similarly
- **Conversation prefix** — a `cache_control` breakpoint is added to the last message's last content block each turn, so the entire conversation prefix is re-used from cache

Cache hit/create stats are included in token tracking output.

Gemini and OpenAI do not currently use prompt caching.

---

## Token Tracking & Context Trimming

**Token tracking:** After each agent turn, usage stats are printed (to stderr in `-c` mode for clean piping):
- Input/output tokens for the turn
- Cache hits (Anthropic)
- Session totals
- Context window remaining percentage

**Context trimming:** After each question, if the API-reported input token count exceeds **80%** of the model's context window:
1. Oldest message rounds are identified for removal
2. If dropped messages exceed ~200 tokens, they're summarised by the model
3. The summary is prepended as an `[Earlier context summary]` message
4. This preserves key decisions and findings while freeing context budget

---

## Parallel Tool Execution

When the model emits multiple tool calls in a single response, `dispatch_tool_calls()` classifies them:

- **Safe tools** (read-only, or auto-approved in yolo mode): run concurrently via `ThreadPoolExecutor(max_workers=4)`
- **Confirmation-required tools**: run sequentially in the main thread

Results are returned in the same order as the input tool calls. All three provider modules share this dispatch function.

---

## MCP Client Support

The agent can connect to external [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) servers, making their tools available alongside built-in tools across all providers.

### Configuration

Uses the same `mcpServers` format as Claude Desktop, loaded from two locations (project-level takes priority):
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

### Async Bridge Architecture

The MCP Python SDK is fully async, but the agent runs synchronously in threads. `MCPManager` solves this with a dedicated asyncio event loop in a daemon thread:

```
MCPManager
├── _loop: asyncio event loop (runs in daemon thread)
├── _exit_stack: AsyncExitStack (keeps transports + sessions alive)
├── _sessions: dict[server_name -> ClientSession]
├── _tool_map: dict[tool_name -> server_name]
│
├── start(config)       # sync: start loop thread, connect servers, discover tools
├── stop()              # sync: close sessions, stop loop, unregister tools
├── call_tool(name, p)  # sync: submit to loop via run_coroutine_threadsafe, block
└── _connect_all(cfg)   # async: stdio_client → ClientSession → list_tools
```

Sync wrappers submit coroutines via `asyncio.run_coroutine_threadsafe()` and block for results. This is thread-safe — multiple parallel tool calls from `dispatch_tool_calls()` can submit to the same loop concurrently.

### Tool Registration

MCP tools are added to the global `TOOLS` and `TOOL_REGISTRY` via `register_mcp_tools()` in `tools/__init__.py`. This means:
- `build_tool_set()` includes them automatically, so subagents get MCP tools by default
- Subagent definitions can reference MCP tool names in their `"tools"` list
- The Anthropic tool cache is invalidated so the cache breakpoint is recalculated

On name collision with a built-in tool or another MCP server, the duplicate is skipped with a warning log (first registration wins).

### Lifecycle

1. `main()` in `cli.py` calls `load_mcp_config()` → `MCPManager.start(config)` after `setup_delegate()` and `refresh_project_context()`
2. Each configured server is connected via stdio transport, initialized, and its tools are discovered
3. During operation, `call_tool()` routes to the correct server session
4. On exit, `MCPManager.stop()` closes all sessions, stops the event loop, and calls `unregister_mcp_tools()`
5. The `mcp` package is optional — without it, the `ImportError` is caught and MCP features are silently unavailable

---

## Project Context Auto-Detection

At startup, `detect_project_context()` in `context.py` augments the system prompt with:

1. **Project type** — detected from `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `Gemfile`, `CMakeLists.txt`, `Makefile`, or `setup.py`
2. **Git context** — current branch, uncommitted change count, last 5 commits
3. **Convention file** — contents of `AGENTS.md` if present (project-specific instructions for the agent)

This gives the agent immediate awareness of the project it's working in.

---

## Subagent System

The `delegate` tool spawns child agents with isolated conversations and filtered tool sets.

### Built-in Agents

| Agent | Model | Tools | Use Case |
|-------|-------|-------|----------|
| `explore` | haiku | read-only (read_file, list_directory, search_files, glob_files, file_outline, read_url, web_search) | Fast, cheap research and fact-finding |
| `code` | (inherits parent) | all except delegate | Full coding tasks needing file writes or commands |

Subagents **never** have access to `delegate` (no nesting). Each runs in its own conversation up to 20 steps, and returns a final text answer to the parent.

### Custom Agents

Define JSON files in `~/.agents/` (user-level) or `.agents/` (project-level, higher priority):

```json
{
  "name": "data-analyst",
  "description": "Analyzes datasets and generates reports",
  "model": "opus",
  "tools": ["read_file", "run_command", "glob_files", "write_file"],
  "system_prompt": "You are a data analyst..."
}
```

Fields: `name` (required), `description`, `model` (null to inherit), `tools` (null for all except delegate), `system_prompt` (null to inherit).

---

## Skill System

Skills are reusable prompt templates invoked as `/slash` commands in interactive mode.

### Discovery

Skills are loaded from `~/.skills/` (user-level) and `.skills/` (project-level, higher priority). Each skill is a subdirectory containing a `SKILL.md` file:

```
.skills/
    review/SKILL.md
    deploy-check/SKILL.md
```

### SKILL.md Format

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

- **`$ARGUMENTS`** — expands to the full args string
- **`$0`, `$1`, ...** — positional args (space-split)
- **`` !`command` ``** — lines matching this pattern are replaced with command stdout (5-second timeout)

### Bundled Skills (10)

| Skill | Category | Description |
|-------|----------|-------------|
| `/pdf` | Document | PDF reading, merging, splitting, form filling, encryption |
| `/xlsx` | Document | Spreadsheet creation, editing, analysis with formulas |
| `/docx` | Document | Word document creation/editing via docx-js or XML manipulation |
| `/pptx` | Document | Presentation reading, editing, creation |
| `/doc-coauthoring` | Document | Structured co-authoring workflow with iterative refinement |
| `/mcp-builder` | Development | Guide for creating MCP (Model Context Protocol) servers |
| `/webapp-testing` | Development | Playwright-based web application testing |
| `/web-artifacts-builder` | Development | React + Vite + Tailwind + shadcn/ui scaffolding |
| `/skill-creator` | Development | Guide for creating new skills |
| `/imagegen` | Other | Gemini image generation via CLI |

Built-in commands (`/clear`, `/copy`, `/model`, `/thinking`, `/version`) cannot be shadowed by skills.

---

## File Attachments

Use `@filepath` in prompts to attach images or PDFs:

```bash
llm-agent -c "@photo.png what's in this image?"
llm-agent -c "@report.pdf summarize this"
llm-agent -c "@a.png @b.png compare these"
```

- The `@` must be at the start of a word (so `user@email.com` is left alone)
- Supported formats: PNG, JPG, JPEG, GIF, WebP, PDF
- Files are base64-encoded and sent as multimodal content blocks
- Works in both interactive and single-shot (`-c`) mode

---

## Error Handling & Retry Logic

All three providers implement retry with exponential backoff (**3 retries**, delays of **1s, 2s, 4s**):

| Provider | Retried Errors |
|----------|---------------|
| Anthropic | `RateLimitError`, `InternalServerError`, `APIConnectionError` |
| Gemini | `ResourceExhausted`, `InternalServerError`, `ServiceUnavailable`, `TooManyRequests` |
| OpenAI | Rate limits and server errors (via SDK + manual retry) |

The agent loop itself has a `MAX_STEPS=20` guard to prevent runaway tool-use loops. Ctrl+C cancels the current response and returns to the prompt.

---

## Safety

### Command Confirmation

Mutating tools (`write_file`, `edit_file`, `run_command`) always show a preview and prompt for confirmation. In yolo mode (`-y`), `run_command` auto-approves **unless** the command matches dangerous patterns.

### Dangerous Command Patterns

Commands containing these substrings are **never** auto-approved:

```
rm, rmdir, mkfs, dd, > /dev/, mv, chmod, chown,
kill, killall, pkill, shutdown, reboot, halt,
curl|, wget| (piped downloads)
```

### System Prompt Guardrails

The system prompt instructs the model to:
- Prefer read-only, non-destructive commands
- Never run destructive commands without explicit user request
- Never commit or display secrets, API keys, or credentials
- Read files before modifying them
- Validate user input at system boundaries
- Avoid introducing security vulnerabilities

### Command Timeout

Shell commands time out after 30 seconds by default (configurable with `-t`).

---

## Extending the Agent

### Adding a New Tool

1. Create `llm_agent/tools/my_tool.py`:

```python
SCHEMA = {
    "name": "my_tool",
    "description": "Does something useful",
    "input_schema": {
        "type": "object",
        "properties": {
            "arg": {"type": "string", "description": "..."},
        },
        "required": ["arg"],
    },
}

NEEDS_CONFIRM = False  # True for mutating tools

def log(params):
    from llm_agent.display import get_display
    get_display().tool_log(f"  my_tool: {params['arg']}")

LOG = log

def handle(params):
    return "result"
```

2. Add the import to `llm_agent/tools/__init__.py`.

### Adding a New Skill

Create `.skills/my-skill/SKILL.md` with YAML frontmatter (`name`, `description`, `argument-hint`) and a prompt body using `$0`/`$ARGUMENTS` variables and `` !`command` `` dynamic injection.

### Adding a Custom Agent

Create a JSON file in `.agents/` or `~/.agents/` defining `name`, `description`, and optionally `model`, `tools`, and `system_prompt`.
