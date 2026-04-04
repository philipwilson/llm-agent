"""Integration tests for ollama_agent_turn: mock the OpenAI-compatible streaming API,
verify tool dispatch and final answer extraction."""

import json
from types import SimpleNamespace

import pytest

from llm_agent.ollama_agent import ollama_agent_turn
from llm_agent.models import ollama_model_name as _ollama_model_name


# ---------------------------------------------------------------------------
# Helpers to build a fake OpenAI-compatible streaming response
# ---------------------------------------------------------------------------

def _make_chunk(content=None, tool_calls=None, usage=None, has_choices=True):
    """Create a SimpleNamespace mimicking an OpenAI streaming chunk."""
    if has_choices:
        delta = SimpleNamespace(content=content, tool_calls=tool_calls)
        choice = SimpleNamespace(delta=delta)
        return SimpleNamespace(choices=[choice], usage=usage)
    return SimpleNamespace(choices=[], usage=usage)


def _text_chunks(text, usage=None):
    """Yield streaming chunks for a simple text response."""
    # First chunk with content
    yield _make_chunk(content=text)
    # Final chunk with usage
    if usage:
        yield _make_chunk(has_choices=False, usage=usage)


def _tool_call_chunks(tool_id, tool_name, tool_input, usage=None):
    """Yield streaming chunks for a tool call."""
    fn_start = SimpleNamespace(name=tool_name, arguments="")
    tc = SimpleNamespace(index=0, id=tool_id, function=fn_start)
    yield _make_chunk(tool_calls=[tc])

    # Argument chunk
    fn_args = SimpleNamespace(name=None, arguments=json.dumps(tool_input))
    tc_delta = SimpleNamespace(index=0, id=None, function=fn_args)
    yield _make_chunk(tool_calls=[tc_delta])

    # Usage chunk
    if usage:
        yield _make_chunk(has_choices=False, usage=usage)


class FakeUsage:
    def __init__(self, prompt_tokens=100, completion_tokens=50):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.prompt_tokens_details = None


class FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self._call_count = 0

    def create(self, **kwargs):
        idx = min(self._call_count, len(self._responses) - 1)
        self._call_count += 1
        return iter(self._responses[idx])


class FakeChat:
    def __init__(self, responses):
        self.completions = FakeCompletions(responses)


class FakeClient:
    def __init__(self, responses):
        self.chat = FakeChat(responses)


# ---------------------------------------------------------------------------
# Tests — model name stripping
# ---------------------------------------------------------------------------

class TestOllamaModelName:
    def test_strip_prefix(self):
        assert _ollama_model_name("ollama:qwen3.5:122b") == "qwen3.5:122b"

    def test_no_prefix(self):
        assert _ollama_model_name("mistral") == "mistral"

    def test_only_prefix(self):
        assert _ollama_model_name("ollama:") == ""


# ---------------------------------------------------------------------------
# Tests — text responses
# ---------------------------------------------------------------------------

class TestOllamaTextOnly:
    def test_simple_text_response(self):
        """Model returns plain text — should be done immediately."""
        chunks = list(_text_chunks("Hello from Ollama!"))
        client = FakeClient([chunks])
        messages = [{"role": "user", "content": "Hi"}]
        usage = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}

        result_msgs, done = ollama_agent_turn(
            client, "ollama:qwen3.5:122b", messages, auto_approve=True,
            usage_totals=usage, tools=None, tool_registry={},
        )

        assert done is True
        assert len(result_msgs) == 2  # user + assistant
        assert result_msgs[-1]["role"] == "assistant"
        text_blocks = [b for b in result_msgs[-1]["content"] if b["type"] == "text"]
        assert text_blocks[0]["text"] == "Hello from Ollama!"

    def test_usage_tracked_from_stream(self):
        """Usage stats from Ollama's OpenAI-compat API are tracked."""
        chunks = list(_text_chunks("answer", FakeUsage(200, 80)))
        client = FakeClient([chunks])
        messages = [{"role": "user", "content": "question"}]
        usage = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}

        ollama_agent_turn(
            client, "ollama:mistral", messages, auto_approve=True,
            usage_totals=usage, tools=None, tool_registry={},
        )

        assert usage["input"] == 200
        assert usage["output"] == 80

    def test_usage_estimated_when_not_reported(self):
        """When Ollama doesn't report usage, it's estimated from message sizes."""
        # No usage in chunks
        chunks = list(_text_chunks("short answer"))
        client = FakeClient([chunks])
        messages = [{"role": "user", "content": "question"}]
        usage = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}

        ollama_agent_turn(
            client, "ollama:mistral", messages, auto_approve=True,
            usage_totals=usage, tools=None, tool_registry={},
        )

        # Should have estimated values (not zero)
        assert usage["input"] > 0
        assert usage["output"] > 0


# ---------------------------------------------------------------------------
# Tests — tool use
# ---------------------------------------------------------------------------

class TestOllamaWithTools:
    def test_tool_use_returns_not_done(self):
        """Model calls a tool — should return done=False with tool results."""
        chunks = list(_tool_call_chunks("call-1", "echo_tool", {"msg": "hi"}))
        client = FakeClient([chunks])

        def echo_handler(params):
            return f"echoed: {params.get('msg', '')}"

        tools = [{"name": "echo_tool", "description": "echo", "input_schema": {"type": "object", "properties": {}}}]
        registry = {"echo_tool": {"handler": echo_handler}}
        messages = [{"role": "user", "content": "use tool"}]

        result_msgs, done = ollama_agent_turn(
            client, "ollama:qwen3.5:122b", messages, auto_approve=True,
            usage_totals=None, tools=tools, tool_registry=registry,
        )

        assert done is False
        # Messages: user, assistant (tool_use), user (tool_result)
        assert len(result_msgs) == 3
        assert result_msgs[1]["role"] == "assistant"
        tool_blocks = [b for b in result_msgs[1]["content"] if b["type"] == "tool_use"]
        assert len(tool_blocks) == 1
        assert tool_blocks[0]["name"] == "echo_tool"

        # Tool result should be in the last message
        last = result_msgs[2]
        assert last["role"] == "user"
        assert any("echoed: hi" in str(b.get("content", "")) for b in last["content"])

    def test_tool_then_text(self):
        """Multi-turn: model calls tool, then responds with text."""
        tool_chunks = list(_tool_call_chunks("t1", "my_tool", {"x": 1}))
        text_chunks = list(_text_chunks("Final answer"))

        client = FakeClient([tool_chunks, text_chunks])

        tools = [{"name": "my_tool", "description": "test", "input_schema": {"type": "object", "properties": {}}}]
        registry = {"my_tool": {"handler": lambda p: "tool output"}}
        messages = [{"role": "user", "content": "do it"}]

        # Turn 1: tool call
        messages, done = ollama_agent_turn(
            client, "ollama:qwen3.5:122b", messages, auto_approve=True,
            tools=tools, tool_registry=registry,
        )
        assert done is False

        # Turn 2: final text
        messages, done = ollama_agent_turn(
            client, "ollama:qwen3.5:122b", messages, auto_approve=True,
            tools=tools, tool_registry=registry,
        )
        assert done is True
        last_assistant = [m for m in messages if m["role"] == "assistant"][-1]
        texts = [b["text"] for b in last_assistant["content"] if b["type"] == "text"]
        assert "Final answer" in texts

    def test_no_tools_kwarg_omitted(self):
        """When no tools are available, the tools kwarg is omitted from the API call."""
        chunks = list(_text_chunks("no tools available"))
        # Track what kwargs are passed to create()
        captured = {}
        original_create = FakeCompletions.create
        def spy_create(self, **kwargs):
            captured.update(kwargs)
            return original_create(self, **kwargs)
        FakeCompletions.create = spy_create

        try:
            client = FakeClient([chunks])
            messages = [{"role": "user", "content": "hello"}]

            ollama_agent_turn(
                client, "ollama:mistral", messages, auto_approve=True,
                tools=[], tool_registry={},
            )

            assert "tools" not in captured
        finally:
            FakeCompletions.create = original_create
