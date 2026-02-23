"""OpenAI agent turn: streaming, tool dispatch, retry logic."""

import json
import os
import time

from llm_agent.formatting import dim, red, yellow
from llm_agent.tools import TOOLS, TOOL_REGISTRY


MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]

# Reasoning models use different parameters and roles
REASONING_MODELS = {"o3", "o4-mini", "o3-mini", "gpt-5.2"}

MAX_OUTPUT_TOKENS = {
    "gpt-4o": 16_384,
    "gpt-4o-mini": 16_384,
    "gpt-5.2": 128_000,
    "o3": 100_000,
    "o4-mini": 100_000,
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SYSTEM_PROMPT_FILE = os.path.join(SCRIPT_DIR, "system_prompt.txt")

with open(SYSTEM_PROMPT_FILE) as _f:
    SYSTEM_PROMPT = _f.read()


def _convert_tools(anthropic_tools):
    """Convert Anthropic tool schemas to OpenAI function tool format."""
    tools = []
    for tool in anthropic_tools:
        tools.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool["input_schema"],
            },
        })
    return tools


def _to_openai_messages(messages, system_prompt):
    """Convert Anthropic-format messages to OpenAI chat format.

    - Prepends system/developer message
    - Converts tool_use content blocks to assistant tool_calls
    - Converts tool_result content blocks to role=tool messages
    """
    # Reasoning models use 'developer' role instead of 'system'
    result = [{"role": "developer", "content": system_prompt}]

    for msg in messages:
        role = msg["role"]
        content = msg.get("content")

        if isinstance(content, str):
            result.append({"role": role, "content": content})
            continue

        if not isinstance(content, list):
            result.append({"role": role, "content": content})
            continue

        # Check what kind of blocks we have
        has_tool_use = any(b.get("type") == "tool_use" for b in content)
        has_tool_result = any(b.get("type") == "tool_result" for b in content)

        if has_tool_use:
            # Assistant message with tool calls
            text_parts = [b["text"] for b in content if b.get("type") == "text" and b.get("text")]
            tool_calls = []
            for b in content:
                if b.get("type") == "tool_use":
                    tool_calls.append({
                        "id": b["id"],
                        "type": "function",
                        "function": {
                            "name": b["name"],
                            "arguments": json.dumps(b.get("input", {})),
                        },
                    })
            msg_out = {"role": "assistant", "tool_calls": tool_calls}
            if text_parts:
                msg_out["content"] = "\n".join(text_parts)
            result.append(msg_out)

        elif has_tool_result:
            # Tool results → one role=tool message per result
            for b in content:
                if b.get("type") == "tool_result":
                    result.append({
                        "role": "tool",
                        "tool_call_id": b["tool_use_id"],
                        "content": b.get("content", ""),
                    })

        else:
            # Regular content blocks (text, images)
            text_parts = []
            openai_content = []
            for b in content:
                btype = b.get("type")
                if btype == "text":
                    text_parts.append(b["text"])
                elif btype in ("image", "document"):
                    source = b.get("source", {})
                    media_type = source.get("media_type", "image/png")
                    data = source.get("data", "")
                    openai_content.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{media_type};base64,{data}",
                        },
                    })

            if openai_content:
                # Multimodal message
                if text_parts:
                    openai_content.insert(0, {"type": "text", "text": "\n".join(text_parts)})
                result.append({"role": role, "content": openai_content})
            elif text_parts:
                result.append({"role": role, "content": "\n".join(text_parts)})

    return result


def openai_agent_turn(client, model, messages, auto_approve=False, usage_totals=None,
                      tools=None, tool_registry=None, system_prompt=None):
    """Run a single OpenAI model turn. Same contract as agent_turn()."""
    effective_tools = tools if tools is not None else TOOLS
    effective_registry = tool_registry if tool_registry is not None else TOOL_REGISTRY
    effective_system = system_prompt if system_prompt is not None else SYSTEM_PROMPT

    openai_tools = _convert_tools(effective_tools)
    openai_messages = _to_openai_messages(messages, effective_system)

    is_reasoning = model in REASONING_MODELS

    # Build API kwargs
    api_kwargs = {
        "model": model,
        "messages": openai_messages,
        "tools": openai_tools,
        "stream": True,
    }
    max_tokens = MAX_OUTPUT_TOKENS.get(model, 16_384)
    if is_reasoning:
        api_kwargs["max_completion_tokens"] = max_tokens
    else:
        api_kwargs["max_tokens"] = max_tokens

    # Stream the response
    content_blocks = []
    printed_text = False
    full_text = ""

    # Tool call accumulators: {index: {id, name, arguments}}
    tool_call_accum = {}

    for attempt in range(MAX_RETRIES + 1):
        try:
            full_text = ""
            printed_text = False
            tool_call_accum = {}

            stream = client.chat.completions.create(**api_kwargs)

            for chunk in stream:
                if not chunk.choices:
                    # Final chunk may have only usage
                    if chunk.usage and usage_totals is not None:
                        usage_totals["input"] += chunk.usage.prompt_tokens
                        usage_totals["output"] += chunk.usage.completion_tokens
                        cached = getattr(chunk.usage, "prompt_tokens_details", None)
                        if cached and getattr(cached, "cached_tokens", 0):
                            usage_totals["cache_read"] += cached.cached_tokens
                        usage_totals["last_input"] = chunk.usage.prompt_tokens
                    continue

                delta = chunk.choices[0].delta

                # Text content
                if delta.content:
                    if not printed_text:
                        print()
                        printed_text = True
                    print(delta.content, end="", flush=True)
                    full_text += delta.content

                # Tool calls
                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_call_accum:
                            tool_call_accum[idx] = {
                                "id": tc_delta.id or "",
                                "name": tc_delta.function.name if tc_delta.function and tc_delta.function.name else "",
                                "arguments": "",
                            }
                        else:
                            if tc_delta.id:
                                tool_call_accum[idx]["id"] = tc_delta.id
                            if tc_delta.function and tc_delta.function.name:
                                tool_call_accum[idx]["name"] = tc_delta.function.name
                        if tc_delta.function and tc_delta.function.arguments:
                            tool_call_accum[idx]["arguments"] += tc_delta.function.arguments

                # Usage in stream_options (if available on final chunk)
                if hasattr(chunk, "usage") and chunk.usage and usage_totals is not None:
                    usage_totals["input"] += chunk.usage.prompt_tokens
                    usage_totals["output"] += chunk.usage.completion_tokens
                    usage_totals["last_input"] = chunk.usage.prompt_tokens

            break  # success

        except Exception as e:
            error_name = type(e).__name__
            retryable = error_name in (
                "RateLimitError", "InternalServerError",
                "APIConnectionError", "APITimeoutError",
            )
            if retryable and attempt < MAX_RETRIES:
                delay = RETRY_DELAYS[attempt]
                print(f"\n{yellow(f'API error: {e}. Retrying in {delay}s...')}")
                time.sleep(delay)
            elif retryable:
                print(f"\n{red(f'API error after {MAX_RETRIES + 1} attempts: {e}')}")
                return messages, True
            else:
                raise

    if printed_text:
        print()

    # Build Anthropic-format content blocks for internal storage
    content_blocks = []
    if full_text:
        content_blocks.append({"type": "text", "text": full_text})

    for idx in sorted(tool_call_accum):
        tc = tool_call_accum[idx]
        tool_input = json.loads(tc["arguments"]) if tc["arguments"] else {}
        content_blocks.append({
            "type": "tool_use",
            "id": tc["id"],
            "name": tc["name"],
            "input": tool_input,
        })

    messages.append({"role": "assistant", "content": content_blocks})

    # No tool calls → model is done
    if not tool_call_accum:
        return messages, True

    # Dispatch tool calls
    tool_uses = [b for b in content_blocks if b["type"] == "tool_use"]
    tool_results = []
    for tool_use in tool_uses:
        name = tool_use["name"]
        params = tool_use["input"]
        entry = effective_registry.get(name)
        if entry is None:
            output = f"(unknown tool: {name})"
        else:
            log_fn = entry.get("log")
            if log_fn:
                log_fn(params)
            if entry.get("needs_confirm"):
                output = entry["handler"](params, auto_approve=auto_approve)
            else:
                output = entry["handler"](params)

        print(dim(f"  → {len(output.splitlines())} lines of output"))
        tool_results.append({
            "type": "tool_result",
            "tool_use_id": tool_use["id"],
            "content": output,
        })

    messages.append({"role": "user", "content": tool_results})
    return messages, False
