"""Agent turn: streaming, caching, retry logic."""

import json
import os
import time

import anthropic

from llm_agent.formatting import dim, red, yellow
from llm_agent.tools import TOOLS, TOOL_REGISTRY


MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]  # seconds between retries (exponential backoff)

CACHE_CONTROL = {"type": "ephemeral"}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SYSTEM_PROMPT_FILE = os.path.join(SCRIPT_DIR, "system_prompt.txt")

with open(SYSTEM_PROMPT_FILE) as _f:
    SYSTEM_PROMPT = _f.read()

CACHED_SYSTEM = [{
    "type": "text",
    "text": SYSTEM_PROMPT,
    "cache_control": CACHE_CONTROL,
}]

# Lazy tool cache — rebuilt on first use or after invalidation.
_CACHED_TOOLS = None


def _build_cached_tools():
    global _CACHED_TOOLS
    _CACHED_TOOLS = [*TOOLS[:-1], {**TOOLS[-1], "cache_control": CACHE_CONTROL}]
    return _CACHED_TOOLS


def invalidate_tool_cache():
    """Force the tool cache to rebuild on next use."""
    global _CACHED_TOOLS
    _CACHED_TOOLS = None


def _get_cached_tools():
    if _CACHED_TOOLS is None:
        return _build_cached_tools()
    return _CACHED_TOOLS


def _cache_messages(messages):
    """Add a cache breakpoint to the last message in the conversation.

    This ensures the growing conversation prefix is cached across
    successive calls within a single question.
    """
    if not messages:
        return messages
    msgs = [*messages[:-1]]
    last = messages[-1]
    content = last.get("content")
    if isinstance(content, str):
        # Simple string content -- wrap in a content block to add cache_control
        msgs.append({
            **last,
            "content": [{
                "type": "text",
                "text": content,
                "cache_control": CACHE_CONTROL,
            }],
        })
    elif isinstance(content, list) and content:
        # List of content blocks -- add cache_control to the last block
        cached_content = [*content[:-1], {**content[-1], "cache_control": CACHE_CONTROL}]
        msgs.append({**last, "content": cached_content})
    else:
        msgs.append(last)
    return msgs


def agent_turn(client, model, messages, auto_approve=False, usage_totals=None,
               tools=None, tool_registry=None, system_prompt=None):
    # Resolve tools, registry, and system prompt (use defaults if not provided)
    if tools is not None:
        effective_tools = [*tools[:-1], {**tools[-1], "cache_control": CACHE_CONTROL}] if tools else []
        effective_registry = tool_registry if tool_registry is not None else TOOL_REGISTRY
    else:
        effective_tools = _get_cached_tools()
        effective_registry = TOOL_REGISTRY

    if system_prompt is not None:
        effective_system = [{
            "type": "text",
            "text": system_prompt,
            "cache_control": CACHE_CONTROL,
        }]
    else:
        effective_system = CACHED_SYSTEM

    # Stream the response with retry logic for transient API errors
    content_blocks = []
    printed_text = False
    cached_msgs = _cache_messages(messages)

    for attempt in range(MAX_RETRIES + 1):
        try:
            content_blocks = []
            current_text = ""
            current_tool_input_json = ""
            current_tool_id = None
            current_tool_name = None

            with client.messages.stream(
                model=model,
                max_tokens=64000 if "haiku" in model else 65536,
                system=effective_system,
                tools=effective_tools,
                messages=cached_msgs,
            ) as stream:
                for event in stream:
                    if event.type == "content_block_start":
                        if event.content_block.type == "text":
                            current_text = ""
                            if not printed_text:
                                print()  # blank line before model output
                                printed_text = True
                        elif event.content_block.type == "tool_use":
                            current_tool_id = event.content_block.id
                            current_tool_name = event.content_block.name
                            current_tool_input_json = ""

                    elif event.type == "content_block_delta":
                        if event.delta.type == "text_delta":
                            print(event.delta.text, end="", flush=True)
                            current_text += event.delta.text
                        elif event.delta.type == "input_json_delta":
                            current_tool_input_json += event.delta.partial_json

                    elif event.type == "content_block_stop":
                        if current_text:
                            content_blocks.append({"type": "text", "text": current_text})
                            current_text = ""
                        if current_tool_id:
                            tool_input = json.loads(current_tool_input_json) if current_tool_input_json else {}
                            content_blocks.append({
                                "type": "tool_use",
                                "id": current_tool_id,
                                "name": current_tool_name,
                                "input": tool_input,
                            })
                            current_tool_id = None
                            current_tool_name = None
                            current_tool_input_json = ""

                # Get usage from the final message
                final = stream.get_final_message()
                if usage_totals is not None and final.usage:
                    usage_totals["input"] += final.usage.input_tokens
                    usage_totals["output"] += final.usage.output_tokens
                    cache_read = getattr(final.usage, "cache_read_input_tokens", 0) or 0
                    cache_create = getattr(final.usage, "cache_creation_input_tokens", 0) or 0
                    usage_totals["cache_read"] = usage_totals.get("cache_read", 0) + cache_read
                    usage_totals["cache_create"] = usage_totals.get("cache_create", 0) + cache_create
                    usage_totals["last_input"] = final.usage.input_tokens

            break  # success, exit retry loop

        except (anthropic.RateLimitError, anthropic.InternalServerError) as e:
            if attempt < MAX_RETRIES:
                delay = RETRY_DELAYS[attempt]
                print(f"\n{yellow(f'API error: {e}. Retrying in {delay}s...')}")
                time.sleep(delay)
                # Reset state for retry
                content_blocks = []
            else:
                print(f"\n{red(f'API error after {MAX_RETRIES + 1} attempts: {e}')}")
                return messages, True  # give up, return to prompt

        except anthropic.APIConnectionError as e:
            if attempt < MAX_RETRIES:
                delay = RETRY_DELAYS[attempt]
                print(f"\n{yellow(f'Connection error: {e}. Retrying in {delay}s...')}")
                time.sleep(delay)
                content_blocks = []
            else:
                print(f"\n{red(f'Connection error after {MAX_RETRIES + 1} attempts: {e}')}")
                return messages, True

    # End streaming text with a newline if we printed anything
    if printed_text:
        print()

    # Separate text blocks from tool_use blocks
    tool_uses = [b for b in content_blocks if b["type"] == "tool_use"]

    # If no tool use, the model is done
    if not tool_uses:
        return messages, True  # done

    # Append the full assistant response to messages
    messages.append({"role": "assistant", "content": content_blocks})

    # Process each tool use
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
        tool_results.append(
            {
                "type": "tool_result",
                "tool_use_id": tool_use["id"],
                "content": output,
            }
        )

    messages.append({"role": "user", "content": tool_results})
    return messages, False  # not done, model needs to see results
