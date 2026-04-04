"""Debug/trace logging for agent turns, API calls, and tool dispatch.

When enabled (via --debug), writes structured JSON-lines to a per-session
log file under ~/.local/share/llm-agent/debug/. Each entry has a timestamp,
event type, and event-specific data.

Usage from other modules:

    from llm_agent.debug import get_debug
    get_debug().log_api_request(model=model, kwargs=api_kwargs)
    get_debug().log_tool_call(name="read_file", params={...})
"""

import json
import os
import time
from datetime import datetime, timezone


class DebugLogger:
    """Writes structured debug events to a JSON-lines file."""

    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._f = open(path, "a")
        self._start = time.monotonic()
        self._write("session_start", {
            "pid": os.getpid(),
            "cwd": os.getcwd(),
        })

    def _write(self, event, data):
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "elapsed": round(time.monotonic() - self._start, 3),
            "event": event,
            **data,
        }
        self._f.write(json.dumps(entry, default=str) + "\n")
        self._f.flush()

    def log_system_prompt(self, prompt):
        self._write("system_prompt", {"length": len(prompt), "text": prompt[:2000]})

    def log_api_request(self, model, provider, num_messages, num_tools, extra=None):
        data = {
            "model": model,
            "provider": provider,
            "num_messages": num_messages,
            "num_tools": num_tools,
        }
        if extra:
            data["extra"] = extra
        self._write("api_request", data)

    def log_api_response(self, model, usage=None, content_types=None, duration=None):
        data = {"model": model}
        if usage:
            data["usage"] = usage
        if content_types:
            data["content_types"] = content_types
        if duration is not None:
            data["duration_s"] = round(duration, 3)
        self._write("api_response", data)

    def log_api_error(self, model, error, attempt, will_retry):
        self._write("api_error", {
            "model": model,
            "error": str(error),
            "error_type": type(error).__name__,
            "attempt": attempt,
            "will_retry": will_retry,
        })

    def log_tool_call(self, name, params):
        # Truncate large param values to keep logs manageable
        truncated = _truncate_params(params)
        self._write("tool_call", {"name": name, "params": truncated})

    def log_tool_result(self, name, output_lines, duration=None, error=None):
        data = {"name": name, "output_lines": output_lines}
        if duration is not None:
            data["duration_s"] = round(duration, 3)
        if error:
            data["error"] = str(error)
        self._write("tool_result", data)

    def log_trim(self, dropped_count, old_tokens, new_tokens):
        self._write("trim", {
            "dropped_messages": dropped_count,
            "old_tokens": old_tokens,
            "new_tokens": new_tokens,
        })

    def close(self):
        self._write("session_end", {})
        self._f.close()


class _NoOpDebug:
    """Silent stand-in when debug mode is disabled."""

    def log_system_prompt(self, *a, **kw): pass
    def log_api_request(self, *a, **kw): pass
    def log_api_response(self, *a, **kw): pass
    def log_api_error(self, *a, **kw): pass
    def log_tool_call(self, *a, **kw): pass
    def log_tool_result(self, *a, **kw): pass
    def log_trim(self, *a, **kw): pass
    def close(self): pass


def _truncate_params(params, max_value_len=500):
    """Truncate string values in a params dict for logging."""
    if not isinstance(params, dict):
        return params
    out = {}
    for k, v in params.items():
        if isinstance(v, str) and len(v) > max_value_len:
            out[k] = v[:max_value_len] + f"...({len(v)} chars)"
        else:
            out[k] = v
    return out


# Module-level singleton
_debug = _NoOpDebug()


def enable_debug():
    """Enable debug logging for this session. Returns the log file path."""
    global _debug
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_dir = os.path.expanduser("~/.local/share/llm-agent/debug")
    path = os.path.join(log_dir, f"{timestamp}-{os.getpid()}.jsonl")
    _debug = DebugLogger(path)
    return path


def get_debug():
    """Return the current debug logger (no-op if debug is disabled)."""
    return _debug
