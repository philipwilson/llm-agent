"""Tests for llm_agent.persistence — session save/load/list/find."""

import json
import os
from datetime import datetime, timezone

import pytest

from llm_agent.persistence import (
    new_session_id,
    session_path,
    save_session,
    load_session,
    list_sessions,
    find_session,
    _clean_messages,
    SESSIONS_DIR,
)


@pytest.fixture
def sessions_dir(tmp_path, monkeypatch):
    """Override SESSIONS_DIR to a temp directory."""
    d = str(tmp_path / "sessions")
    monkeypatch.setattr("llm_agent.persistence.SESSIONS_DIR", d)
    return d


class TestNewSessionId:
    def test_length(self):
        sid = new_session_id()
        assert len(sid) == 8

    def test_unique(self):
        ids = {new_session_id() for _ in range(100)}
        assert len(ids) == 100


class TestSessionPath:
    def test_format(self):
        started = datetime(2026, 4, 4, 15, 30, 0, tzinfo=timezone.utc)
        path = session_path("abc12345", started)
        assert path.endswith("20260404-153000-abc12345.json")
        assert SESSIONS_DIR in path


class TestSaveAndLoad:
    def test_roundtrip(self, sessions_dir):
        started = datetime(2026, 4, 4, 15, 30, 0, tzinfo=timezone.utc)
        path = os.path.join(sessions_dir, "test.json")
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        usage = {"input": 100, "output": 50, "cache_read": 0, "cache_create": 0}

        save_session(path, "abc12345", "claude-sonnet-4-6", messages,
                     usage, started, "hello")

        data = load_session(path)
        assert data["session_id"] == "abc12345"
        assert data["model"] == "claude-sonnet-4-6"
        assert data["first_question"] == "hello"
        assert data["message_count"] == 2
        assert len(data["messages"]) == 2
        assert data["messages"][0]["content"] == "hello"
        assert data["usage"]["input"] == 100

    def test_atomic_write_no_tmp_left(self, sessions_dir):
        started = datetime(2026, 4, 4, 15, 30, 0, tzinfo=timezone.utc)
        path = os.path.join(sessions_dir, "test.json")
        save_session(path, "x", "m", [], {}, started, "q")
        assert not os.path.exists(path + ".tmp")
        assert os.path.exists(path)


class TestCleanMessages:
    def test_strips_gemini_parts(self):
        messages = [
            {"role": "assistant", "content": [{"type": "text", "text": "hi"}],
             "_gemini_parts": ["some protobuf object"]},
        ]
        cleaned = _clean_messages(messages)
        assert "_gemini_parts" not in cleaned[0]
        assert cleaned[0]["content"][0]["text"] == "hi"

    def test_strips_base64_data(self):
        messages = [{
            "role": "user",
            "content": [{
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": "AAAA" * 1000},
            }],
        }]
        cleaned = _clean_messages(messages)
        assert cleaned[0]["content"][0]["source"]["data"] == "(binary data stripped)"
        assert cleaned[0]["content"][0]["source"]["media_type"] == "image/png"

    def test_preserves_normal_messages(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        cleaned = _clean_messages(messages)
        assert cleaned == messages

    def test_does_not_mutate_original(self):
        original = [{"role": "user", "content": "test", "_gemini_parts": []}]
        _clean_messages(original)
        assert "_gemini_parts" in original[0]


class TestListSessions:
    def test_empty_dir(self, sessions_dir):
        assert list_sessions() == []

    def test_nonexistent_dir(self, sessions_dir):
        # sessions_dir not created yet
        assert list_sessions() == []

    def test_lists_newest_first(self, sessions_dir):
        os.makedirs(sessions_dir)
        for i, name in enumerate(["20260401-100000-aaa.json",
                                   "20260402-100000-bbb.json",
                                   "20260403-100000-ccc.json"]):
            data = {"session_id": name[16:19], "model": "m", "messages": [],
                    "started_at": f"2026-04-0{i+1}T10:00:00"}
            with open(os.path.join(sessions_dir, name), "w") as f:
                json.dump(data, f)

        result = list_sessions()
        assert len(result) == 3
        assert result[0]["session_id"] == "ccc"
        assert result[2]["session_id"] == "aaa"

    def test_limit(self, sessions_dir):
        os.makedirs(sessions_dir)
        for i in range(5):
            name = f"2026040{i}-100000-s{i:02d}.json"
            data = {"session_id": f"s{i:02d}", "model": "m", "messages": []}
            with open(os.path.join(sessions_dir, name), "w") as f:
                json.dump(data, f)

        result = list_sessions(limit=2)
        assert len(result) == 2


class TestFindSession:
    def test_find_last(self, sessions_dir):
        os.makedirs(sessions_dir)
        for name in ["20260401-100000-aaa11111.json",
                     "20260402-100000-bbb22222.json"]:
            data = {"session_id": name[16:24], "messages": []}
            with open(os.path.join(sessions_dir, name), "w") as f:
                json.dump(data, f)

        path = find_session("last")
        assert path.endswith("bbb22222.json")

    def test_find_by_id_prefix(self, sessions_dir):
        os.makedirs(sessions_dir)
        name = "20260401-100000-abc12345.json"
        data = {"session_id": "abc12345", "messages": []}
        with open(os.path.join(sessions_dir, name), "w") as f:
            json.dump(data, f)

        assert find_session("abc") is not None
        assert find_session("abc12345") is not None

    def test_find_not_found(self, sessions_dir):
        os.makedirs(sessions_dir)
        assert find_session("zzz") is None

    def test_find_empty_dir(self, sessions_dir):
        assert find_session("last") is None
