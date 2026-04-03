# Tool Plan: Shell and Background Execution

## Scope

This plan covers:

- `llm_agent.tools.run_command`
- `llm_agent.tools.check_task`
- shell state and background-task handling in `llm_agent.tools.base`

## Current State

The current shell layer is deliberately small:

- `run_command` executes a shell string and always requires confirmation.
- a simple `is_dangerous()` classifier blocks YOLO auto-approval for obviously risky commands.
- background execution is a boolean flag on the same tool.
- `check_task` is a polling tool that returns task status and truncated output.

This is easy to understand, but it is much less capable than the shell systems in Gemini CLI, `aitool`, and Codex.

## What The Other Systems Do Better

### Gemini CLI

Useful ideas:

- explicit shell-execution object model rather than a single string-only contract
- stronger policy integration for sandboxing and permissions
- structured background-process tools instead of overloading one command tool
- better output streaming and tail-reading behavior

Main references:

- `~/src/gemini-cli/packages/core/src/tools/shell.ts`
- `~/src/gemini-cli/packages/core/src/tools/shellBackgroundTools.ts`

### Anthropic `aitool`

Useful ideas:

- command-semantics classification for read/search/list/silent commands
- auto-backgrounding for long-running commands
- better UI summaries for commands whose output should collapse
- deeper validation of shell behavior before execution

Main references:

- `~/src/aitool/src/tools/BashTool/BashTool.tsx`

### Codex

Useful ideas:

- PTY-backed exec sessions
- split `exec_command` and `write_stdin`
- structured escalation fields such as `sandbox_permissions`, `justification`, and `prefix_rule`
- better distinction between one-shot commands and interactive sessions

Main references:

- `~/src/codex/codex-rs/tools/src/local_tool.rs`
- `~/src/codex/codex-rs/core/src/tools/handlers/unified_exec.rs`

## Recommendations For This Repo

### Phase 1: Improve the current tools without changing the model contract much

- strengthen dangerous-command detection beyond the current token-based rules
- add better command summaries in tool logs
- split background task listing from task output reading more clearly in output formatting
- add per-task timestamps, runtime, and cwd metadata
- improve `check_task` to support "tail N lines" semantics

### Phase 2: Separate one-shot execution from interactive execution

- keep `run_command` for one-shot commands
- add a new interactive exec path for commands that need stdin or a PTY
- add a separate `write_stdin` or equivalent follow-up tool

This is the biggest concrete improvement we could copy from Codex.

### Phase 3: Improve background execution

- add better background-task state with explicit running, completed, failed, canceled states
- persist richer metadata about background tasks
- support reading incremental output instead of only one static blob
- consider auto-backgrounding long-running safe commands

This is the clearest idea to borrow from Gemini CLI and `aitool`.

### Phase 4: Improve approval ergonomics

- separate "dangerous command" classification from "needs escalation" classification
- allow more structured justification when a command needs broader permissions
- consider persistent approval rules for repeated benign command prefixes

## Proposed Deliverables

1. Improve `run_command` metadata and `check_task` output.
2. Add tailing support to `check_task`.
3. Introduce an interactive exec tool with session IDs.
4. Refactor background-task storage around explicit task records.
5. Add stronger approval and escalation semantics.

## Test Plan

- expand `tests/test_is_dangerous.py`
- add task-metadata coverage to `tests/tools/test_run_command.py`
- add incremental-output and tailing coverage to `tests/tools/test_check_task.py`
- add interactive-session tests if a PTY-based tool is introduced

## Suggested Start

Start with Phase 1 and the `check_task` improvements first. They are high-value, low-risk, and do not require reworking the provider integrations.

