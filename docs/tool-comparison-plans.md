# Tool Comparison Plans

This index captures the follow-up plans from comparing this repo's tool layer with:

- `~/src/gemini-cli`
- `~/src/aitool`
- `~/src/codex`

The goal is not to copy any one system wholesale. The goal is to preserve this repo's simple Python agent loop while selectively adopting the highest-value ideas from the larger tool ecosystems.

## Priority Order

1. Shell and background execution
2. File mutation
3. File reading, search, and navigation
4. Ask-user UX
5. Delegation and subagents

## Documents

- [tool-plan-shell-and-background-execution.md](tool-plan-shell-and-background-execution.md)
- [tool-plan-file-mutation.md](tool-plan-file-mutation.md)
- [tool-plan-file-reading-and-navigation.md](tool-plan-file-reading-and-navigation.md)
- [tool-plan-ask-user.md](tool-plan-ask-user.md)
- [tool-plan-delegation.md](tool-plan-delegation.md)

## Why These Areas

These are the tool families where the other systems expose meaningfully stronger behavior rather than just different platform plumbing:

- richer shell execution and approval models
- safer and more robust file editing
- better file-reading and code-navigation surfaces
- stronger structured user-input collection
- more capable subagent lifecycle management

## Working Principle

For each area, the intended approach is:

1. Keep a stable local tool contract where possible.
2. Import small, high-leverage ideas first.
3. Avoid architectural sprawl unless the payoff is clear.
4. Add tests alongside each capability upgrade.

