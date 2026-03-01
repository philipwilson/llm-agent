"""Tests for utility functions in cli.py."""

import os

import pytest

from llm_agent.cli import (
    estimate_tokens,
    _is_tool_result_message,
    is_gemini_model,
    is_openai_model,
    parse_attachments,
    trim_conversation,
)


class TestEstimateTokens:
    def test_empty(self):
        assert estimate_tokens([]) == 0

    def test_string_content(self):
        msgs = [{"role": "user", "content": "hello world"}]  # 11 chars -> 2 tokens
        assert estimate_tokens(msgs) == 11 // 4

    def test_list_content_with_text(self):
        msgs = [{"role": "assistant", "content": [
            {"type": "text", "text": "hello world"},
        ]}]
        assert estimate_tokens(msgs) == 11 // 4

    def test_multiple_messages(self):
        msgs = [
            {"role": "user", "content": "abcd"},       # 4 chars -> 1
            {"role": "assistant", "content": "efghijkl"},  # 8 chars -> 2
        ]
        assert estimate_tokens(msgs) == 3

    def test_missing_content(self):
        msgs = [{"role": "user"}]
        assert estimate_tokens(msgs) == 0


class TestIsToolResultMessage:
    def test_normal_user_message(self):
        assert not _is_tool_result_message({"role": "user", "content": "hello"})

    def test_assistant_message(self):
        assert not _is_tool_result_message({"role": "assistant", "content": "hi"})

    def test_tool_result_message(self):
        msg = {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "123", "content": "result"},
        ]}
        assert _is_tool_result_message(msg)

    def test_mixed_content(self):
        msg = {"role": "user", "content": [
            {"type": "text", "text": "hello"},
            {"type": "tool_result", "tool_use_id": "123", "content": "result"},
        ]}
        assert _is_tool_result_message(msg)

    def test_user_with_list_no_tool_result(self):
        msg = {"role": "user", "content": [
            {"type": "text", "text": "hello"},
        ]}
        assert not _is_tool_result_message(msg)


class TestModelDetection:
    @pytest.mark.parametrize("model", [
        "gemini-2.5-flash", "gemini-3.1-pro-preview",
    ])
    def test_gemini_models(self, model):
        assert is_gemini_model(model)
        assert not is_openai_model(model)

    @pytest.mark.parametrize("model", [
        "gpt-4o", "gpt-4o-mini", "gpt-5.2", "o3", "o4-mini",
    ])
    def test_openai_models(self, model):
        assert is_openai_model(model)
        assert not is_gemini_model(model)

    @pytest.mark.parametrize("model", [
        "claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5",
    ])
    def test_anthropic_models(self, model):
        assert not is_gemini_model(model)
        assert not is_openai_model(model)


class TestParseAttachments:
    def test_no_attachments(self):
        text, blocks, err = parse_attachments("hello world")
        assert text == "hello world"
        assert blocks == []
        assert err is None

    def test_email_not_treated_as_attachment(self):
        text, blocks, err = parse_attachments("email user@example.com")
        assert text == "email user@example.com"
        assert blocks == []
        assert err is None

    def test_missing_file_with_known_extension(self, tmp_path):
        text, blocks, err = parse_attachments("@nonexistent.png")
        assert err == "File not found: nonexistent.png"

    def test_valid_image_attachment(self, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        from llm_agent.tools.base import shell
        old_cwd = shell.cwd
        shell.cwd = str(tmp_path)
        try:
            text, blocks, err = parse_attachments("describe @test.png please")
        finally:
            shell.cwd = old_cwd

        assert err is None
        assert len(blocks) == 1
        assert blocks[0]["type"] == "image"
        assert blocks[0]["source"]["media_type"] == "image/png"
        assert "describe" in text
        assert "please" in text
        assert "@test.png" not in text

    def test_unsupported_extension_existing_file(self, tmp_path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"\x00" * 10)

        from llm_agent.tools.base import shell
        old_cwd = shell.cwd
        shell.cwd = str(tmp_path)
        try:
            text, blocks, err = parse_attachments(f"@data.bin")
        finally:
            shell.cwd = old_cwd

        assert err is not None
        assert "Unsupported file type" in err


class TestTrimConversation:
    def _make_conversation(self, n_rounds):
        """Build a synthetic conversation with n user/assistant rounds."""
        msgs = []
        for i in range(n_rounds):
            msgs.append({"role": "user", "content": f"question {i}" + " x" * 200})
            msgs.append({"role": "assistant", "content": f"answer {i}" + " y" * 200})
        return msgs

    def test_no_trim_under_budget(self):
        msgs = self._make_conversation(3)
        result = trim_conversation(msgs, 1000, "claude-sonnet-4-6")
        assert len(result) == len(msgs)

    def test_trims_when_over_budget(self):
        msgs = self._make_conversation(10)
        # Pretend we used 180k of 200k budget (over 80%)
        result = trim_conversation(msgs, 180_000, "claude-sonnet-4-6")
        assert len(result) < len(msgs)

    def test_preserves_recent_messages(self):
        msgs = self._make_conversation(10)
        result = trim_conversation(msgs, 180_000, "claude-sonnet-4-6")
        # Should trim something but keep at least the last round
        assert len(result) < len(msgs)
        if result:
            assert result[-1] == msgs[-1]

    def test_does_not_split_tool_use_pair(self):
        """Tool result messages should not be orphaned from their tool_use."""
        msgs = [
            {"role": "user", "content": "question" + " x" * 200},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "f"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "result" + " z" * 200},
            ]},
            {"role": "assistant", "content": "final answer" + " y" * 200},
            {"role": "user", "content": "next question"},
            {"role": "assistant", "content": "next answer"},
        ]
        result = trim_conversation(msgs, 180_000, "claude-sonnet-4-6")
        # If trimmed, should trim the whole round (user + tool_use + tool_result + answer)
        for msg in result:
            if _is_tool_result_message(msg):
                # The tool_use must also be present
                idx = result.index(msg)
                assert idx > 0
                prev = result[idx - 1]
                assert prev.get("role") == "assistant"
