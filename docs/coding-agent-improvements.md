# Coding Agent Improvements

Recommendations for making llm-agent a more effective coding agent, grouped by impact area.

## 1. ~~Smarter file editing~~ (DONE — v0.12.0)

All three improvements have been implemented in `edit_file.py`:

- ~~**Line-range editing**~~ — `start_line` + `end_line` + `new_string` parameters replace lines by number (1-based, inclusive). The model sees line numbers from `read_file` and can reference them directly.
- ~~**Multi-edit batching**~~ — `edits` array parameter accepts multiple operations (string match and/or line-range) applied atomically. All edits validated before any are applied; overlapping edits are rejected. Single unified diff preview with one confirmation prompt.
- ~~**Fuzzy matching fallback**~~ — When `old_string` has no exact match, whitespace-normalized matching is tried automatically (collapses runs of spaces/tabs, strips trailing whitespace). Preview shows "(matched after whitespace normalization)".

## 2. ~~Project context awareness~~ (DONE — v0.12.1)

All three improvements have been implemented in `context.py` + `agent.py`:

- ~~**Auto-detect project type**~~ — Detects `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `Gemfile`, `CMakeLists.txt`, `Makefile` and extracts project name. Injected into system prompt at startup via `refresh_project_context()`.
- ~~**Convention file support**~~ — Loads `AGENTS.md` from the working directory into the system prompt if present.
- ~~**Git context**~~ — Auto-includes current branch, uncommitted change count, and last 5 commit summaries.

## 3. Dedicated code navigation tools

The regex-based `search_files` works but is crude for code navigation.

- **`find_definition`** — Find where a symbol (function, class, variable) is defined. Could use `ctags`, tree-sitter, or even `grep` with language-aware patterns (e.g., `def foo`, `function foo`, `class Foo`). Much more reliable than raw regex for the model.
- **`find_references`** — Find all usages of a symbol. Distinct from grep because it can exclude definitions and comments.
- ~~**`file_outline`**~~ — DONE (v0.12.1). Regex-based parser for Python, JS/TS, Go, Rust, Java, Ruby, C/C++. Returns classes, functions, methods with line numbers and nesting.

`find_definition` and `find_references` could be implemented pragmatically with tree-sitter-based parsing for common languages, falling back to regex patterns.

## 4. Better context management

The current trimming (drop oldest messages when over 80% budget) loses important context.

- ~~**Summarize before discarding**~~ — DONE (v0.12.1). When trimming drops messages, they are first summarized by the model and the summary is prepended as a `[Earlier context summary]` message. Falls back to silent drop if the client is unavailable or the dropped content is trivial (<200 estimated tokens).
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

## 8. ~~Richer system prompt for coding~~ (DONE — v0.12.2, v0.12.3)

System prompt expanded from 42 to 65 lines. Now identifies as a "coding agent" rather than "Unix CLI assistant". New sections:

- ~~**Coding workflow**~~ — read related files and trace call paths, check for and run related tests after changes, fix bugs before moving on, consider edge cases.
- ~~**Style and consistency**~~ — match existing code style (quotes, indentation, naming conventions), don't refactor beyond what was asked.
- ~~**Git workflow**~~ — commit only when asked, check status/diff/log before committing, stage specific files, never amend/force-push unless asked, never push unless asked, PR creation via `gh`.
- ~~**Read before you write**~~ — expanded to include `file_outline` for large files.
- Existing "Minimal changes" and "Safety" sections retained.

## 9. Smaller but useful additions

- **Clipboard / scratch pad tool** — Let the agent store intermediate findings (e.g., "the auth function is at line 234 of server.py") without consuming conversation context for repeated lookups.
- **`diff_files` tool** — Compare two files or two versions of a file. Useful for understanding changes between branches or reviewing modifications.
- **Streaming confirmation in TUI** — Show the edit preview *as the model generates it* rather than waiting for the full tool call. This gives the user earlier visibility into what the agent plans to do.
- ~~**TUI input wrapping**~~ — DONE (v0.12.1). Input now uses `TextArea` with soft wrapping and auto-grow (up to 8 lines) instead of single-line `Input`. Enter submits, Shift+Enter for newlines.

## Prioritisation

Highest impact with minimum effort:

1. ~~**Line-range editing**~~ — DONE
2. ~~**File outline tool**~~ — DONE
3. ~~**Project context auto-detection**~~ — DONE
4. ~~**Conversation summarization on trim**~~ — DONE
