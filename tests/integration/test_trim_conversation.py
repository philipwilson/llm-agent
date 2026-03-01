"""Integration tests for conversation trimming with mock summarization."""

import pytest

from llm_agent.cli import trim_conversation, _summarize_dropped


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conversation(n_rounds, content_size=500):
    """Build a conversation with n rounds of user/assistant messages."""
    msgs = []
    for i in range(n_rounds):
        msgs.append({"role": "user", "content": f"question {i} " + "x" * content_size})
        msgs.append({"role": "assistant", "content": f"answer {i} " + "y" * content_size})
    return msgs


def _make_tool_round(question, tool_name, tool_result, answer, content_size=200):
    """Build a user question -> tool_use -> tool_result -> answer round."""
    return [
        {"role": "user", "content": question + " " + "q" * content_size},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": tool_name, "input": {"path": "f"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": tool_result + " " + "r" * content_size},
        ]},
        {"role": "assistant", "content": f"{answer} " + "a" * content_size},
    ]


class FakeClient:
    """Client that returns a canned summary when asked to summarize."""

    def __init__(self, summary_text="- Key finding 1\n- Key finding 2"):
        self._summary = summary_text

    @property
    def messages(self):
        return self

    def stream(self, **kwargs):
        """Mimics anthropic client.messages.stream for _summarize_dropped."""
        from types import SimpleNamespace

        class FakeStream:
            def __init__(self, text):
                self._text = text
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def __iter__(self):
                yield SimpleNamespace(
                    type="content_block_start",
                    content_block=SimpleNamespace(type="text"),
                )
                yield SimpleNamespace(
                    type="content_block_delta",
                    delta=SimpleNamespace(type="text_delta", text=self._text),
                )
                yield SimpleNamespace(type="content_block_stop")
            def get_final_message(self):
                return SimpleNamespace(
                    usage=SimpleNamespace(
                        input_tokens=100,
                        output_tokens=50,
                        cache_read_input_tokens=0,
                        cache_creation_input_tokens=0,
                    )
                )

        return FakeStream(self._summary)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTrimWithSummary:
    def test_summary_prepended_after_trimming(self):
        """When trimming drops messages, their summary is prepended."""
        msgs = _make_conversation(20, content_size=1000)
        client = FakeClient("- Discussed file structure\n- Decided on approach X")

        result = trim_conversation(msgs, 180_000, "claude-sonnet-4-6", client=client)

        assert len(result) < len(msgs)
        # First message should be the summary
        assert result[0]["role"] == "user"
        assert "[Earlier context summary]" in result[0]["content"]
        assert "Discussed file structure" in result[0]["content"]

    def test_no_summary_without_client(self):
        """Without a client, trimming drops messages without summary."""
        msgs = _make_conversation(20, content_size=1000)

        result = trim_conversation(msgs, 180_000, "claude-sonnet-4-6", client=None)

        assert len(result) < len(msgs)
        # No summary message — first message is just a regular user message
        if result:
            assert "[Earlier context summary]" not in str(result[0].get("content", ""))

    def test_no_trim_when_under_budget(self):
        """Messages under budget are returned unchanged."""
        msgs = _make_conversation(3)
        client = FakeClient("should not be called")

        result = trim_conversation(msgs, 1000, "claude-sonnet-4-6", client=client)

        assert result == msgs

    def test_tool_rounds_trimmed_atomically(self):
        """Tool use/result pairs should not be split during trimming."""
        # Build a conversation with tool rounds
        msgs = []
        for i in range(5):
            msgs.extend(_make_tool_round(
                f"question {i}", "read_file",
                f"file content {i}", f"answer {i}",
                content_size=500,
            ))
        # Add a final plain round
        msgs.append({"role": "user", "content": "final question " + "x" * 500})
        msgs.append({"role": "assistant", "content": "final answer " + "y" * 500})

        result = trim_conversation(msgs, 180_000, "claude-sonnet-4-6")

        # Verify no orphaned tool_results
        for i, msg in enumerate(result):
            content = msg.get("content")
            if isinstance(content, list):
                has_tool_result = any(
                    b.get("type") == "tool_result" for b in content if isinstance(b, dict)
                )
                if has_tool_result:
                    # Previous message must be an assistant with tool_use
                    assert i > 0
                    prev = result[i - 1]
                    assert prev["role"] == "assistant"
                    prev_content = prev.get("content")
                    if isinstance(prev_content, list):
                        assert any(
                            b.get("type") == "tool_use"
                            for b in prev_content if isinstance(b, dict)
                        )


class TestSummarizeDropped:
    def test_returns_summary(self):
        """_summarize_dropped should return the model's response."""
        client = FakeClient("- Summary point 1")
        dropped = [
            {"role": "user", "content": "old question " + "x" * 300},
            {"role": "assistant", "content": "old answer " + "y" * 300},
        ]
        result = _summarize_dropped(client, "claude-sonnet-4-6", dropped)
        assert result is not None
        assert "Summary point 1" in result

    def test_skips_tiny_content(self):
        """Very short dropped content shouldn't be summarized."""
        client = FakeClient("- Summary")
        dropped = [{"role": "user", "content": "hi"}]
        # estimate_tokens("hi") < 200, so _summarize_dropped is not called
        # from trim_conversation, but we're calling it directly here
        result = _summarize_dropped(client, "claude-sonnet-4-6", dropped)
        # It still works, just returns the summary
        assert isinstance(result, str)
