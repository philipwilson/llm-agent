# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A single-file Python agent loop (`agent.py`) that uses Claude to answer user questions by exploring the filesystem and running Unix commands. Supports both the direct Anthropic API and Google Vertex AI.

## Running

```bash
# Option 1: Direct Anthropic API
export ANTHROPIC_API_KEY="your-api-key"

# Option 2: Google Vertex AI
export ANTHROPIC_VERTEX_PROJECT_ID="your-gcp-project-id"
export CLOUD_ML_REGION="us-east5"  # optional, defaults to us-east5

# If both are set, ANTHROPIC_API_KEY takes priority.

# Run with default model (sonnet)
python agent.py

# Select model
python agent.py -m opus
python agent.py -m haiku
python agent.py -m sonnet

# Auto-approve safe commands
python agent.py -y
python agent.py --yolo
```

Requires `anthropic` pip package (add `[vertex]` extra for Vertex AI support).

## Architecture

Everything is in `agent.py`. The key flow:

1. **`main()`** — parses args (`-m` model, `-y` yolo), sets up readline history, creates API client
2. **`agent_loop()`** — REPL that reads user input, runs the inner agent loop, maintains conversation history, and prints token usage after each turn
3. **`agent_turn()`** — streams a single model API call, dispatches tool use, returns when the model produces a final text answer or requests tool results
4. **Tool handlers** — execute the requested tool and return results to the model
5. **`confirm()`** — gates `run_command` execution; auto-approves in yolo mode unless the command matches `DANGEROUS_PATTERNS`

The inner loop in `agent_loop` calls `agent_turn` repeatedly until the model produces a text-only response (no tool use), with a `MAX_STEPS` guard (default 20) to prevent runaway loops.

## Tools

The model has six tools. Read-only tools run without confirmation; mutating tools always require it.

**Read-only (no confirmation):**
- **`read_file`** — reads file contents with line numbers, supports `offset`/`limit` for paging. Reports total line count and file size.
- **`list_directory`** — lists directory entries with type indicators and file sizes. Optional `hidden` flag.
- **`search_files`** — regex search over file contents using ripgrep (falls back to grep). Supports glob filtering and result cap.

**Mutating (always require confirmation):**
- **`write_file`** — creates or overwrites a file. Shows a content preview and prompts `Apply? [Y/n]`. Creates parent directories automatically.
- **`edit_file`** — targeted find-and-replace in an existing file. `old_string` must match exactly once (fails if not found or ambiguous). Shows a `-`/`+` diff preview.
- **`run_command`** — arbitrary shell command execution. Prompts `Run? [Y/n]`. In yolo mode (`-y`), auto-approves unless the command matches `DANGEROUS_PATTERNS`.

## Key behaviours

- **Streaming** — model responses stream to the terminal as they're generated
- **Readline** — line editing and persistent history (`~/.agent_history`, 1000 entries)
- **Token tracking** — per-turn and session totals printed after each answer
- **Output truncation** — command output over 200 lines is cut to first/last 100 lines

## Model Names

Vertex AI Anthropic models use bare names without `@date` suffixes: `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-6`.
