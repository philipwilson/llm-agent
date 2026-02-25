# Explanation of `llm-agent`

`llm-agent` is a **toy interactive CLI agent** that uses LLMs to answer user questions by running Unix shell commands, exploring the filesystem, and searching the web. It implements an agentic tool-use loop: the user asks a question, the model decides which tools to call, the agent executes them locally, feeds the results back, and repeats until the model has enough information to give a final answer. Supports Anthropic Claude (direct API and Vertex AI) and Google Gemini models.

---

## High-Level Architecture

```
User Question
     │
     ▼
┌──────────────┐
│  Session      │◄──── conversation history
│  (session.py) │
└──────┬───────┘       │
       ▼
┌──────────────────┐     ┌──────────────┐
│  API Call         │────►│ Tool Use?    │
│  (streaming)      │     │  yes / no    │
└──────────────────┘     └──────┬───────┘
                                │
                 ┌──────────────┴──────────────┐
                 │ No                          │ Yes
                 ▼                             ▼
          Print final answer        Execute tool(s) locally
                                   Feed results back to model
                                   (loop again)
```

The code was originally a single `agent.py` file but has been refactored into a proper Python package (`llm_agent/`) with separate modules for the CLI, each provider's agent turn logic, formatting, and individual tools.

---

## Package Structure

```
pyproject.toml              — package metadata and entry point
llm_agent/
    __init__.py             — VERSION constant
    cli.py                  — main(), arg parsing, REPL, run_question()
    agent.py                — Anthropic agent_turn(), streaming, caching, retry
    gemini_agent.py         — Gemini gemini_agent_turn(), format conversion
    formatting.py           — ANSI colour helpers, output truncation, token formatting
    system_prompt.txt       — system prompt (editable without touching Python)
    tools/
        __init__.py         — collects TOOLS list + TOOL_REGISTRY from modules
        base.py             — ShellState, _resolve(), confirm_edit(), COMMAND_TIMEOUT
        read_file.py        — SCHEMA + handle
        list_directory.py   — SCHEMA + handle
        search_files.py     — SCHEMA + handle
        glob_files.py       — SCHEMA + handle
        read_url.py         — SCHEMA + handle
        web_search.py       — SCHEMA + handle
        write_file.py       — SCHEMA + handle
        edit_file.py        — SCHEMA + handle
        run_command.py      — SCHEMA + handle + NEEDS_CONFIRM
```

---

## Module-by-Module Walkthrough

### 1. `cli.py` — Entry Point, Arg Parsing, REPL

This is the top-level module. It handles:

- **Model aliases**: Maps short names (`opus`, `sonnet`, `haiku`, `gemini-flash`, `gemini-pro`) to full model IDs. Default is `sonnet`.
- **Constants**:
  - `HISTORY_FILE` / `HISTORY_SIZE`: Persistent readline history at `~/.agent_history` (1000 entries).
  - `MAX_STEPS = 20`: Limits tool-use iterations per user question.
  - `CONTEXT_WINDOWS`: Maps model IDs to their context window sizes (200k for Claude, 1M for Gemini).
  - `CONTEXT_BUDGET = 0.80`: Trim threshold — conversation is trimmed when input tokens exceed 80% of the model's context window.
  - `ATTACHMENT_TYPES`: Maps file extensions to media types for `@filepath` attachments.
  - `DEFAULT_THINKING`: Per-model thinking level defaults (gemini-pro defaults to `high`).

- **`parse_attachments(text)`**: Scans user input for `@filepath` tokens, base64-encodes recognised file types (images, PDFs), and returns multimodal content blocks in Anthropic format. Only triggers on `@` at the start of a word (so `user@email.com` is left alone). Reports an error if the file is missing or has an unsupported extension.

- **`setup_readline()`**: Loads persistent input history and registers an `atexit` handler to save it on exit.

- **`make_client(model)`**: Auto-detects the backend based on the model and environment:
  - Gemini models → `google-genai` SDK with `GOOGLE_API_KEY`
  - `ANTHROPIC_API_KEY` set → direct Anthropic API
  - `ANTHROPIC_VERTEX_PROJECT_ID` set → Anthropic via Google Vertex AI
  - Otherwise exits with an error

- **`Session.run_question(user_input)`** (in `session.py`): Runs a single user question to completion. Parses attachments, selects the right turn function (`agent_turn` or `gemini_agent_turn`), and calls it in a loop until the model produces a final answer or `MAX_STEPS` is reached. Handles Ctrl+C by discarding the partial turn cleanly.

- **`estimate_tokens(messages)`**: Estimates the token count of a list of messages using a chars/4 heuristic. Used by `trim_conversation()` to gauge how many tokens removing a round of messages would reclaim.

- **`trim_conversation(conversation, last_input_tokens, model)`**: Removes oldest complete message rounds (a user message plus all following assistant/tool messages) until the estimated token removal covers the excess above the context budget. Only trims when `last_input_tokens` exceeds 80% of the model's context window.

- **`Session` class** (in `session.py`): Owns the state (conversation history, token stats, loaded skills). Exposes `handle_command()` for routing slash commands and `run_question()` for executing tasks. Automatically manages token-budget trimming.

- **`agent_loop()`** (in `cli.py`): The interactive REPL. Prints a welcome banner, then repeatedly prompts for input. It delegates slash commands and questions to the `Session` object. After each answer, it queries the `Session` to display per-turn and session-level token usage.
- **`main()`**: Parses CLI arguments:
  - `-m` / `--model`: Choose model (default: `sonnet`)
  - `-y` / `--yolo`: Auto-approve safe commands
  - `-c QUESTION`: Single-shot mode (non-interactive, prints token stats to stderr)
  - `-t` / `--timeout`: Command timeout in seconds (default: 30)
  - `--thinking`: Thinking level for Gemini models (`low`/`medium`/`high`)

  Then creates the API client and dispatches to single-shot or interactive mode.

### 2. `agent.py` — Anthropic Streaming & Tool Dispatch

**`agent_turn(client, model, messages, auto_approve, usage_totals)`** handles a single Anthropic API call:

1. **Prompt caching**: The system prompt, tool definitions, and conversation prefix all have `cache_control` breakpoints. This means unchanged portions are cached across successive API calls within a question, reducing cost and latency.
2. **Streams the response** using `client.messages.stream()`, so text appears in real-time.
3. **Collects content blocks** as they arrive:
   - `text` blocks are printed immediately to the terminal.
   - `tool_use` blocks have their JSON input accumulated incrementally from `input_json_delta` events.
4. **Retry logic**: Retries up to 3 times (with exponential backoff: 1s, 2s, 4s) on `RateLimitError`, `InternalServerError`, and `APIConnectionError`.
5. **Tracks token usage** from the final message, including cache read/creation stats.
6. **If no tool calls** → returns `done=True` (the model gave a final answer).
7. **If tool calls exist**:
   - Appends the assistant's response to the message history.
   - Executes each tool via the `TOOL_REGISTRY`.
   - Appends tool results as a `user` message (per Anthropic's API convention).
   - Returns `done=False` so the loop continues.

### 3. `gemini_agent.py` — Gemini Streaming & Format Conversion

Since messages are stored internally in Anthropic format, this module converts at the API boundary:

- **`_convert_tools()`**: Converts Anthropic tool schemas to Gemini `FunctionDeclaration` objects.
- **`_to_gemini_contents()`**: Converts Anthropic-format messages to Gemini `Content` objects:
  - `role: "assistant"` → `role: "model"`
  - `tool_use` blocks → `FunctionCall` parts
  - `tool_result` blocks → `FunctionResponse` parts (tool name stashed in `_name` field)
  - Image/document blocks → decoded from base64 to raw bytes via `Part.from_bytes()`
  - Raw Gemini parts (with thought signatures) are replayed directly when available.

- **`gemini_agent_turn()`**: Same contract as `agent_turn()`. Adds support for a `thinking_level` parameter (`low`/`medium`/`high`) that controls Gemini's reasoning depth. Same retry logic pattern (3 retries with backoff). Stashes raw Gemini parts alongside Anthropic-format blocks so thought signatures are preserved across turns.

### 4. `formatting.py` — Colour Helpers & Output Formatting

- **ANSI wrappers**: `bold`, `dim`, `red`, `green`, `yellow`, `cyan` — each wraps text in escape codes. Respects `NO_COLOR` / `FORCE_COLOR` env vars and TTY detection.
- **`truncate(text, max_lines)`**: Keeps the first and last half of lines (default 200 total), omitting the middle with a count of omitted lines.
- **`format_tokens(n)`**: Formats token counts as `1.2k`, `3.4M`, etc.

### 5. `tools/` — One File Per Tool

Each tool module exports:
- `SCHEMA` — Anthropic tool-use JSON schema (name, description, input_schema)
- `handle(params)` — the implementation function
- `LOG` (optional) — a function that prints what the tool is doing
- `NEEDS_CONFIRM` (optional) — set to `True` for mutating tools

`tools/__init__.py` auto-collects these into `TOOLS` (schema list) and `TOOL_REGISTRY` (dispatch dict). Adding a new tool requires creating a module file and adding one import line.

**`tools/base.py`** provides shared infrastructure:
- **`ShellState`**: Tracks the working directory across `run_command` invocations. Appends `echo "__CWD__:$(pwd)"` to each command and parses the result to update `cwd`.
- **`_resolve(path)`**: Resolves relative paths against the shell's working directory. Used by all file-based tools.
- **`confirm_edit(prompt_lines)`**: Shows a preview and prompts `Apply? [Y/n]`.
- **`COMMAND_TIMEOUT`**: Default 30s, overridable via `-t`.

The nine tools:

| Tool | Type | Purpose |
|------|------|---------|
| `read_file` | Read-only | Read file contents with line numbers, optional `offset`/`limit` paging |
| `list_directory` | Read-only | List directory entries with type indicators and file sizes |
| `search_files` | Read-only | Regex search over file contents using ripgrep (falls back to grep) |
| `glob_files` | Read-only | Find files matching a glob pattern recursively via `glob.glob()` |
| `read_url` | Read-only | Fetch a web page via curl, convert HTML to plain text via lynx/w3m (regex fallback) |
| `web_search` | Read-only | Search the web via DuckDuckGo HTML (no API key needed) |
| `write_file` | Mutating | Create or overwrite a file (shows preview, requires confirmation) |
| `edit_file` | Mutating | Targeted find-and-replace (old_string must match exactly once, shows diff) |
| `run_command` | Mutating | Arbitrary shell command (requires confirmation; dangerous patterns always require it even in YOLO mode) |

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Multi-provider support** | Anthropic (direct + Vertex AI) and Gemini, selectable at runtime via `-m`. Messages stored in Anthropic format internally; Gemini module converts at the boundary. |
| **One file per tool** | Each tool is self-contained with its own schema and handler. Adding a tool is one file + one import line. |
| **Streaming output** | Text appears as the model generates it, giving a responsive feel. |
| **Prompt caching** | System prompt, tools, and conversation prefix are cached across API calls to reduce cost and latency (Anthropic only). |
| **Retry with backoff** | Transient API errors (rate limits, server errors, connection issues) retry up to 3 times with exponential backoff. |
| **Dangerous command detection** | Simple substring matching against `DANGEROUS_PATTERNS` ensures destructive commands always need human approval, even in YOLO mode. |
| **Tool confirmation for writes** | `write_file` and `edit_file` always show a diff-like preview and require confirmation — no auto-approve bypass. |
| **Ripgrep with grep fallback** | `search_files` prefers `rg` for speed and `.gitignore` awareness, but gracefully degrades. |
| **Working directory tracking** | `ShellState` tracks `cwd` across commands so `cd` in one command persists to the next. All file tools resolve relative paths against it. |
| **Token-budget conversation trimming** | Uses actual input token counts reported by the API to trim conversation history when it exceeds 80% of the model's context window, rather than using a fixed message count. |
| **Partial turn discard on Ctrl-C** | Interrupted turns don't leave broken tool-result pairs in the conversation. |
| **Gemini thought preservation** | Raw Gemini parts are stashed alongside Anthropic-format blocks so thought signatures are faithfully replayed on subsequent turns. |

---

## Usage

```bash
pip install -e .              # base install
pip install -e '.[gemini]'    # with Gemini support
pip install -e '.[all]'       # all providers

# Anthropic (direct API)
export ANTHROPIC_API_KEY="sk-..."
llm-agent                              # interactive, confirm mode, sonnet
llm-agent -m opus                      # use opus
llm-agent -y                           # YOLO mode (auto-approve safe commands)

# Anthropic (Vertex AI)
export ANTHROPIC_VERTEX_PROJECT_ID="my-project"
export CLOUD_ML_REGION="us-east5"      # optional
llm-agent

# Gemini
export GOOGLE_API_KEY="your-key"
llm-agent -m gemini-pro --thinking high

# Single-shot mode
llm-agent -c "how much disk space is free?"
llm-agent -c "what's in /etc/hosts?" -m haiku --yolo

# Attach images or PDFs
llm-agent -c "@photo.png what's in this image?"
llm-agent -c "@report.pdf summarize this document"
```
