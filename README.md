# llm-agent

A terminal-based AI agent that answers questions by exploring your filesystem, running shell commands, and searching the web. Supports Anthropic Claude (direct API and Vertex AI), Google Gemini, and OpenAI models.

## Installation

Requires Python 3.9+.

```bash
pip install -e .              # base install (Anthropic direct API)
pip install -e '.[vertex]'    # with Vertex AI support
pip install -e '.[gemini]'    # with Gemini support
pip install -e '.[openai]'    # with OpenAI support
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

# OpenAI
export OPENAI_API_KEY="your-openai-api-key"
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
llm-agent -m gpt-4o
llm-agent -m gpt-5.2
llm-agent -m o3

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
| `/skills` | List available skills |
| `/name [args]` | Invoke a skill (e.g. `/review src/main.py`) |
| `/clear` | Clear conversation history |
| `/version` | Show version and current model |
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
- **Skills** -- reusable prompt templates invoked as `/slash` commands (see below)

## Skills

Skills are reusable prompt templates you invoke as `/slash` commands. Place them in `.skills/` (project-level) or `~/.skills/` (user-level) as subdirectories containing a `SKILL.md` file:

```
.skills/review/SKILL.md
```

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

- `$ARGUMENTS` expands to the full args string; `$0`, `$1`, etc. expand to positional args
- Lines matching `` !`command` `` are replaced with the command's stdout
- Use `/skills` to list available skills

## Supported models

| Alias | Model | Provider |
|-------|-------|----------|
| `sonnet` | claude-sonnet-4-6 | Anthropic |
| `opus` | claude-opus-4-6 | Anthropic |
| `haiku` | claude-haiku-4-5 | Anthropic |
| `gemini-flash` | gemini-2.5-flash | Google |
| `gemini-pro` | gemini-3.1-pro-preview | Google |
| `gpt-4o` | gpt-4o | OpenAI |
| `gpt-4o-mini` | gpt-4o-mini | OpenAI |
| `gpt-5.2` | gpt-5.2 | OpenAI |
| `o3` | o3 | OpenAI |
| `o4-mini` | o4-mini | OpenAI |

## License

MIT
