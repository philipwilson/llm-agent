# `agent.py` — Suggested Improvements

## 🐛 Bugs

### ~~1. Symlink detection order is wrong~~ ✅ Fixed
### ~~2. `rg --max-count` doesn't do what you think~~ ✅ Fixed
### ~~3. `search_files` grep fallback can still raise `TimeoutExpired`~~ ✅ Fixed
### ~~4. Conversation history grows unboundedly~~ ✅ Fixed

---

## 🔒 Security

### 5. `DANGEROUS_PATTERNS` is easily bypassed
Simple substring matching can be evaded trivially:
- `r m -rf /` (spaces), `/bin/rm`, `command rm`, `$(rm ...)`, backticks, `perl -e 'unlink ...'`, `python -c 'import os; os.remove(...)'`, etc.

Consider switching to an **allowlist** approach (only permit specific prefixes), or at minimum use word-boundary-aware matching and also check for shell metacharacters like `$()` and backticks piping to `sh`/`bash`.

### 6. `shell=True` without sanitization
`run_command` passes user-influenced strings directly to a shell. This is by design for a CLI agent, but combined with auto-approve mode (`--yolo`), it means the LLM can execute arbitrary commands with only the weak `DANGEROUS_PATTERNS` check as a guard. Document this risk prominently or consider sandboxing (e.g., running commands in a container or with `bwrap`).

### 7. No path restriction on `write_file` / `edit_file`
The agent can write to any path the user has permissions for — `~/.bashrc`, `~/.ssh/authorized_keys`, etc. Consider restricting writes to the current working directory subtree.

---

## 🏗️ Architecture & Robustness

### ~~8. Tool dispatch should use a registry, not if/elif~~ ✅ Done
### ~~9. No error handling around the streaming API call~~ ✅ Fixed
### ~~10. No `KeyboardInterrupt` handling during tool execution~~ ✅ Fixed
### ~~11. `content_blocks` state leaks between blocks~~ ✅ Fixed

---

## ✨ Features

### ~~12. Add a `/clear` or `/reset` command~~ ✅ Done
### ~~13. Add a `--timeout` flag~~ ✅ Done
### ~~14. Support non-Vertex Anthropic API~~ ✅ Done
### ~~15. Add `-c` mode for non-interactive use~~ ✅ Done

### 16. Show cost estimate, not just token counts
Token counts are useful, but approximate cost in dollars would be more immediately actionable, especially for opus.

---

## 🧹 Code Quality

### 17. Extract TOOLS schema from handler functions
The tool definitions are ~150 lines of boilerplate that duplicate information already implicit in the handler functions. Consider generating them from decorated functions or at least co-locating each schema with its handler.

### 18. `format_tokens` duplicates size formatting logic
`handle_list_directory` has its own size formatter and `format_tokens` does the same thing for token counts. Extract a shared `human_readable(n, suffix="")` helper.

### 19. Magic strings for tool result messages
Strings like `"(user declined to run this command)"` and `"(no output)"` are used as both control flow signals and user-visible messages. Consider using constants or a result dataclass.

### 20. `handle_write_file` preview math is off
When a file has exactly 11 lines, the preview says "1 more lines" (should be "1 more line"). Minor plural fix.

---

## Summary

| Category       | Total | Done | Remaining |
|----------------|-------|------|-----------|
| Bugs           | 4     | 4    | 0         |
| Security       | 3     | 0    | 3         |
| Architecture   | 4     | 4    | 0         |
| Features       | 5     | 4    | 1         |
| Code Quality   | 4     | 0    | 4         |
| **Total**      | **20**| **12**| **8**    |
