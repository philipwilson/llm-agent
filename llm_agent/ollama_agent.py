"""Ollama agent turn: streaming via OpenAI-compatible API, tool dispatch, retry logic."""

import json
import os
import time

from llm_agent.debug import get_debug
from llm_agent.display import get_display
from llm_agent.formatting import dim, red, yellow
from llm_agent.models import ollama_model_name, max_output_tokens as _max_output_tokens
from llm_agent.openai_agent import _convert_tools, _to_openai_messages
from llm_agent.tools import TOOLS, TOOL_REGISTRY, dispatch_tool_calls


MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SYSTEM_PROMPT_FILE = os.path.join(SCRIPT_DIR, "system_prompt.txt")

with open(SYSTEM_PROMPT_FILE) as _f:
    SYSTEM_PROMPT = _f.read()


def ollama_agent_turn(client, model, messages, auto_approve=False, usage_totals=None,
                      tools=None, tool_registry=None, system_prompt=None):
    """Run a single Ollama model turn. Same contract as agent_turn()."""
    effective_tools = tools if tools is not None else TOOLS
    effective_registry = tool_registry if tool_registry is not None else TOOL_REGISTRY
    effective_system = system_prompt if system_prompt is not None else SYSTEM_PROMPT

    openai_tools = _convert_tools(effective_tools)
    openai_messages = _to_openai_messages(messages, effective_system)

    api_model = ollama_model_name(model)

    # Build API kwargs
    api_kwargs = {
        "model": api_model,
        "messages": openai_messages,
        "stream": True,
    }

    if openai_tools:
        api_kwargs["tools"] = openai_tools

    max_tokens = int(os.environ.get("OLLAMA_MAX_TOKENS", _max_output_tokens(model)))
    api_kwargs["max_tokens"] = max_tokens

    # Ollama may support stream_options for usage stats (0.5+)
    api_kwargs["stream_options"] = {"include_usage": True}

    # Stream the response
    content_blocks = []
    printed_text = False
    full_text = ""
    tool_call_accum = {}

    debug = get_debug()

    for attempt in range(MAX_RETRIES + 1):
        try:
            full_text = ""
            printed_text = False
            tool_call_accum = {}

            debug.log_api_request(
                model=model, provider="ollama",
                num_messages=len(openai_messages),
                num_tools=len(openai_tools),
            )
            _turn_start = time.monotonic()

            stream = client.chat.completions.create(**api_kwargs)

            usage_recorded = False

            for chunk in stream:
                # Track usage if available
                if not usage_recorded and chunk.usage and usage_totals is not None:
                    usage_totals["input"] = usage_totals.get("input", 0) + chunk.usage.prompt_tokens
                    usage_totals["output"] = usage_totals.get("output", 0) + chunk.usage.completion_tokens
                    usage_totals["last_input"] = chunk.usage.prompt_tokens
                    usage_recorded = True

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta

                # Text content
                if delta.content:
                    if not printed_text:
                        get_display().stream_start()
                        printed_text = True
                    get_display().stream_token(delta.content)
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

            debug.log_api_response(
                model=model,
                usage=dict(usage_totals) if usage_totals else None,
                content_types=[("text" if full_text else None)]
                              + [f"tool_call:{tc['name']}" for tc in tool_call_accum.values()],
                duration=time.monotonic() - _turn_start,
            )
            break  # success

        except Exception as e:
            error_name = type(e).__name__
            # Connection errors are common with local Ollama
            retryable = error_name in (
                "APIConnectionError", "APITimeoutError",
                "InternalServerError", "ConnectionError",
            )
            debug.log_api_error(model, e, attempt, will_retry=retryable and attempt < MAX_RETRIES)
            if retryable and attempt < MAX_RETRIES:
                delay = RETRY_DELAYS[attempt]
                get_display().error(f"\n{yellow(f'Ollama error: {e}. Retrying in {delay}s...')}")
                time.sleep(delay)
            elif retryable:
                get_display().error(f"\n{red(f'Ollama error after {MAX_RETRIES + 1} attempts: {e}')}")
                get_display().error(f"{dim('Is Ollama running? Try: ollama serve')}")
                return messages, True
            else:
                raise

    if printed_text:
        get_display().stream_end()

    # Estimate usage if not reported by Ollama
    if usage_totals is not None and not usage_recorded:
        from llm_agent.cli import estimate_tokens
        est_input = estimate_tokens(messages)
        est_output = len(full_text) // 4
        usage_totals["input"] = usage_totals.get("input", 0) + est_input
        usage_totals["output"] = usage_totals.get("output", 0) + est_output
        usage_totals["last_input"] = est_input

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

    # No tool calls -> model is done
    if not tool_call_accum:
        return messages, True

    # Dispatch tool calls (parallel when safe, sequential for confirmations)
    tool_uses = [b for b in content_blocks if b["type"] == "tool_use"]
    tool_results = dispatch_tool_calls(tool_uses, effective_registry, auto_approve)

    messages.append({"role": "user", "content": tool_results})
    return messages, False
