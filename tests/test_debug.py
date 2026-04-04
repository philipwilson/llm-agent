"""Tests for llm_agent.debug — debug/trace logging."""

import json
import os

import pytest

from llm_agent.debug import DebugLogger, _NoOpDebug, _truncate_params, get_debug


class TestDebugLogger:
    def test_creates_log_file(self, tmp_path):
        path = str(tmp_path / "test.jsonl")
        logger = DebugLogger(path)
        logger.close()
        assert os.path.isfile(path)

    def test_session_start_and_end(self, tmp_path):
        path = str(tmp_path / "test.jsonl")
        logger = DebugLogger(path)
        logger.close()
        lines = open(path).read().strip().split("\n")
        events = [json.loads(line)["event"] for line in lines]
        assert events[0] == "session_start"
        assert events[-1] == "session_end"

    def test_log_api_request(self, tmp_path):
        path = str(tmp_path / "test.jsonl")
        logger = DebugLogger(path)
        logger.log_api_request(model="opus", provider="anthropic",
                               num_messages=5, num_tools=3)
        logger.close()
        lines = open(path).read().strip().split("\n")
        entry = json.loads(lines[1])  # [0] is session_start
        assert entry["event"] == "api_request"
        assert entry["model"] == "opus"
        assert entry["provider"] == "anthropic"
        assert entry["num_messages"] == 5

    def test_log_api_response(self, tmp_path):
        path = str(tmp_path / "test.jsonl")
        logger = DebugLogger(path)
        logger.log_api_response(model="opus", usage={"input": 100},
                                content_types=["text"], duration=1.234)
        logger.close()
        lines = open(path).read().strip().split("\n")
        entry = json.loads(lines[1])
        assert entry["event"] == "api_response"
        assert entry["duration_s"] == 1.234
        assert entry["usage"]["input"] == 100

    def test_log_api_error(self, tmp_path):
        path = str(tmp_path / "test.jsonl")
        logger = DebugLogger(path)
        logger.log_api_error("opus", ValueError("bad"), attempt=0, will_retry=True)
        logger.close()
        lines = open(path).read().strip().split("\n")
        entry = json.loads(lines[1])
        assert entry["event"] == "api_error"
        assert entry["error_type"] == "ValueError"
        assert entry["will_retry"] is True

    def test_log_tool_call_and_result(self, tmp_path):
        path = str(tmp_path / "test.jsonl")
        logger = DebugLogger(path)
        logger.log_tool_call("read_file", {"path": "/tmp/foo"})
        logger.log_tool_result("read_file", output_lines=10, duration=0.05)
        logger.close()
        lines = open(path).read().strip().split("\n")
        call_entry = json.loads(lines[1])
        result_entry = json.loads(lines[2])
        assert call_entry["event"] == "tool_call"
        assert call_entry["name"] == "read_file"
        assert result_entry["event"] == "tool_result"
        assert result_entry["output_lines"] == 10

    def test_log_trim(self, tmp_path):
        path = str(tmp_path / "test.jsonl")
        logger = DebugLogger(path)
        logger.log_trim(dropped_count=4, old_tokens=50000, new_tokens=30000)
        logger.close()
        lines = open(path).read().strip().split("\n")
        entry = json.loads(lines[1])
        assert entry["event"] == "trim"
        assert entry["dropped_messages"] == 4

    def test_elapsed_increases(self, tmp_path):
        path = str(tmp_path / "test.jsonl")
        logger = DebugLogger(path)
        logger.log_api_request(model="x", provider="y", num_messages=0, num_tools=0)
        logger.close()
        lines = open(path).read().strip().split("\n")
        e0 = json.loads(lines[0])["elapsed"]
        e1 = json.loads(lines[1])["elapsed"]
        assert e1 >= e0

    def test_system_prompt_truncated(self, tmp_path):
        path = str(tmp_path / "test.jsonl")
        logger = DebugLogger(path)
        long_prompt = "x" * 5000
        logger.log_system_prompt(long_prompt)
        logger.close()
        lines = open(path).read().strip().split("\n")
        entry = json.loads(lines[1])
        assert entry["event"] == "system_prompt"
        assert entry["length"] == 5000
        assert len(entry["text"]) == 2000


class TestNoOpDebug:
    def test_all_methods_are_silent(self):
        noop = _NoOpDebug()
        # Should not raise
        noop.log_system_prompt("x")
        noop.log_api_request(model="x", provider="y", num_messages=0, num_tools=0)
        noop.log_api_response(model="x")
        noop.log_api_error("x", Exception(), 0, True)
        noop.log_tool_call("x", {})
        noop.log_tool_result("x", 0)
        noop.log_trim(0, 0, 0)
        noop.close()


class TestTruncateParams:
    def test_short_values_unchanged(self):
        params = {"path": "/tmp/foo", "count": 5}
        assert _truncate_params(params) == params

    def test_long_string_truncated(self):
        params = {"content": "x" * 1000}
        result = _truncate_params(params)
        assert len(result["content"]) < 600
        assert "1000 chars" in result["content"]

    def test_non_dict_passthrough(self):
        assert _truncate_params("hello") == "hello"


class TestGetDebug:
    def test_default_is_noop(self):
        # get_debug() should return _NoOpDebug by default
        debug = get_debug()
        assert isinstance(debug, _NoOpDebug)
