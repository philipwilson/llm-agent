# `agent.py` — Suggested Improvements

## 🐛 Bugs

### 1. Symlink detection order is wrong (line 328–332)
`os.stat()` follows symlinks, so `os.path.isdir()` is checked first. But `os.path.islink()` on line 330 will never be reached for symlinks to directories — and for symlinks to files, `os.stat()` already resolved the symlink, so the size shown is the target's size, not the link's. Use `os.lstat()` instead, and check `islink()` **before** `isdir()`.

```python
# Current (buggy)
st = os.stat(full)
if os.path.isdir(full):
    ...
elif os.path.islink(full):
    ...

# Fixed
st = os.lstat(full)
if os.path.islink(full):
    target = os.readlink(full)
    lines.append(f"  {name} -> {target}")
elif os.path.isdir(full):
    lines.append(f"  {name}/")
else:
    ...
```

### 2. `rg --max-count` doesn't do what you think (line 357)
`--max-count` limits matches **per file**, not total. A search across many files can still return far more than `max_results` lines. Use `rg --max-count 1` with a separate total cap, or pipe through `head`, or just rely on the post-hoc truncation you already do on line 387.

### 3. `search_files` grep fallback can still raise `TimeoutExpired` (line 373)
The inner `try/except` around the grep fallback catches `Exception` but `subprocess.TimeoutExpired` inherits from `SubprocessError`, which *does* inherit from `Exception` — so it's caught, but returned as a raw error object string rather than the friendly timeout message. Add an explicit `TimeoutExpired` handler in the fallback too.

### 4. Conversation history grows unboundedly (line 652)
`conversation = messages` keeps every tool call and result forever. Long sessions will eventually exceed the context window or become very expensive. Consider truncating or summarizing old turns.

---

## 🔒 Security

### 5. `DANGEROUS_PATTERNS` is easily bypassed
Simple substring matching can be evaded trivially:
- `r m -rf /` (spaces), `/bin/rm`, `command rm`, `$(rm ...)`, backticks, `perl -e 'unlink ...'`, `python -c 'import os; os.remove(...)'`, etc.

Consider switching to an **allowlist** approach (only permit specific prefixes), or at minimum use word-boundary-aware matching and also check for shell metacharacters like `$()` and backticks piping to `sh`/`bash`.

### 6. `shell=True` without sanitization (line 269)
`run_command` passes user-influenced strings directly to a shell. This is by design for a CLI agent, but combined with auto-approve mode (`--yolo`), it means the LLM can execute arbitrary commands with only the weak `DANGEROUS_PATTERNS` check as a guard. Document this risk prominently or consider sandboxing (e.g., running commands in a container or with `bwrap`).

### 7. No path restriction on `write_file` / `edit_file`
The agent can write to any path the user has permissions for — `~/.bashrc`, `~/.ssh/authorized_keys`, etc. Consider restricting writes to the current working directory subtree.

---

## 🏗️ Architecture & Robustness

### 8. Tool dispatch should use a registry, not if/elif (lines 561–582)
The `agent_turn` function has a long if/elif chain for tool dispatch. A dictionary mapping tool names to handler functions would be cleaner, more extensible, and less error-prone:

```python
TOOL_HANDLERS = {
    "read_file": handle_read_file,
    "list_directory": handle_list_directory,
    "search_files": handle_search_files,
    "write_file": handle_write_file,
    "edit_file": handle_edit_file,
    "run_command": handle_run_command,  # wrap confirm + run
}
```

### 9. No error handling around the streaming API call (line 494)
If the Vertex AI API returns a network error, rate-limit, or 500, the entire agent crashes. Wrap the streaming call in a try/except with retry logic (at least for transient errors like 429 and 503).

### 10. No `KeyboardInterrupt` handling during tool execution
If the user presses Ctrl+C during a long-running command or API call, the agent crashes entirely rather than gracefully returning to the prompt. Catch `KeyboardInterrupt` in the inner agent loop and treat it as "cancel this turn."

### 11. `content_blocks` state leaks between blocks (lines 488–533)
`current_tool_name` is never reset to `None` at the end of a tool block — only `current_tool_id` and `current_tool_input_json` are. This works by accident because the `if current_tool_id:` guard protects it, but it's fragile. Reset all related state together.

---

## ✨ Features

### 12. Add a `/clear` or `/reset` command
There's no way to reset conversation history without restarting the agent. A `/clear` command would let users start fresh within a session.

### 13. Add a `--timeout` flag
`COMMAND_TIMEOUT` is hardcoded to 30s. Some legitimate commands (e.g., `find /` or large builds) need more time. Make it configurable via CLI argument.

### 14. Support non-Vertex Anthropic API
The client is hardcoded to `AnthropicVertex`. Many users have direct Anthropic API keys. Consider auto-detecting based on which environment variables are set (`ANTHROPIC_API_KEY` vs `ANTHROPIC_VERTEX_PROJECT_ID`).

### 15. Add `--single` / `-c` mode for non-interactive use
Allow one-shot usage like `./agent.py -c "how much disk space is free?"` for scripting and piping.

### 16. Show cost estimate, not just token counts
Token counts (line 644–648) are useful, but approximate cost in dollars would be more immediately actionable, especially for opus.

---

## 🧹 Code Quality

### 17. Extract TOOLS schema from handler functions
The tool definitions (lines 90–236) are 150 lines of boilerplate that duplicate information already implicit in the handler functions. Consider generating them from decorated functions or at least co-locating each schema with its handler.

### 18. `format_tokens` duplicates size formatting logic (lines 335–340 vs 597–602)
`handle_list_directory` has its own size formatter and `format_tokens` does the same thing for token counts. Extract a shared `human_readable(n, suffix="")` helper.

### 19. Magic strings for tool result messages
Strings like `"(user declined to run this command)"` and `"(no output)"` are used as both control flow signals and user-visible messages. Consider using constants or a result dataclass.

### 20. `handle_write_file` preview math is off (line 416)
When a file has more than 10 lines, the preview shows the first 5 and last 5 but says `{len(lines) - 10} more lines` — this is correct arithmetically, but if the file is exactly 11 lines, it says "1 more lines" (should be "1 more line"). Minor, but easy to fix with a simple plural helper.

---

## Summary

| Category       | Count | Severity   |
|----------------|-------|------------|
| Bugs           | 4     | Medium–High |
| Security       | 3     | High        |
| Architecture   | 4     | Medium      |
| Features       | 5     | Low–Medium  |
| Code Quality   | 4     | Low         |

The most impactful improvements would be fixing the **symlink bug (#1)**, adding **API error handling with retries (#9)**, implementing **path restrictions for writes (#7)**, and adding **Ctrl+C resilience (#10)**.
