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

from llm_agent.formatting import dim


class Display:
    """Default display: prints to stdout, reads from stdin."""

    def stream_start(self):
        """Called before the first token of a model response."""
        print()

    def stream_token(self, text):
        """Called for each streamed text token."""
        print(text, end="", flush=True)

    def stream_end(self):
        """Called after the last token of a model response."""
        print()

    def tool_log(self, message):
        """Log a tool invocation (already ANSI-formatted)."""
        print(message)

    def tool_result(self, line_count):
        """Log the result size of a tool call."""
        print(dim(f"  → {line_count} lines of output"))

    def confirm(self, preview_lines, prompt_text):
        """Show a preview and ask for Y/n confirmation.

        Args:
            preview_lines: list of pre-formatted strings to display.
            prompt_text: the confirmation prompt (e.g. "Apply? [Y/n]").

        Returns:
            True if the user confirmed, False otherwise.
        """
        for line in preview_lines:
            print(line)
        answer = input(f"  {dim(prompt_text)} ").strip().lower()
        return answer in ("", "y", "yes")

    def auto_approved(self, preview_lines):
        """Show a preview that was auto-approved (no confirmation needed)."""
        for line in preview_lines:
            print(line)
        print(f"  {dim('(auto-approved)')}")

    def status(self, message):
        """Print a dim status message."""
        print(dim(message))

    def error(self, message):
        """Print an error message (already formatted)."""
        print(message)

    def info(self, message):
        """Print an informational message (already formatted)."""
        print(message)

    def info_stderr(self, message):
        """Print an informational message to stderr."""
        print(message, file=sys.stderr)


_current = Display()


def get_display():
    """Return the current Display instance."""
    return _current


def set_display(display):
    """Replace the current Display instance."""
    global _current
    _current = display
