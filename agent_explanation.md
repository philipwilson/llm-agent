# Explanation of `agent.py`

`agent.py` is a **toy interactive CLI agent** that uses Anthropic's Claude model to answer user questions by running Unix shell commands and file operations. It implements an agentic tool-use loop: the user asks a question, Claude decides which tools to call, the script executes them locally, feeds the results back to Claude, and repeats until Claude has enough information to give a final answer.

---

## High-Level Architecture

```
User Question
     │
     ▼
┌──────────────┐
│  Agent Loop   │◄──── conversation history
│  (agent_loop) │
└──────┬───────┘
       │
       ▼
┌──────────────────┐     ┌──────────────┐
│  Claude API Call  │────►│ Tool Use?    │
│  (streaming)      │     │  yes / no    │
└──────────────────┘     └──────┬───────┘
                                │
                 ┌──────────────┴──────────────┐
                 │ No                          │ Yes
                 ▼                             ▼
          Print final answer        Execute tool(s) locally
                                   Feed results back to Claude
                                   (loop again)
```

---

## Major Sections

### 1. Configuration & Constants (lines 1–64)

- **Imports**: `argparse`, `subprocess`, `json`, `os`, `readline`, and the `anthropic` SDK.
- **ANSI color helpers**: `bold`, `dim`, `red`, `green`, `yellow`, `cyan` — used throughout for pretty terminal output. Respects `NO_COLOR` / `FORCE_COLOR` env vars and whether stdout is a TTY.
- **Model aliases**: Maps short names (`opus`, `sonnet`, `haiku`) to full Anthropic model IDs. Default is `sonnet`.
- **Safety/limit constants**:
  - `HISTORY_FILE` / `HISTORY_SIZE`: Persistent readline history at `~/.agent_history`.
  - `MAX_OUTPUT_LINES = 200`: Truncates long command output.
  - `COMMAND_TIMEOUT = 30`: Kills commands that take longer than 30 seconds.
  - `MAX_STEPS = 20`: Limits tool-use iterations per user question.
  - `MAX_CONVERSATION_TURNS = 40`: Trims older conversation turns to avoid unbounded context growth.
  - `DANGEROUS_PATTERNS`: A list of shell substrings (e.g., `rm `, `mkfs`, `dd `, `shutdown`) that always require manual confirmation even in auto-approve mode.

### 2. System Prompt & Tool Definitions (lines 66–238)

- **`SYSTEM_PROMPT`**: Instructs Claude that it is a Unix CLI assistant with access to six tools, and provides usage guidelines (prefer dedicated tools, prefer read-only commands, don't guess, etc.).
- **`TOOLS`**: A list of six tool definitions in Anthropic's tool-use JSON schema format:

| Tool | Purpose |
|------|---------|
| `read_file` | Read file contents with line numbers, optional offset/limit |
| `list_directory` | List directory entries with types and human-readable sizes |
| `search_files` | Regex search across files (uses `rg` or falls back to `grep`) |
| `write_file` | Create or overwrite a file (requires user confirmation) |
| `edit_file` | Find-and-replace a unique string in a file (requires user confirmation) |
| `run_command` | Run an arbitrary shell command (requires confirmation; dangerous commands always require it) |

### 3. Client Setup (lines 241–265)

- **`setup_readline()`**: Loads persistent input history from `~/.agent_history` and registers an `atexit` handler to save it on exit.
- **`make_client()`**: Auto-detects the backend:
  - If `ANTHROPIC_API_KEY` is set → uses the direct Anthropic API.
  - Else if `ANTHROPIC_VERTEX_PROJECT_ID` is set → uses Google Vertex AI (defaults to `us-east5` region).
  - Otherwise exits with an error message.

### 4. Tool Handler Functions (lines 268–503)

Each tool has a corresponding Python function that performs the actual work:

- **`truncate(text, max_lines)`**: Keeps the first and last half of lines, omitting the middle, to keep output within `MAX_OUTPUT_LINES`.

- **`run_command(command)`**: Runs a shell command via `subprocess.run()` with a 30s timeout. Returns combined stdout/stderr or an error/timeout message.

- **`handle_read_file(params)`**: Opens a file, reads all lines, selects the requested range (`offset` / `limit`), and returns them with line numbers prefixed.

- **`handle_list_directory(params)`**: Lists directory contents using `os.listdir()`. Shows directories with a trailing `/`, symlinks with `->`, and files with human-readable sizes (B/k/M). Hidden files filtered unless `hidden=True`.

- **`handle_search_files(params)`**: Tries `rg` (ripgrep) first with `--glob` support; falls back to `grep -rn` if ripgrep isn't installed. Caps results at `max_results` (default 50).

- **`handle_write_file(params)`**: Shows a preview of the file to be written (first/last 5 lines if long), asks the user for Y/n confirmation, then writes.

- **`handle_edit_file(params)`**: Reads the file, verifies the `old_string` exists exactly once, shows a red/green diff preview, asks for confirmation, then does a single `str.replace()`.

- **`confirm(command, description, auto_approve)`**: The confirmation prompt for `run_command`. In auto-approve mode (`-y`), safe commands are auto-approved; dangerous ones (matching `DANGEROUS_PATTERNS`) still require manual confirmation.

- **`handle_run_command(params, auto_approve)`**: Prints the command, calls `confirm()`, and if approved, runs it.

### 5. Tool Registry (lines 506–537)

A dictionary mapping tool names to their handlers, optional log functions (for printing what's being done), and a flag for whether the handler needs the `auto_approve` argument. This cleanly decouples tool dispatch from the agent loop.

### 6. Agent Turn — Streaming & Tool Execution (lines 540–638)

**`agent_turn(client, model, messages, auto_approve, usage_totals)`** handles a single Claude API call:

1. **Streams the response** using `client.messages.stream()`, so text appears in real-time.
2. **Collects content blocks** as they arrive:
   - `text` blocks are printed immediately to the terminal.
   - `tool_use` blocks have their JSON input accumulated incrementally.
3. **Tracks token usage** from the final message.
4. **If no tool calls** → returns `done=True` (the model gave a final answer).
5. **If tool calls exist**:
   - Appends the assistant's response to the message history.
   - Executes each tool via the `TOOL_REGISTRY`.
   - Appends tool results as a `user` message (per Anthropic's API convention).
   - Returns `done=False` so the loop continues.

### 7. Agent Loop — The Main REPL (lines 649–714)

**`agent_loop(client, model, auto_approve)`** is the outer interactive loop:

1. Prints a welcome banner with the model name and mode.
2. Repeatedly prompts the user for input (`>` prompt).
3. For each user message:
   - Appends it to the `conversation` history.
   - Enters an **inner loop** calling `agent_turn()` until Claude produces a final text answer (no tool calls) or `MAX_STEPS` is reached.
   - Tracks and displays per-turn and per-session token usage.
   - On `KeyboardInterrupt`, discards the partial turn (doesn't corrupt conversation history).
4. **Conversation trimming**: After each turn, if history exceeds `MAX_CONVERSATION_TURNS`, it trims from the front, ensuring the first remaining message is from the user (so the API doesn't receive an orphaned assistant/tool message).

### 8. Entry Point (lines 716–738)

**`main()`** parses CLI arguments:
- `-m` / `--model`: Choose `opus`, `sonnet`, or `haiku` (default: `sonnet`).
- `-y` / `--yolo`: Auto-approve mode — safe commands run without confirmation; dangerous ones still ask.

Then sets up readline, creates the API client, and enters `agent_loop()`.

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Streaming output** | Text appears as Claude generates it, giving a responsive feel. |
| **Dangerous command detection** | Simple substring matching against `DANGEROUS_PATTERNS` ensures destructive commands always need human approval, even in YOLO mode. |
| **Tool confirmation for writes** | `write_file` and `edit_file` always show a diff-like preview and require confirmation — no auto-approve bypass. |
| **Ripgrep with grep fallback** | Prefers `rg` for speed and `.gitignore` awareness, but gracefully degrades. |
| **Conversation trimming** | Prevents unbounded context window growth in long sessions, while maintaining coherent history. |
| **Partial turn discard on Ctrl-C** | Interrupted turns don't leave broken tool-result pairs in the conversation. |

---

## Usage

```bash
# Using the direct Anthropic API
export ANTHROPIC_API_KEY="sk-..."
python agent.py                    # interactive, confirm mode, sonnet
python agent.py -m opus            # use opus model
python agent.py -y                 # YOLO mode (auto-approve safe commands)

# Using Google Vertex AI
export ANTHROPIC_VERTEX_PROJECT_ID="my-project"
export CLOUD_ML_REGION="us-east5"  # optional, defaults to us-east5
python agent.py
```
