# Tool Plan: File Mutation

## Scope

This plan covers:

- `llm_agent.tools.edit_file`
- `llm_agent.tools.write_file`

## Current State

The current mutation layer is intentionally simple:

- `edit_file` supports exact string replacement, fuzzy whitespace-normalized replacement, line-range replacement, and atomic batches.
- `write_file` creates or overwrites a file after confirmation.
- both tools show previews before applying changes.

This is usable, but it lacks several safety and fidelity features that the Gemini, Anthropic, and Codex approaches have.

## What The Other Systems Do Better

### Gemini CLI

Useful ideas:

- stale-read protection before writes
- preservation of line endings and content fidelity
- optional LLM-based correction of malformed write payloads
- richer diff confirmation and diff statistics

Main references:

- `~/src/gemini-cli/packages/core/src/tools/edit.ts`
- `~/src/gemini-cli/packages/core/src/tools/write-file.ts`

### Anthropic `aitool`

Useful ideas:

- stronger input validation around nonexistent files and ambiguous edits
- secret-guard and permission-aware validation
- file-history tracking and git-diff integration
- better handling of large files, encodings, and unexpected modification races

Main references:

- `~/src/aitool/src/tools/FileEditTool/FileEditTool.ts`
- `~/src/aitool/src/tools/FileWriteTool/FileWriteTool.ts`

### Codex

Useful ideas:

- an `apply_patch` tool with a structured grammar rather than "string replacement as tool contract"
- patch-level approval semantics
- file-oriented diff envelopes that are easier for the model to reason about safely

Main references:

- `~/src/codex/codex-rs/tools/src/apply_patch_tool.rs`
- `~/src/codex/codex-rs/core/src/tools/runtimes/apply_patch.rs`

## Recommendations For This Repo

### Phase 1: Add safety checks around existing tools

- require a fresh read before mutating an existing file
- track file mtimes or read timestamps in session state
- reject edits if the file changed after it was last read
- preserve original line endings where practical

This is the highest-value idea to borrow from Gemini CLI and `aitool`.

### Phase 2: Improve previews and result quality

- show more structured diff summaries
- report line counts changed
- preserve encoding and newline style when possible
- detect obviously malformed omission placeholders before writing

### Phase 3: Add a patch-based mutation tool

- keep `edit_file` and `write_file` for simplicity
- add a new `apply_patch`-style tool for larger or multi-file edits
- use a constrained patch grammar rather than raw shell patching

This is the main idea worth borrowing from Codex.

### Phase 4: Consider optional smarter recovery

- if an edit fails because the match is close but not exact, provide stronger recovery guidance
- optionally add a bounded edit-correction path for malformed model output

This should stay optional. The default path should remain deterministic.

## Proposed Deliverables

1. Add read-before-write freshness tracking.
2. Improve newline and encoding preservation.
3. Add richer preview metadata to `edit_file` and `write_file`.
4. Design and implement an `apply_patch` tool.
5. Add race-condition and stale-read test coverage.

## Test Plan

- extend `tests/tools/test_edit_file.py`
- extend `tests/tools/test_write_file.py`
- add freshness/race tests
- add patch-grammar tests if a new patch tool lands

## Suggested Start

Start with read-before-write freshness checks. That gives the biggest safety improvement without forcing a tool-schema redesign.

