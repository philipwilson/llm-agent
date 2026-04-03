# Tool Plan: Delegation and Subagents

## Scope

This plan covers:

- `llm_agent.tools.delegate`
- `llm_agent.agents`
- subagent lifecycle behavior in the main session flow

## Current State

The current delegation system is intentionally thin:

- one `delegate` call spawns one subagent task
- subagents get a filtered tool set
- there is no nested delegation
- the parent gets back the subagent result as one response

This is enough for simple "explore" and "code" delegation, but it is much simpler than the agent systems in `aitool` and Codex.

## What The Other Systems Do Better

### Anthropic `aitool`

Useful ideas:

- richer agent input schema
- optional background execution
- more explicit lifecycle and progress reporting
- isolation options such as worktrees
- more configurable agent types and prompts

Main references:

- `~/src/aitool/src/tools/AgentTool/AgentTool.tsx`
- `~/src/aitool/src/tools/TaskOutputTool/TaskOutputTool.tsx`

### Codex

Useful ideas:

- separate tools for spawn, send input, wait, and close
- better lifecycle events for agent interactions
- more explicit multi-agent protocol instead of a single monolithic delegate tool

Main references:

- `~/src/codex/codex-rs/core/src/tools/handlers/multi_agents_v2.rs`

### Gemini CLI

Gemini CLI is less directly comparable here. The higher-value delegation ideas mostly come from `aitool` and Codex.

## Recommendations For This Repo

### Phase 1: Improve observability

- return clearer metadata from delegated runs
- log which agent was used, which model it used, and how long it took
- surface better subagent progress in the display layer

### Phase 2: Improve the contract

- keep `delegate` for compatibility
- consider a richer payload that can eventually support:
  - model override
  - background execution
  - clearer task descriptions

### Phase 3: Add lifecycle tools only if needed

- if delegation grows substantially, split responsibilities into:
  - spawn
  - wait
  - maybe send-input or resume

This should only happen if the simple one-shot `delegate` model becomes a real constraint.

### Phase 4: Consider isolation

- worktree-based subagent isolation is the main advanced feature worth considering
- do not attempt remote-agent orchestration unless there is a concrete use case

## What Not To Do

- do not copy the full complexity of `aitool` or Codex unless the repo actually needs it
- do not turn delegation into a second orchestration framework without a clear problem

## Proposed Deliverables

1. Improve delegated-run metadata and output.
2. Add better progress reporting for subagents.
3. Decide whether background subagents are worth adding.
4. Revisit tool-splitting only if one-shot delegation becomes limiting.

## Test Plan

- extend `tests/tools/test_delegate.py`
- extend `tests/integration/test_subagent.py`
- add coverage for any new metadata or lifecycle behavior

## Suggested Start

Start with better visibility and result metadata, not with new lifecycle tools. The current system is still structurally sound; it mainly lacks observability and richer control.

