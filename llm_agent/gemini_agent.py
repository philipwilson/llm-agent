"""Gemini agent turn: streaming, tool dispatch, retry logic."""

import base64
import os
import time

from llm_agent.display import get_display
from llm_agent.formatting import dim, red, yellow
from llm_agent.tools import TOOLS, TOOL_REGISTRY, dispatch_tool_calls


MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SYSTEM_PROMPT_FILE = os.path.join(SCRIPT_DIR, "system_prompt.txt")

with open(SYSTEM_PROMPT_FILE) as _f:
    SYSTEM_PROMPT = _f.read()


def _convert_tools(anthropic_tools):
    """Convert Anthropic tool schemas to Gemini function declarations."""
    from google.genai import types

    # Keys in JSON Schema that Gemini's FunctionDeclaration doesn't accept
    _STRIP_KEYS = {"$schema", "additionalProperties"}

    def _clean_schema(schema):
        """Recursively remove unsupported keys from a JSON Schema dict."""
        if not isinstance(schema, dict):
            return schema
        cleaned = {k: v for k, v in schema.items() if k not in _STRIP_KEYS}
        # Recurse into nested schemas (properties, items, etc.)
        if "properties" in cleaned:
            cleaned["properties"] = {
                k: _clean_schema(v) for k, v in cleaned["properties"].items()
            }
        if "items" in cleaned and isinstance(cleaned["items"], dict):
            cleaned["items"] = _clean_schema(cleaned["items"])
        return cleaned

    declarations = []
    for tool in anthropic_tools:
        declarations.append(types.FunctionDeclaration(
            name=tool["name"],
            description=tool.get("description", ""),
            parameters=_clean_schema(tool["input_schema"]),
        ))
    return [types.Tool(function_declarations=declarations)]


def _to_gemini_contents(messages):
    """Convert Anthropic-format messages to Gemini Content objects.

    Assistant messages that carry ``_gemini_parts`` (raw Part objects from the
    streaming response) are replayed directly so that metadata such as thought
    signatures is preserved.
    """
    from google.genai import types

    contents = []
    for msg in messages:
        role = "model" if msg["role"] == "assistant" else "user"

        # Replay raw Gemini parts when available (preserves thought signatures)
        if "_gemini_parts" in msg:
            contents.append(types.Content(role=role, parts=msg["_gemini_parts"]))
            continue

        content = msg.get("content")
        parts = []

        if isinstance(content, str):
            parts.append(types.Part(text=content))
        elif isinstance(content, list):
            for block in content:
                btype = block.get("type")
                if btype == "text":
                    parts.append(types.Part(text=block["text"]))
                elif btype in ("image", "document"):
                    source = block["source"]
                    raw = base64.b64decode(source["data"])
                    parts.append(types.Part.from_bytes(
                        data=raw,
                        mime_type=source["media_type"],
                    ))
                elif btype == "tool_use":
                    parts.append(types.Part(
                        function_call=types.FunctionCall(
                            name=block["name"],
                            args=block.get("input", {}),
                        )
                    ))
                elif btype == "tool_result":
                    parts.append(types.Part(
                        function_response=types.FunctionResponse(
                            name=block.get("_name", "unknown"),
                            response={"result": block.get("content", "")},
                        )
                    ))

        if parts:
            contents.append(types.Content(role=role, parts=parts))

    return contents


def gemini_agent_turn(client, model, messages, auto_approve=False, usage_totals=None,
                      thinking_level=None, tools=None, tool_registry=None,
                      system_prompt=None):
    """Run a single Gemini model turn. Same contract as agent_turn()."""
    from google.genai import types

    # Resolve tools, registry, and system prompt (use defaults if not provided)
    effective_tools = tools if tools is not None else TOOLS
    effective_registry = tool_registry if tool_registry is not None else TOOL_REGISTRY
    effective_system = system_prompt if system_prompt is not None else SYSTEM_PROMPT

    contents = _to_gemini_contents(messages)

    thinking_config = None
    if thinking_level:
        level_map = {
            "low": types.ThinkingLevel.LOW,
            "medium": types.ThinkingLevel.MEDIUM,
            "high": types.ThinkingLevel.HIGH,
        }
        thinking_config = types.ThinkingConfig(thinking_level=level_map[thinking_level])

    config = types.GenerateContentConfig(
        system_instruction=effective_system,
        tools=_convert_tools(effective_tools),
        thinking_config=thinking_config,
    )

    function_calls = []
    raw_parts = []      # preserve raw Parts for replay (thought signatures etc.)
    full_text = ""
    printed_text = False
    last_usage = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            function_calls = []
            raw_parts = []
            full_text = ""
            printed_text = False
            last_usage = None

            for chunk in client.models.generate_content_stream(
                model=model, contents=contents, config=config
            ):
                if chunk.candidates:
                    candidate = chunk.candidates[0]
                    if candidate.content and candidate.content.parts:
                        for part in candidate.content.parts:
                            raw_parts.append(part)
                            if part.text:
                                if not printed_text:
                                    get_display().stream_start()
                                    printed_text = True
                                get_display().stream_token(part.text)
                                full_text += part.text
                            if part.function_call:
                                function_calls.append(part.function_call)
                if chunk.usage_metadata:
                    last_usage = chunk.usage_metadata

            break  # success

        except Exception as e:
            error_name = type(e).__name__
            if error_name == "ClientError" and "not supported" in str(e).lower():
                get_display().error(f"\n{red(str(e))}")
                return messages, True
            retryable = error_name in (
                "ResourceExhausted", "InternalServerError",
                "ServiceUnavailable", "TooManyRequests",
            )
            if retryable and attempt < MAX_RETRIES:
                delay = RETRY_DELAYS[attempt]
                get_display().error(f"\n{yellow(f'API error: {e}. Retrying in {delay}s...')}")
                time.sleep(delay)
            elif retryable:
                get_display().error(f"\n{red(f'API error after {MAX_RETRIES + 1} attempts: {e}')}")
                return messages, True
            else:
                raise

    if printed_text:
        get_display().stream_end()

    # Track usage
    if usage_totals is not None and last_usage:
        usage_totals["input"] += getattr(last_usage, "prompt_token_count", 0) or 0
        usage_totals["output"] += getattr(last_usage, "candidates_token_count", 0) or 0
        usage_totals["last_input"] = getattr(last_usage, "prompt_token_count", 0) or 0

    # Build Anthropic-format content blocks for internal use (tool dispatch),
    # but also stash raw_parts for faithful Gemini replay on next turn.
    content_blocks = []
    if full_text:
        content_blocks.append({"type": "text", "text": full_text})
    for i, fc in enumerate(function_calls):
        content_blocks.append({
            "type": "tool_use",
            "id": f"gemini_{i}",
            "name": fc.name,
            "input": dict(fc.args) if fc.args else {},
        })

    messages.append({
        "role": "assistant",
        "content": content_blocks,
        "_gemini_parts": raw_parts,
    })

    # No function calls → model is done
    if not function_calls:
        return messages, True

    # Dispatch tool calls (parallel when safe, sequential for confirmations)
    tool_uses = [b for b in content_blocks if b["type"] == "tool_use"]
    tool_results = dispatch_tool_calls(tool_uses, effective_registry, auto_approve)
    # Stash tool names for Gemini FunctionResponse conversion
    for result, use in zip(tool_results, tool_uses):
        result["_name"] = use["name"]

    messages.append({"role": "user", "content": tool_results})
    return messages, False
