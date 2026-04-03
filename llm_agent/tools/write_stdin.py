"""write_stdin tool: send input to or poll output from a PTY session."""

from datetime import datetime

from llm_agent.formatting import bold, dim
from llm_agent.tools.base import shell

SCHEMA = {
    "name": "write_stdin",
    "description": (
        "Send input to a PTY-backed interactive session started by start_session, "
        "or poll for new output by passing an empty chars string."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "The session ID returned by start_session (for example 'pty-1').",
            },
            "chars": {
                "type": "string",
                "description": (
                    "Characters to write to the session stdin. Use \\n to submit a line. "
                    "Leave empty to only poll for new output. You can send control characters "
                    "such as \\u0003 for Ctrl-C."
                ),
                "default": "",
            },
            "wait_ms": {
                "type": "integer",
                "description": "How long to wait for new output after writing or polling.",
                "default": 200,
            },
            "max_output_chars": {
                "type": "integer",
                "description": "Maximum number of newly produced output characters to return.",
                "default": 12000,
            },
            "close": {
                "type": "boolean",
                "description": "If true, terminate the interactive session after collecting final output.",
                "default": False,
            },
        },
        "required": ["session_id"],
    },
}

NEEDS_CONFIRM = True
NEEDS_SEQUENTIAL = True


def _format_timestamp(ts):
    if ts is None:
        return "n/a"
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _format_duration(seconds):
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, seconds = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m {seconds:02d}s"


def _preview_chars(chars):
    rendered = chars.encode("unicode_escape").decode("ascii")
    if len(rendered) > 120:
        rendered = rendered[:117] + "..."
    return rendered


def confirm(session_id, chars="", close=False):
    from llm_agent.display import get_display

    display = get_display()
    preview = [f"  {dim('#')} {dim(f'session {session_id}')}"]
    if close:
        prompt = "Close interactive session? [Y/n]"
    else:
        preview.append(f"  {bold('stdin')} {bold(_preview_chars(chars))}")
        prompt = "Send input to interactive session? [Y/n]"
    return display.confirm(preview, prompt)


def _format_result(info, output, heading):
    lines = [
        f"Session: {info['session_id']}",
        f"Command: {info['command']}",
        f"PID: {info['pid']}",
        f"Working directory: {info['cwd']}",
        f"Status: {info['status']}",
        f"Started: {_format_timestamp(info['started_at'])}",
        f"Finished: {_format_timestamp(info['finished_at'])}",
        f"Runtime: {_format_duration(info['duration_seconds'])}",
    ]
    if info["exit_code"] is not None:
        lines.append(f"Exit code: {info['exit_code']}")
    if output:
        lines.append(f"\n{heading}:\n{output}")
    else:
        lines.append("\n(no new output)")
    return "\n".join(lines)


def handle(params, auto_approve=False):
    del auto_approve  # Non-empty writes and closes always require explicit approval.

    session_id = params.get("session_id", "").strip()
    chars = params.get("chars", "")
    wait_ms = params.get("wait_ms", 200)
    max_output_chars = params.get("max_output_chars", 12000)
    close = params.get("close", False)

    if not session_id:
        return "(error: session_id is required)"
    if close and chars:
        return "(error: close cannot be combined with chars)"
    if wait_ms is not None and wait_ms < 0:
        return "(error: wait_ms must be >= 0)"
    if max_output_chars is not None and max_output_chars < 1:
        return "(error: max_output_chars must be >= 1)"

    needs_confirmation = bool(chars) or close
    if needs_confirmation and not confirm(session_id, chars=chars, close=close):
        if close:
            return "(user declined to close interactive session)"
        return "(user declined to write to interactive session)"

    if close:
        info = shell.terminate_session(
            session_id,
            wait_ms=wait_ms,
            max_output_chars=max_output_chars,
        )
        if info is None:
            return f"(unknown session: {session_id})"
        return _format_result(info, info["output"], "Final output")

    info = shell.write_session(
        session_id,
        chars=chars,
        wait_ms=wait_ms,
        max_output_chars=max_output_chars,
    )
    if info is None:
        return f"(unknown session: {session_id})"
    return _format_result(info, info["output"], "New output")
