# Codebase Study: `llm-agent`

## What It Is

**llm-agent** (v0.8.1) is a terminal-based AI agent that answers questions by exploring the filesystem, running shell commands, and searching the web. It's a Python package (~62k bytes of source) with a `llm-agent` CLI entry point.

## Supported Providers & Models

| Alias | Model | Provider |
|-------|-------|----------|
| `sonnet` (default) | claude-sonnet-4-6 | Anthropic / Vertex AI |
| `opus` | claude-opus-4-6 | Anthropic / Vertex AI |
| `haiku` | claude-haiku-4-5 | Anthropic / Vertex AI |
| `gemini-flash` | gemini-2.5-flash | Google Gemini |
| `gemini-pro` | gemini-3.1-pro-preview | Google Gemini |

---

## Architecture

The codebase follows a clean **modular architecture** with clear separation of concerns:

```
pyproject.toml              — Package metadata, entry point, dynamic version
llm_agent/
    __init__.py             — VERSION = "0.8.1"
    cli.py          (16k)   — Entry point, arg parsing, REPL, conversation management
    agent.py        (8.7k)  — Anthropic Claude streaming, caching, retry logic
    gemini_agent.py (8.5k)  — Gemini streaming, format conversion, thinking config
    agents.py       (6.4k)  — Subagent definitions, custom agent loading, execution
    formatting.py   (1.2k)  — ANSI colors, output truncation, token formatting
    system_prompt.txt (2k)  — System prompt (editable without touching Python)
    tools/
        __init__.py         — Tool registry, build_tool_set() for filtering
        base.py             — ShellState, path resolution, confirmation UI, timeouts
        read_file.py        — Read files with line numbers + paging
        list_directory.py   — Directory listing with types/sizes
        search_files.py     — Regex search (ripgrep → grep fallback)
        glob_files.py       — Recursive glob via Python's glob.glob()
        read_url.py         — Fetch URLs (curl + lynx/w3m/regex HTML conversion)
        web_search.py       — DuckDuckGo HTML scraping (no API key)
        write_file.py       — File creation/overwrite with preview + confirmation
        edit_file.py        — Find-and-replace with diff preview + uniqueness check
        run_command.py      — Shell execution with dangerous pattern detection
        delegate.py         — Subagent spawning via callback injection
docs/                       — Detailed architecture and flow explanations
```

## Key Flow

1. **`main()`** (cli.py) — Parses args (`-m`, `-y`, `-c`, `--thinking`), creates API client, routes to single-shot or interactive mode
2. **`agent_loop()`** (cli.py) — Interactive REPL with readline, supports `/model`, `/thinking`, `/clear`, `/version` commands
3. **`run_question()`** (cli.py) — Runs one user question to completion: calls `agent_turn` in a loop until the model gives a final answer, capped at `MAX_STEPS=20`
4. **`agent_turn()`** / **`gemini_agent_turn()`** — Streams a single API call, dispatches tool calls via `TOOL_REGISTRY`, returns `(messages, done)`
5. **Tool dispatch** — The registry maps tool names → `{handler, log, needs_confirm}`. Tools that mutate the filesystem prompt for confirmation.

## Tool System (10 Tools)

**Read-only (auto-approved):**
- `read_file` — File viewing with offset/limit paging
- `list_directory` — Directory listing with hidden file support
- `search_files` — Regex content search (rg preferred, grep fallback)
- `glob_files` — Recursive file finding via glob patterns
- `read_url` — Web page fetching with HTML-to-text conversion
- `web_search` — DuckDuckGo scraping, no API key needed

**Mutating (confirmation required):**
- `write_file` — Create/overwrite with preview (auto-creates parent dirs)
- `edit_file` — Unique-match find-and-replace with colored diff preview
- `run_command` — Shell execution with dangerous pattern detection

**Delegation:**
- `delegate` — Spawns sandboxed subagents (no nesting allowed)

## Subagent System

Two built-in agents, plus custom agents loadable from `~/.agents/` or `.agents/` JSON files:
- **`explore`** — Read-only tools, uses haiku, optimized for fast research
- **`code`** — Full tools (except delegate), inherits parent model

Subagents get isolated conversations, filtered tool sets, and optional model overrides. They can never call `delegate` themselves (no recursion).

## Notable Design Patterns

| Pattern | Details |
|---------|---------|
| **Provider abstraction** | Messages stored in Anthropic format internally; Gemini module converts at API boundary |
| **Prompt caching** | System prompt + conversation prefix cached with `ephemeral` cache control to reduce cost/latency |
| **Token-budget trimming** | When input tokens exceed 80% of context window, oldest message rounds are dropped |
| **Graceful degradation** | `read_url` tries lynx → w3m → regex; `search_files` tries rg → grep |
| **Dangerous command detection** | `run_command` blocks auto-approval for patterns like `rm -rf`, `mkfs`, `dd`, piped downloads |
| **Shell state persistence** | `ShellState` tracks `cwd` across commands (like Claude Code) |
| **Streaming output** | Responses stream to terminal in real-time for both providers |
| **Exponential backoff** | 3 retries with [1, 2, 4]s delays for rate limits and server errors |
| **Callback injection** | `delegate` tool uses a runtime-injected callback to avoid circular imports |

## Known TODOs / Open Items

- **Cost estimates** — Show dollar costs alongside token counts
- **Sandbox option** — Docker/bwrap for safe command execution
- **Stronger dangerous-pattern detection** — Current substring matching is easily bypassed
- **Path restriction for writes** — `write_file`/`edit_file` can write anywhere (including `~/.ssh/`)
- **Magic strings** — Control flow strings like `"(user declined to run this command)"` should be constants
- **Minor bug** — "1 more lines" pluralization in write_file preview

---

This is a well-structured, thoughtfully designed CLI agent with good separation between provider-specific logic and the core agent loop. The tool system is cleanly modular (one file per tool), and the subagent architecture adds flexibility for complex multi-step tasks.
