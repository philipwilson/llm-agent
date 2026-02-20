# Future Improvements

## Usability

- **Cost estimates** — Show approximate cost in dollars alongside token counts, especially useful for opus.

## Architecture

- **Persistent shell session** — Use `pexpect` or a long-lived `subprocess.Popen` so `cd`, `export`, and other stateful commands persist between invocations.
- **System prompt from file** — Move `SYSTEM_PROMPT` to an external file so it can be iterated on without editing Python.
- **Co-locate tool schemas with handlers** — The tool definitions are ~150 lines of boilerplate separate from the handler functions. Consider generating schemas from decorated functions or co-locating each schema with its handler.
- **Shared size formatter** — `handle_list_directory` and `format_tokens` both format numbers with k/M suffixes independently. Extract a shared helper.

## Safety

- **Sandbox option** — Run commands inside a Docker container or `bwrap` so the agent can't damage the host filesystem.
- **Stronger dangerous command detection** — `DANGEROUS_PATTERNS` uses simple substring matching, easily bypassed with path prefixes, `$()`, backticks, etc. Consider word-boundary-aware matching or an allowlist approach.
- **Path restriction for writes** — `write_file` and `edit_file` can write to any path (`~/.bashrc`, `~/.ssh/`, etc.). Consider restricting to the current working directory subtree.

## Code Quality

- **Magic strings** — Strings like `"(user declined to run this command)"` are used as both control flow and user-visible messages. Consider constants or a result type.
- **Plural fix in write_file preview** — "1 more lines" should be "1 more line" when a file has exactly 11 lines.
