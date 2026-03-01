"""Integration tests for agent_turn: mock the Anthropic streaming API,
verify tool dispatch and final answer extraction."""

import json
from types import SimpleNamespace

import pytest

from llm_agent.agent import agent_turn


# ---------------------------------------------------------------------------
# Helpers to build a fake Anthropic streaming client
# ---------------------------------------------------------------------------

def _make_event(event_type, **kwargs):
    """Create a SimpleNamespace mimicking an Anthropic stream event."""
    e = SimpleNamespace(type=event_type)
    for k, v in kwargs.items():
        setattr(e, k, v)
    return e


def _text_events(text):
    """Yield stream events for a simple text response."""
    yield _make_event(
        "content_block_start",
        content_block=SimpleNamespace(type="text"),
    )
    yield _make_event(
        "content_block_delta",
        delta=SimpleNamespace(type="text_delta", text=text),
    )
    yield _make_event("content_block_stop")


def _tool_use_events(tool_id, tool_name, tool_input):
    """Yield stream events for a tool_use block."""
    yield _make_event(
        "content_block_start",
        content_block=SimpleNamespace(type="tool_use", id=tool_id, name=tool_name),
    )
    yield _make_event(
        "content_block_delta",
        delta=SimpleNamespace(type="input_json_delta", partial_json=json.dumps(tool_input)),
    )
    yield _make_event("content_block_stop")


class FakeUsage:
    def __init__(self, input_tokens=100, output_tokens=50):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0


class FakeStream:
    """Mimics the Anthropic client.messages.stream() context manager."""

    def __init__(self, events, usage=None):
        self._events = list(events)
        self._usage = usage or FakeUsage()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def __iter__(self):
        return iter(self._events)

    def get_final_message(self):
        return SimpleNamespace(usage=self._usage)


class FakeMessages:
    def __init__(self, streams):
        self._streams = list(streams)
        self._call_count = 0

    def stream(self, **kwargs):
        idx = min(self._call_count, len(self._streams) - 1)
        self._call_count += 1
        return self._streams[idx]


class FakeClient:
    def __init__(self, streams):
        self.messages = FakeMessages(streams)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAgentTurnTextOnly:
    def test_simple_text_response(self):
        """Model returns plain text — should be done immediately."""
        events = list(_text_events("Hello, world!"))
        client = FakeClient([FakeStream(events)])
        messages = [{"role": "user", "content": "Hi"}]
        usage = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}

        result_msgs, done = agent_turn(
            client, "claude-sonnet-4-6", messages, auto_approve=True,
            usage_totals=usage, tools=None, tool_registry={},
        )

        assert done is True
        assert len(result_msgs) == 2  # user + assistant
        assert result_msgs[-1]["role"] == "assistant"
        text_blocks = [b for b in result_msgs[-1]["content"] if b["type"] == "text"]
        assert text_blocks[0]["text"] == "Hello, world!"

    def test_usage_tracked(self):
        events = list(_text_events("answer"))
        client = FakeClient([FakeStream(events, FakeUsage(200, 80))])
        messages = [{"role": "user", "content": "question"}]
        usage = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}

        agent_turn(
            client, "claude-sonnet-4-6", messages, auto_approve=True,
            usage_totals=usage, tools=None, tool_registry={},
        )

        assert usage["input"] == 200
        assert usage["output"] == 80


class TestAgentTurnWithTools:
    def test_tool_use_returns_not_done(self):
        """Model calls a tool — should return done=False with tool results."""
        events = list(_tool_use_events("tool-1", "echo_tool", {"msg": "hi"}))
        client = FakeClient([FakeStream(events)])

        def echo_handler(params):
            return f"echoed: {params.get('msg', '')}"

        tools = [{"name": "echo_tool", "description": "echo", "input_schema": {"type": "object", "properties": {}}}]
        registry = {"echo_tool": {"handler": echo_handler}}
        messages = [{"role": "user", "content": "use tool"}]

        result_msgs, done = agent_turn(
            client, "claude-sonnet-4-6", messages, auto_approve=True,
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
        # Turn 1: tool use
        tool_events = list(_tool_use_events("t1", "my_tool", {"x": 1}))
        # Turn 2: text response
        text_events = list(_text_events("Final answer"))

        client = FakeClient([
            FakeStream(tool_events),
            FakeStream(text_events),
        ])

        tools = [{"name": "my_tool", "description": "test", "input_schema": {"type": "object", "properties": {}}}]
        registry = {"my_tool": {"handler": lambda p: "tool output"}}
        messages = [{"role": "user", "content": "do it"}]

        # Turn 1: tool call
        messages, done = agent_turn(
            client, "claude-sonnet-4-6", messages, auto_approve=True,
            tools=tools, tool_registry=registry,
        )
        assert done is False

        # Turn 2: final text
        messages, done = agent_turn(
            client, "claude-sonnet-4-6", messages, auto_approve=True,
            tools=tools, tool_registry=registry,
        )
        assert done is True
        last_assistant = [m for m in messages if m["role"] == "assistant"][-1]
        texts = [b["text"] for b in last_assistant["content"] if b["type"] == "text"]
        assert "Final answer" in texts

    def test_mixed_text_and_tool(self):
        """Model returns text and tool_use in the same response."""
        events = list(_text_events("Let me check...")) + list(
            _tool_use_events("t1", "lookup", {"q": "test"})
        )
        client = FakeClient([FakeStream(events)])
        tools = [{"name": "lookup", "description": "look up", "input_schema": {"type": "object", "properties": {}}}]
        registry = {"lookup": {"handler": lambda p: "found it"}}
        messages = [{"role": "user", "content": "search"}]

        result_msgs, done = agent_turn(
            client, "claude-sonnet-4-6", messages, auto_approve=True,
            tools=tools, tool_registry=registry,
        )

        # Not done because there was a tool use
        assert done is False
        assistant = result_msgs[1]
        types = {b["type"] for b in assistant["content"]}
        assert "text" in types
        assert "tool_use" in types
