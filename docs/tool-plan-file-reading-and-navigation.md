# Tool Plan: File Reading and Navigation

## Scope

This plan covers:

- `llm_agent.tools.read_file`
- `llm_agent.tools.search_files`
- `llm_agent.tools.glob_files`
- `llm_agent.tools.list_directory`
- `llm_agent.tools.file_outline`

## Current State

The current read and discovery tools are intentionally lightweight:

- `read_file` reads a line range with line numbers
- `search_files` wraps `rg` or `grep`
- `glob_files` wraps Python globbing
- `list_directory` shows a flat directory listing
- `file_outline` uses regex-based symbol extraction

These tools are fast and understandable, but they leave a lot of navigation power on the table.

## What The Other Systems Do Better

### Gemini CLI

Useful ideas:

- stronger `grep` controls, including result modes and pagination-like behavior
- `read_many_files` for batched file reads with exclusions and filtering
- path-access validation integrated into the tool layer
- better structured result summaries

Main references:

- `~/src/gemini-cli/packages/core/src/tools/grep.ts`
- `~/src/gemini-cli/packages/core/src/tools/read-file.ts`
- `~/src/gemini-cli/packages/core/src/tools/read-many-files.ts`

### Anthropic `aitool`

Useful ideas:

- richer file-reading behavior for binary, notebook, image, and PDF content
- stronger guards against problematic device files
- LSP-backed navigation for definitions, references, hover, and document symbols

Main references:

- `~/src/aitool/src/tools/FileReadTool/FileReadTool.ts`
- `~/src/aitool/src/tools/LSPTool/LSPTool.ts`

### Codex

Useful ideas:

- stronger directory listing semantics, including pagination and depth
- more advanced read semantics for structural blocks
- a separate `tool_search` concept for discovering additional tool surfaces

Main references:

- `~/src/codex/codex-rs/core/src/tools/handlers/list_dir.rs`
- `~/src/codex/codex-rs/core/src/tools/handlers/read_file_tests.rs`
- `~/src/codex/codex-rs/core/templates/search_tool/tool_description.md`

## Recommendations For This Repo

### Phase 1: Improve the existing read tools

- add stronger range validation and clearer truncation guidance to `read_file`
- expand `search_files` with more control over output limits and maybe file-only mode
- add pagination or depth options to `list_directory`
- improve `glob_files` around exclusion and result shaping

### Phase 2: Add batched reading

- introduce a `read_many_files` tool
- support include and exclude patterns
- make it easy to read a small focused set of files in one tool call

This is the clearest idea to borrow from Gemini CLI.

### Phase 3: Strengthen code navigation

- improve `file_outline` if possible, but do not over-invest in regex parsing
- add an optional LSP-backed navigation tool for:
  - document symbols
  - go to definition
  - find references
  - hover

This is the strongest idea to borrow from `aitool`.

### Phase 4: Improve multimodal and non-text file reading where justified

- keep `read_file` text-first
- only expand to richer binary/notebook/PDF behavior if that solves common repo tasks

## Proposed Deliverables

1. Upgrade `read_file`, `search_files`, and `list_directory`.
2. Add `read_many_files`.
3. Decide whether to add an LSP-based navigation tool.
4. Keep `file_outline` as a fallback or quick heuristic even if LSP is added.

## Test Plan

- extend `tests/tools/test_read_file.py`
- extend `tests/tools/test_search_files.py`
- extend `tests/tools/test_list_directory.py`
- add `tests/tools/test_read_many_files.py` if introduced
- add dedicated tests for any new LSP-backed tool with a mockable backend

## Suggested Start

Start with `read_many_files` and better `search_files` result controls. Those improvements are likely to pay off immediately without needing editor or LSP infrastructure.

