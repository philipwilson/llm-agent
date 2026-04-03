# Tool Plan: Ask User

## Scope

This plan covers:

- `llm_agent.tools.ask_user`
- related display and TUI support for structured user questions

## Current State

The current `ask_user` tool is intentionally simple:

- one question
- optional choices
- numeric answer normalization back to choice labels
- always sequential, always interactive

This works, but it is less expressive than the structured user-input tools in Gemini CLI, `aitool`, and Codex.

## What The Other Systems Do Better

### Gemini CLI

Useful ideas:

- multiple questions in one tool call
- stronger schema validation for question structure
- explicit answer mapping
- better return payload shape

Main references:

- `~/src/gemini-cli/packages/core/src/tools/ask-user.ts`

### Anthropic `aitool`

Useful ideas:

- richer choice objects with explanations
- support for preview content in some UI modes
- better user-facing summaries after the user answers

Main references:

- `~/src/aitool/src/tools/AskUserQuestionTool/AskUserQuestionTool.tsx`

### Codex

Useful ideas:

- compact schema with headers, stable IDs, and recommended options
- availability gating by collaboration mode
- automatic "Other" handling at the UI layer

Main references:

- `~/src/codex/codex-rs/tools/src/request_user_input_tool.rs`

## Recommendations For This Repo

### Phase 1: Expand the schema conservatively

- support one to three questions in a single tool call
- add stable IDs for returned answers
- require labels and descriptions for multiple-choice options

### Phase 2: Improve the UX

- add short headers for questions
- render better answer summaries in the display layer
- keep the prompt flow readable in both readline and TUI modes

### Phase 3: Add small quality-of-life features

- allow a recommended option convention
- standardize free-text fallback handling
- consider an implicit "Other" path in the UI rather than the schema

## What Not To Do

- do not make `ask_user` so rich that it becomes a form engine
- do not require a large UI rewrite before the schema improves
- do not make subagents able to call it

## Proposed Deliverables

1. Extend `ask_user` to support multiple structured questions.
2. Update display/TUI handling.
3. Improve answer normalization and returned structure.

## Test Plan

- extend `tests/tools/test_ask_user.py`
- add display/TUI-level behavior coverage where possible
- add schema-validation tests for malformed multi-question payloads

## Suggested Start

Start by matching the Codex and Gemini shape: a small number of short, structured questions with labeled options and descriptions.

