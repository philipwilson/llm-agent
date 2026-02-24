"""Display protocol: abstracts all user-facing output.

The default Display class prints to stdout — identical to the original behavior.
TUIDisplay (in tui.py) overrides these methods to route output to Textual widgets.

Usage:
    from llm_agent.display import get_display
    get_display().stream_token(text)
    get_display().tool_log(message)
    get_display().confirm(preview_lines, prompt) -> bool
"""

import sys
import threading
from contextlib import contextmanager

from llm_agent.formatting import dim


class Display:
    """Default display: prints to stdout, reads from stdin.

    Thread safety: a lock serializes non-streaming output so log lines from
    concurrent subagents don't interleave.  Streaming can be suppressed
    per-thread via ``suppress_streaming()`` (used by subagents, whose final
    answer is returned to the parent rather than streamed).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._local = threading.local()

    @contextmanager
    def suppress_streaming(self):
        """Context manager that suppresses stream output on the current thread."""
        self._local.streaming_suppressed = True
        try:
            yield
        finally:
            self._local.streaming_suppressed = False

    def _is_streaming_suppressed(self):
        return getattr(self._local, "streaming_suppressed", False)

    def stream_start(self):
        """Called before the first token of a model response."""
        if self._is_streaming_suppressed():
            return
        print()

    def stream_token(self, text):
        """Called for each streamed text token."""
        if self._is_streaming_suppressed():
            return
        print(text, end="", flush=True)

    def stream_end(self):
        """Called after the last token of a model response."""
        if self._is_streaming_suppressed():
            return
        print()

    def tool_log(self, message):
        """Log a tool invocation (already ANSI-formatted)."""
        with self._lock:
            print(message)

    def tool_result(self, line_count):
        """Log the result size of a tool call."""
        with self._lock:
            print(dim(f"  → {line_count} lines of output"))

    def confirm(self, preview_lines, prompt_text):
        """Show a preview and ask for Y/n confirmation.

        Args:
            preview_lines: list of pre-formatted strings to display.
            prompt_text: the confirmation prompt (e.g. "Apply? [Y/n]").

        Returns:
            True if the user confirmed, False otherwise.
        """
        with self._lock:
            for line in preview_lines:
                print(line)
            answer = input(f"  {dim(prompt_text)} ").strip().lower()
        return answer in ("", "y", "yes")

    def auto_approved(self, preview_lines):
        """Show a preview that was auto-approved (no confirmation needed)."""
        with self._lock:
            for line in preview_lines:
                print(line)
            print(f"  {dim('(auto-approved)')}")

    def status(self, message):
        """Print a dim status message."""
        with self._lock:
            print(dim(message))

    def error(self, message):
        """Print an error message (already formatted)."""
        with self._lock:
            print(message)

    def info(self, message):
        """Print an informational message (already formatted)."""
        with self._lock:
            print(message)

    def info_stderr(self, message):
        """Print an informational message to stderr."""
        with self._lock:
            print(message, file=sys.stderr)


_current = Display()


def get_display():
    """Return the current Display instance."""
    return _current


def set_display(display):
    """Replace the current Display instance."""
    global _current
    _current = display
