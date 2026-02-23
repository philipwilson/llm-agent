# Coding Agent Improvements

Recommendations for making llm-agent a more effective coding agent, grouped by impact area.

## 1. ~~Smarter file editing~~ (DONE — v0.11.7)

All three improvements have been implemented in `edit_file.py`:

- ~~**Line-range editing**~~ — `start_line` + `end_line` + `new_string` parameters replace lines by number (1-based, inclusive). The model sees line numbers from `read_file` and can reference them directly.
- ~~**Multi-edit batching**~~ — `edits` array parameter accepts multiple operations (string match and/or line-range) applied atomically. All edits validated before any are applied; overlapping edits are rejected. Single unified diff preview with one confirmation prompt.
- ~~**Fuzzy matching fallback**~~ — When `old_string` has no exact match, whitespace-normalized matching is tried automatically (collapses runs of spaces/tabs, strips trailing whitespace). Preview shows "(matched after whitespace normalization)".

## 2. Project context awareness

The agent starts every session blind. It could automatically detect and surface project context.

- **Auto-detect project type** on first turn — look for `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `Makefile`, etc. Inject a brief project summary into the system prompt (language, framework, test command, entry points).
- **Convention file support** — Load a `.agent.md` or similar project-level instruction file (like CLAUDE.md) into the system prompt automatically. This already exists as CLAUDE.md for Claude Code, but `llm-agent` doesn't have its own equivalent.
- **Git context** — Auto-include current branch, recent commits, and dirty-file list at session start. The agent frequently needs this and currently has to `run_command` to get it.

## 3. Dedicated code navigation tools

The regex-based `search_files` works but is crude for code navigation.

- **`find_definition`** — Find where a symbol (function, class, variable) is defined. Could use `ctags`, tree-sitter, or even `grep` with language-aware patterns (e.g., `def foo`, `function foo`, `class Foo`). Much more reliable than raw regex for the model.
- **`find_references`** — Find all usages of a symbol. Distinct from grep because it can exclude definitions and comments.
- **`file_outline`** — Return the structure of a file (classes, functions, methods with line numbers) without reading the entire file. Lets the agent understand a 2000-line file without consuming 2000 lines of context.

These could be implemented pragmatically with tree-sitter-based parsing for common languages, falling back to regex patterns.

## 4. Better context management

The current trimming (drop oldest messages when over 80% budget) loses important context.

- **Summarize before discarding** — Before trimming old messages, ask the model (or a small/fast model) to produce a summary of what was discussed and decided. Prepend the summary as a system-injected message so the agent retains key context.
- **File content deduplication** — If the agent has read the same file 3 times across the conversation, only the most recent read needs to stay verbatim. Earlier reads can be collapsed to "read file X (see later read for current contents)."
- **Tool result compression** — Large `run_command` or `search_files` outputs could be summarized more aggressively after they've been processed. The 200-line truncation in `formatting.py` helps, but it's applied at generation time, not retroactively as context pressure grows.

## 5. Test loop integration

Effective coding agents run tests as a validation step. Currently the agent has to `run_command` and interpret raw output.

- **`run_tests` tool** — A dedicated tool that detects the test framework (pytest, jest, go test, cargo test, etc.), runs tests, and returns structured results (pass/fail counts, failing test names, failure messages). The model can then act on structured data rather than parsing terminal output.
- **Auto-test after edits** — Optionally run the relevant test suite after file modifications and report regressions, giving the agent a feedback loop without the user having to say "now run the tests."

## 6. Change tracking and rollback

The agent has no memory of what it's changed.

- **Track all modifications** — Maintain a session-level list of files written/edited, with before-snapshots. Display a summary on `/changes` or at session end.
- **`undo` command** — Revert the last file modification (or all modifications). Currently if the agent makes a bad edit, recovery requires manual intervention or a new `edit_file` call that the model has to figure out.
- **Git-based safety net** — Auto-stash or create a WIP commit before the agent starts modifying files, so the user can always `git checkout` to recover.

## 7. Parallel subagent execution

`run_subagent` in `agents.py` is synchronous — one subagent at a time. For coding tasks, you often want to explore multiple files or approaches simultaneously.

- **Parallel delegation** — Allow the model to spawn multiple `explore` subagents concurrently (e.g., "read the tests while I read the implementation"). This would require making `delegate` accept multiple tasks or supporting concurrent tool execution.

## 8. Richer system prompt for coding

The current `system_prompt.txt` is 41 lines and quite generic. For a coding-focused agent, it should include:

- Explicit instructions about the edit-test-verify cycle
- Guidance on reading code before modifying it (it has this, but briefly)
- Instructions to check for related tests when modifying code
- Reminders to handle edge cases the user didn't mention
- Style guidance (match existing code style, don't over-refactor)

## 9. Smaller but useful additions

- **Clipboard / scratch pad tool** — Let the agent store intermediate findings (e.g., "the auth function is at line 234 of server.py") without consuming conversation context for repeated lookups.
- **`diff_files` tool** — Compare two files or two versions of a file. Useful for understanding changes between branches or reviewing modifications.
- **Streaming confirmation in TUI** — Show the edit preview *as the model generates it* rather than waiting for the full tool call. This gives the user earlier visibility into what the agent plans to do.

## Prioritisation

Highest impact with minimum effort:

1. ~~**Line-range editing**~~ — DONE
2. **File outline tool** — moderate effort, dramatically reduces context consumption on large files
3. **Project context auto-detection** — small addition to `cli.py`, makes the agent useful faster in every session
4. **Conversation summarization on trim** — moderate effort, prevents the agent from "forgetting" important decisions mid-session
