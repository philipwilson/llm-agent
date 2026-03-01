"""Tests for llm_agent.formatting — pure functions, no mocking needed."""

from llm_agent.formatting import truncate, format_tokens


class TestTruncate:
    def test_short_text_unchanged(self):
        text = "line 1\nline 2\nline 3"
        assert truncate(text) == text

    def test_empty_string(self):
        assert truncate("") == ""

    def test_single_line(self):
        assert truncate("hello") == "hello"

    def test_exactly_at_limit(self):
        lines = [f"line {i}" for i in range(200)]
        text = "\n".join(lines)
        assert truncate(text) == text

    def test_one_over_limit(self):
        lines = [f"line {i}" for i in range(201)]
        text = "\n".join(lines)
        result = truncate(text)
        assert "1 lines omitted" in result
        assert "line 0" in result
        assert "line 200" in result

    def test_way_over_limit(self):
        lines = [f"line {i}" for i in range(500)]
        text = "\n".join(lines)
        result = truncate(text)
        assert "300 lines omitted" in result
        # First 100 lines preserved
        assert "line 0" in result
        assert "line 99" in result
        # Last 100 lines preserved
        assert "line 400" in result
        assert "line 499" in result
        # Middle lines omitted
        assert "line 200" not in result

    def test_custom_max_lines(self):
        lines = [f"line {i}" for i in range(20)]
        text = "\n".join(lines)
        result = truncate(text, max_lines=10)
        assert "10 lines omitted" in result

    def test_preserves_content(self):
        """First half and last half of lines are preserved exactly."""
        lines = [f"line {i}" for i in range(210)]
        text = "\n".join(lines)
        result = truncate(text)
        result_lines = result.splitlines()
        # First 100 lines
        assert result_lines[0] == "line 0"
        assert result_lines[99] == "line 99"
        # Last 100 lines
        assert result_lines[-1] == "line 209"


class TestFormatTokens:
    def test_small_numbers(self):
        assert format_tokens(0) == "0"
        assert format_tokens(1) == "1"
        assert format_tokens(999) == "999"

    def test_thousands(self):
        assert format_tokens(1000) == "1.0k"
        assert format_tokens(1500) == "1.5k"
        assert format_tokens(12345) == "12.3k"
        assert format_tokens(999_999) == "1000.0k"

    def test_millions(self):
        assert format_tokens(1_000_000) == "1.0M"
        assert format_tokens(2_500_000) == "2.5M"
        assert format_tokens(10_000_000) == "10.0M"
