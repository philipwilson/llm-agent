# How `agent.py` Turns a User Prompt into LLM Calls and Tool Use

## 1. User Input Enters the System

There are two entry points:

- **Interactive mode** (`agent_loop`, line 712): The user types at a `>` prompt in a REPL. The input is read via `input()` (line 721) and passed to `run_question()`.
- **One-shot mode** (`-c` flag, line 798): A single question is passed as a CLI argument directly to `run_question()`.

Either way, the raw user string arrives at `run_question()` (line 684).

---

## 2. Building the LLM Prompt (`run_question`)

`run_question` (line 684) does the following:

1. **Appends the user text** to the `conversation` list as a simple message:
   ```python
   conversation.append({"role": "user", "content": user_input})
   ```
2. **Copies the conversation** into a working `messages` list (so it can be extended with tool-call rounds without polluting the outer conversation until the turn succeeds).

The actual API call happens in `agent_turn()` (line 546), which calls `client.messages.stream()` with:

| Parameter     | Value |
|---------------|-------|
| `model`       | The resolved model ID (e.g. `claude-sonnet-4-6`) |
| `system`      | `SYSTEM_PROMPT` — a static string (line 68) telling Claude it's a Unix CLI assistant, listing the available tools and usage guidelines |
| `tools`       | `TOOLS` — a static list of 6 tool JSON schemas (lines 94–240) describing `read_file`, `list_directory`, `search_files`, `write_file`, `edit_file`, and `run_command` |
| `messages`    | The accumulated `messages` list (user turns + assistant turns + tool results) |
| `max_tokens`  | 65536 |

So the full LLM prompt is: **system prompt + tool definitions + the full message history**.

---

## 3. Streaming the LLM Response (`agent_turn`)

The response is consumed as a stream of server-sent events (lines 566–599). The code tracks state to reconstruct two kinds of content blocks:

### Text blocks
- On `content_block_start` with type `"text"`, it begins accumulating text.
- On `content_block_delta` with type `"text_delta"`, it **prints each chunk to stdout immediately** (`print(..., end="", flush=True)`) and appends it to `current_text`.
- On `content_block_stop`, the accumulated text is saved as `{"type": "text", "text": ...}` into `content_blocks`.

### Tool-use blocks
- On `content_block_start` with type `"tool_use"`, it captures the `id` and `name` of the tool call.
- On `content_block_delta` with type `"input_json_delta"`, it accumulates the JSON string of the tool's input parameters.
- On `content_block_stop`, it parses the accumulated JSON and saves a `{"type": "tool_use", "id": ..., "name": ..., "input": ...}` block.

A single response can contain **multiple** content blocks of both types (e.g., the model might emit text explaining what it's about to do, then one or more tool calls).

---

## 4. Decision: Tool Use or Done?

After streaming completes, `agent_turn` separates tool-use blocks from text blocks (line 636):

```python
tool_uses = [b for b in content_blocks if b["type"] == "tool_use"]
```

### Path A: No tool calls → **Done**

If `tool_uses` is empty, the model's text response is the final answer. The function returns `(messages, True)` — the `True` signals to the loop in `run_question` that the turn is complete. The text was already printed to stdout during streaming.

### Path B: Tool calls present → **Execute tools, then loop back**

If there are tool calls, the flow continues:

1. **The full assistant response** (text + tool-use blocks) is appended to `messages` as an `"assistant"` message (line 643).

2. **Each tool call is dispatched** via `TOOL_REGISTRY` (lines 514–539), a dict mapping tool names to handler functions:

   | Tool | Handler | Confirmation? |
   |------|---------|---------------|
   | `read_file` | `handle_read_file` | No |
   | `list_directory` | `handle_list_directory` | No |
   | `search_files` | `handle_search_files` | No |
   | `write_file` | `handle_write_file` | Yes (always) |
   | `edit_file` | `handle_edit_file` | Yes (always) |
   | `run_command` | `handle_run_command` | Yes (unless `--yolo` and non-dangerous) |

   - Read-only tools (`read_file`, `list_directory`, `search_files`) run immediately with a log line.
   - `write_file` and `edit_file` always show a diff-like preview and ask for `Y/n`.
   - `run_command` shows the command and asks for `Y/n` — unless `--yolo` mode is on **and** the command doesn't match any `DANGEROUS_PATTERNS` (line 59), in which case it auto-approves.

3. **Tool results are collected** into a list of `{"type": "tool_result", "tool_use_id": ..., "content": ...}` objects, and appended to `messages` as a `"user"` message (line 672). This is the Anthropic API's convention: tool results are sent back as user messages.

4. The function returns `(messages, False)` — the `False` tells `run_question` the model still needs to process the tool results.

---

## 5. The Agent Loop (`run_question`)

Back in `run_question` (line 684), there's a `while True` loop:

```python
while True:
    messages, done = agent_turn(client, model, messages, ...)
    if done:
        break
    steps += 1
    if steps >= MAX_STEPS:  # 20
        break
```

Each iteration is one round-trip to the LLM. The loop continues until:
- The model responds with **no tool calls** (it's satisfied and gives a text answer), or
- **20 tool-use steps** are hit (safety limit), or
- The user presses **Ctrl-C**.

So a single user question can trigger many LLM calls, each one seeing the growing message history that includes all prior tool calls and their results.

---

## 6. Conversation Continuity

After `run_question` returns, the updated `messages` list is saved back into the `conversation` variable in `agent_loop` (line 758). This means the **next** user question will include all prior context.

To prevent unbounded growth, the conversation is trimmed based on actual token usage. After each question, `trim_conversation()` checks whether the last API call's input token count exceeded 80% of the model's context window. If so, it removes the oldest complete message rounds (user + assistant/tool messages) until the estimated token savings cover the excess.

The user can also type `/clear` to reset the conversation to empty.

---

## Summary: The Complete Lifecycle

```
User types "what's in /tmp?"
        │
        ▼
run_question() appends {"role": "user", "content": "what's in /tmp?"}
        │
        ▼
agent_turn() calls Claude API with:
  system=SYSTEM_PROMPT, tools=TOOLS, messages=[...history..., user msg]
        │
        ▼
Claude streams back: text + tool_use(list_directory, {path: "/tmp"})
        │
        ├─► Text is printed live to terminal
        │
        ▼
Tool dispatched: handle_list_directory({path: "/tmp"})
  → returns directory listing string
        │
        ▼
Tool result appended to messages as {"role": "user", content: [tool_result]}
        │
        ▼
agent_turn() calls Claude API again with updated messages
        │
        ▼
Claude streams back: text summary of /tmp contents (no tool calls)
        │
        ├─► Text is printed live to terminal
        │
        ▼
done=True → loop exits → conversation saved for follow-ups
```
