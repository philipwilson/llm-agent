# Conversation Data Structure

The conversation is a **plain Python list of message dictionaries**, following the Anthropic message format internally (even when using Gemini).

## Initialization

```python
conversation = []
```

## Message Types

There are essentially 3 kinds of messages that get appended to this list:

---

### 1. User Message

**Simple (no attachments):**
```python
{"role": "user", "content": "What's in cli.py?"}
```

**With attachments (`@file` syntax):**
```python
{"role": "user", "content": [
    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "..."}},
    {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "..."}},
    {"type": "text", "text": "Analyze these files."}
]}
```

The `content` field is either a **string** or a **list of typed content blocks**.

---

### 2. Assistant Message

Always a list of content blocks — a mix of text and tool_use blocks:

```python
{"role": "assistant", "content": [
    {"type": "text", "text": "Let me read that file..."},
    {"type": "tool_use", "id": "toolu_01234567890", "name": "read_file",
     "input": {"path": "llm_agent/cli.py", "limit": 50}}
]}
```

For **Gemini**, an extra `_gemini_parts` key stores raw Gemini `Part` objects for faithful replay on subsequent turns:
```python
{"role": "assistant", "content": [...], "_gemini_parts": [<raw Gemini Part objects>]}
```

---

### 3. Tool Results (as a User Message)

Tool results are sent back as a **user message** containing `tool_result` blocks — one per tool call:

```python
{"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "toolu_01234567890",
     "content": "[llm_agent/cli.py: 441 lines]\n1  #!/usr/bin/env python3\n..."},
    {"type": "tool_result", "tool_use_id": "toolu_09876543210",
     "_name": "search_files",   # Gemini only — needed for FunctionResponse conversion
     "content": "3 matches found..."}
]}
```

---

## Full Conversation Example (Multi-Turn)

After a user asks a question and the agent calls a tool then responds:

```python
[
    # 1. User's question
    {"role": "user", "content": "What's in cli.py?"},

    # 2. Assistant decides to use a tool
    {"role": "assistant", "content": [
        {"type": "text", "text": "Let me read that file..."},
        {"type": "tool_use", "id": "toolu_abc", "name": "read_file",
         "input": {"path": "llm_agent/cli.py", "limit": 50}}
    ]},

    # 3. Tool result comes back as a user message
    {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_abc",
         "content": "[llm_agent/cli.py: 441 lines]\n1  #!/usr/bin/env..."}
    ]},

    # 4. Assistant gives final answer (no more tool calls)
    {"role": "assistant", "content": [
        {"type": "text", "text": "cli.py is the main entry point. It contains..."}
    ]}
]
```

---

## Conversation Management

**Trimming** (`trim_conversation` in `cli.py`): When input tokens exceed 80% of the model's context window, entire **rounds** (user + assistant + tool results) are removed from the **front** of the list, preserving the most recent context.

**Caching** (`_cache_messages` in `agent.py`): Before each API call, the last message in the conversation gets a `cache_control: {"type": "ephemeral"}` annotation added to its last content block, so the growing conversation prefix is cached across successive calls within a single question.

**Subagents**: Start with a completely fresh `[{"role": "user", "content": task}]` list, run independently, and only their final text answer is returned as a tool result string.

---

## Key Design Insight

The conversation is a **flat list** that strictly alternates `user` → `assistant` → `user` → `assistant`... where tool results masquerade as `user` messages. This matches the Anthropic API's expected format. For Gemini, the list is converted at the API boundary via `_to_gemini_contents()`, then results are converted back to Anthropic format for internal storage.
