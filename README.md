# llm-agent

A terminal-based AI agent that answers questions by exploring your filesystem, running shell commands, and searching the web. Supports Anthropic Claude (direct API and Vertex AI) and Google Gemini models.

## Installation

Requires Python 3.9+.

```bash
pip install -e .              # base install (Anthropic direct API)
pip install -e '.[vertex]'    # with Vertex AI support
pip install -e '.[gemini]'    # with Gemini support
pip install -e '.[all]'       # all providers
```

## Setup

Configure at least one provider:

```bash
# Anthropic API (direct)
export ANTHROPIC_API_KEY="your-api-key"

# Anthropic via Google Vertex AI
export ANTHROPIC_VERTEX_PROJECT_ID="your-gcp-project-id"
export CLOUD_ML_REGION="us-east5"  # optional, defaults to us-east5

# Google Gemini
export GOOGLE_API_KEY="your-google-api-key"
```

If both `ANTHROPIC_API_KEY` and `ANTHROPIC_VERTEX_PROJECT_ID` are set, the direct API takes priority.

## Usage

```bash
# Interactive mode (default model: sonnet)
llm-agent

# Choose a model
llm-agent -m opus
llm-agent -m haiku
llm-agent -m gemini-flash
llm-agent -m gemini-pro

# Single-shot mode
llm-agent -c "how much disk space is free?"
llm-agent -c "what's in /etc/hosts?" -m haiku

# Auto-approve safe commands
llm-agent -y
llm-agent --yolo

# Attach images or PDFs
llm-agent -c "@photo.png what's in this image?"
llm-agent -c "@report.pdf summarize this document"

# Gemini thinking level
llm-agent -m gemini-pro --thinking high
```

### Interactive commands

| Command | Description |
|---------|-------------|
| `/model <name>` | Switch model mid-session |
| `/thinking [level]` | Show or set Gemini thinking level (`low`/`medium`/`high`/`off`) |
| `Ctrl+C` | Cancel current response |
| `Ctrl+D` | Exit |

## Tools

The agent has ten tools it can use autonomously. Read-only tools run without confirmation; mutating tools prompt before executing.

**Read-only:**
- **read_file** -- read file contents with line numbers, optional offset/limit
- **list_directory** -- list directory entries with types and sizes
- **search_files** -- regex search over file contents (ripgrep, falls back to grep)
- **glob_files** -- find files by glob pattern recursively (`**/*.py`, etc.)
- **read_url** -- fetch a URL and return plain text content
- **web_search** -- search the web via DuckDuckGo (no API key needed)

**Mutating (require confirmation):**
- **write_file** -- create or overwrite a file
- **edit_file** -- targeted find-and-replace in an existing file
- **run_command** -- run an arbitrary shell command

**Delegation (no confirmation):**
- **delegate** -- spawn a subagent with its own conversation and filtered tool set (built-in: `explore` for read-only research, `code` for full access)

In yolo mode (`-y`), `run_command` auto-approves unless the command matches known dangerous patterns (e.g. `rm -rf`, `mkfs`, `dd`).

## Features

- **Streaming** -- responses stream to the terminal as they're generated
- **Working directory tracking** -- `cd` in one command persists to the next
- **Prompt caching** -- system prompt and conversation prefix are cached across API calls
- **Token tracking** -- per-turn and session totals after each answer
- **File attachments** -- `@filepath` syntax for images (png, jpg, gif, webp) and PDFs
- **Readline** -- line editing and persistent history (`~/.agent_history`)
- **Output truncation** -- command output over 200 lines is trimmed to first/last 100 lines

## Supported models

| Alias | Model |
|-------|-------|
| `sonnet` | claude-sonnet-4-6 |
| `opus` | claude-opus-4-6 |
| `haiku` | claude-haiku-4-5 |
| `gemini-flash` | gemini-2.5-flash |
| `gemini-pro` | gemini-3.1-pro-preview |

## License

MIT
