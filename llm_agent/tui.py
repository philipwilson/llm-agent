"""Textual TUI for interactive mode."""

import threading

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.theme import Theme
from textual.widgets import OptionList, RichLog, Rule, Static, TextArea
from rich.text import Text

from llm_agent.display import Display, set_display
from llm_agent.formatting import bold, dim, format_tokens


# ---------------------------------------------------------------------------
# PromptInput — TextArea with readline keybindings, wrapping, and Enter submit
# ---------------------------------------------------------------------------

class PromptInput(TextArea):
    """Multi-line input widget with Emacs-style keybindings and Enter-to-submit."""

    class Submitted(Message):
        """Posted when the user presses Enter to submit input."""
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    def __init__(self, *, placeholder="", id=None):
        super().__init__(
            "",
            soft_wrap=True,
            show_line_numbers=False,
            theme="css",
            tab_behavior="focus",
            id=id,
            placeholder=placeholder,
            compact=True,
        )
        self._placeholder_text = placeholder

    # -- Compatibility properties for code that expects Input-like API --

    @property
    def value(self):
        return self.text

    @value.setter
    def value(self, val):
        self.text = val

    @property
    def cursor_position(self):
        row, col = self.cursor_location
        # Convert to flat character offset
        lines = self.text.split("\n")
        pos = sum(len(lines[i]) + 1 for i in range(row)) + col
        return pos

    @cursor_position.setter
    def cursor_position(self, pos):
        # Convert flat offset to (row, col)
        text = self.text
        if pos >= len(text):
            lines = text.split("\n")
            self.cursor_location = (len(lines) - 1, len(lines[-1]))
        else:
            consumed = 0
            for i, line in enumerate(text.split("\n")):
                if consumed + len(line) >= pos:
                    self.cursor_location = (i, pos - consumed)
                    return
                consumed += len(line) + 1  # +1 for newline
            # Fallback: end of text
            lines = text.split("\n")
            self.cursor_location = (len(lines) - 1, len(lines[-1]))

    @property
    def placeholder(self):
        return self._placeholder_text

    @placeholder.setter
    def placeholder(self, val):
        self._placeholder_text = val
        # TextArea.placeholder is a Textual reactive descriptor, not a
        # Python property — use the descriptor protocol directly.
        TextArea.placeholder.__set__(self, val)

    # -- Key handling --

    def _on_key(self, event):
        """Intercept Enter to submit, Shift+Enter for newline, plus readline bindings."""
        key = event.key

        if key == "enter":
            # Submit the input
            text = self.text
            self.text = ""
            self.post_message(self.Submitted(text))
            event.prevent_default()
            event.stop()
            return

        if key == "shift+enter":
            # Insert a newline (let TextArea handle it as normal Enter)
            self.insert("\n")
            event.prevent_default()
            event.stop()
            return

        # Emacs/readline keybindings
        if key == "ctrl+a":
            # Move to start of current line
            row, _ = self.cursor_location
            self.cursor_location = (row, 0)
            event.prevent_default()
            event.stop()
        elif key == "ctrl+e":
            # Move to end of current line
            row, _ = self.cursor_location
            lines = self.text.split("\n")
            self.cursor_location = (row, len(lines[row]))
            event.prevent_default()
            event.stop()
        elif key == "ctrl+f":
            self.action_cursor_right()
            event.prevent_default()
            event.stop()
        elif key == "ctrl+b":
            self.action_cursor_left()
            event.prevent_default()
            event.stop()
        elif key == "ctrl+k":
            # Kill to end of line
            row, col = self.cursor_location
            lines = self.text.split("\n")
            line = lines[row]
            if col < len(line):
                # Delete from cursor to end of line
                lines[row] = line[:col]
                self.text = "\n".join(lines)
                self.cursor_location = (row, col)
            elif row < len(lines) - 1:
                # At end of line, join with next line
                lines[row] = line + lines[row + 1]
                del lines[row + 1]
                self.text = "\n".join(lines)
                self.cursor_location = (row, col)
            event.prevent_default()
            event.stop()
        elif key == "ctrl+u":
            # Kill to start of line
            row, col = self.cursor_location
            lines = self.text.split("\n")
            lines[row] = lines[row][col:]
            self.text = "\n".join(lines)
            self.cursor_location = (row, 0)
            event.prevent_default()
            event.stop()
        elif key == "ctrl+w":
            # Delete word backward
            row, col = self.cursor_location
            lines = self.text.split("\n")
            line = lines[row]
            before = line[:col]
            # Skip trailing spaces, then delete word chars
            i = len(before) - 1
            while i >= 0 and before[i] == " ":
                i -= 1
            while i >= 0 and before[i] != " ":
                i -= 1
            lines[row] = before[:i + 1] + line[col:]
            new_col = i + 1
            self.text = "\n".join(lines)
            self.cursor_location = (row, new_col)
            event.prevent_default()
            event.stop()
        elif key == "ctrl+h":
            self.action_delete_left()
            event.prevent_default()
            event.stop()


# ---------------------------------------------------------------------------
# ChoiceList — OptionList subclass with Escape-to-cancel
# ---------------------------------------------------------------------------

class ChoiceList(OptionList):
    """Arrow-key selection widget for ask_user choices. Escape cancels."""

    class Cancelled(Message):
        pass

    def _on_key(self, event):
        if event.key == "escape":
            self.post_message(self.Cancelled())
            event.prevent_default()
            event.stop()


# ---------------------------------------------------------------------------
# Light theme inspired by Claude Code
# ---------------------------------------------------------------------------

LIGHT_THEME = Theme(
    name="agent-light",
    primary="#2e8b57",       # sea-green accent (prompt, highlights)
    secondary="#6a737d",     # muted gray for secondary text
    accent="#2e8b57",
    background="#ffffff",    # white conversation area
    surface="#f0f0f0",       # light gray for status bar
    panel="#f7f7f7",         # very light gray for input area
    warning="#d29922",
    error="#cb2431",
    success="#2e8b57",
    dark=False,
)


# ---------------------------------------------------------------------------
# TUIDisplay — routes all output to Textual widgets from the worker thread
# ---------------------------------------------------------------------------

class TUIDisplay(Display):
    """Display implementation that routes output to Textual widgets."""

    def __init__(self, app):
        super().__init__()
        self._app = app
        self._confirm_event = threading.Event()
        self._confirm_result = False
        self._ask_event = threading.Event()
        self._ask_result = ""
        self._selection_event = threading.Event()
        self._selection_result = ""
        # Accumulate the full streamed response, write once at stream_end.
        # Each RichLog.write() creates an independent block that wraps at
        # its own width, so writing per-batch produces narrow paragraphs.
        self._stream_buffer = ""
        self._stream_lock = threading.Lock()

    # -- helpers --

    def _write(self, text):
        """Write ANSI text to the RichLog widget (thread-safe)."""
        self._app.call_from_thread(self._app_write, text)

    def _app_write(self, text):
        """Must run on the main thread."""
        log = self._app.query_one("#conversation", RichLog)
        log.write(Text.from_ansi(text))

    # -- Display protocol --

    def stream_start(self):
        if self._is_streaming_suppressed():
            return
        with self._stream_lock:
            self._stream_buffer = ""
        self._app.call_from_thread(self._app_set_streaming, True)

    def stream_token(self, text):
        if self._is_streaming_suppressed():
            return
        with self._stream_lock:
            self._stream_buffer += text

    def stream_end(self):
        if self._is_streaming_suppressed():
            return
        with self._stream_lock:
            buf = self._stream_buffer
            self._stream_buffer = ""
        if buf:
            self._app.call_from_thread(self._app_write, buf)
        self._app.call_from_thread(self._app_set_streaming, False)

    def _app_set_streaming(self, active):
        """Update streaming state and refresh the prompt marker."""
        self._app._streaming = active
        self._app._refresh_marker()

    def subagent_started(self):
        super().subagent_started()
        self._app.call_from_thread(self._app._refresh_marker)

    def subagent_finished(self):
        super().subagent_finished()
        self._app.call_from_thread(self._app._refresh_marker)

    def tool_log(self, message):
        self._write(message)

    def tool_result(self, line_count):
        self._write(dim(f"  → {line_count} lines of output"))

    def confirm(self, preview_lines, prompt_text):
        """Show preview, switch input to Y/n mode, block until answered."""
        for line in preview_lines:
            self._write(line)
        self._confirm_event.clear()
        self._app.call_from_thread(self._app_enter_confirm, prompt_text)
        self._confirm_event.wait()
        return self._confirm_result

    def _app_enter_confirm(self, prompt_text):
        """Hide PromptInput, mount a Yes/No ChoiceList for confirmation."""
        app = self._app
        app._confirm_mode = True
        inp = app.query_one("#prompt", PromptInput)
        inp.display = False

        choice_list = ChoiceList("Yes", "No", id="choice-list")
        container = app.query_one("#input-row", Horizontal)
        container.mount(choice_list)
        choice_list.focus()
        app.query_one("#status-tokens", Static).update(
            Text.from_ansi(dim("Enter to approve, ↓ N for reject"))
        )

    def ask_user(self, question, choices=None):
        """Show question, switch input to ask/selection mode, block until answered."""
        from llm_agent.formatting import bold as _bold
        text = f"\n  {_bold(question)}"
        if choices:
            for i, choice in enumerate(choices, 1):
                label = choice.get("label", "")
                desc = choice.get("description", "")
                if desc:
                    text += f"\n    {dim(str(i)+'.')} {label} — {dim(desc)}"
                else:
                    text += f"\n    {dim(str(i)+'.')} {label}"
        self._write(text)

        if choices:
            # Selection mode — OptionList widget
            self._selection_event.clear()
            self._app.call_from_thread(self._app_enter_selection_mode, choices)
            self._selection_event.wait()
            return self._selection_result
        else:
            # Free-text mode — existing ask_mode
            self._ask_event.clear()
            self._app.call_from_thread(self._app_enter_ask_mode)
            self._ask_event.wait()
            return self._ask_result

    def _app_enter_ask_mode(self):
        """Switch the input widget to ask mode."""
        app = self._app
        app._ask_mode = True
        inp = app.query_one("#prompt", PromptInput)
        inp.placeholder = "Type your answer..."
        inp.value = ""
        app.query_one("#status-tokens", Static).update(
            Text.from_ansi(dim("awaiting answer..."))
        )

    def _app_enter_selection_mode(self, choices):
        """Hide PromptInput, mount a ChoiceList widget for arrow-key selection."""
        app = self._app
        app._selection_mode = True
        inp = app.query_one("#prompt", PromptInput)
        inp.display = False

        labels = []
        for choice in choices:
            label = choice.get("label", "")
            desc = choice.get("description", "")
            if desc:
                labels.append(f"{label} — {desc}")
            else:
                labels.append(label)

        choice_list = ChoiceList(*labels, id="choice-list")
        container = app.query_one("#input-row", Horizontal)
        container.mount(choice_list)
        choice_list.focus()
        app.query_one("#status-tokens", Static).update(
            Text.from_ansi(dim("↑/↓ to navigate, Enter to select"))
        )

    def auto_approved(self, preview_lines):
        for line in preview_lines:
            self._write(line)
        self._write(dim("  (auto-approved)"))

    def status(self, message):
        self._write(dim(message))

    def error(self, message):
        self._write(message)

    def info(self, message):
        self._write(message)

    def info_stderr(self, message):
        # In TUI mode, stderr messages go to the conversation area too
        self._write(message)

    def update_status_bar(self, text):
        """Update the persistent status bar at the bottom."""
        self._app.call_from_thread(self._app_update_status, text)

    def _app_update_status(self, text):
        self._app.query_one("#status-tokens", Static).update(
            Text.from_ansi(text)
        )


# ---------------------------------------------------------------------------
# AgentApp — the Textual application
# ---------------------------------------------------------------------------

APP_CSS = """
Screen {
    background: $background;
}

#conversation {
    height: 1fr;
    background: $background;
    border: none;
    scrollbar-size: 1 1;
    padding: 0 1;
}

#input-row {
    height: auto;
    max-height: 10;
    background: $background;
    padding: 0 1;
}

#prompt-marker {
    width: 2;
    height: 1;
    color: $primary;
    background: $background;
    content-align: left middle;
}

#prompt {
    height: auto;
    min-height: 1;
    max-height: 8;
    background: $background;
    border: none;
    padding: 0;
    scrollbar-size: 0 0;
}

#prompt:focus {
    border: none;
}

#choice-list {
    height: auto;
    max-height: 8;
    background: $background;
    border: none;
    padding: 0;
    scrollbar-size: 0 0;
}

Rule {
    color: #d0d0d0;
    margin: 0;
}

#status-bar {
    height: 1;
    background: $surface;
    padding: 0 1;
}

#status-model {
    width: 1fr;
    height: 1;
    background: $surface;
    content-align: left middle;
    color: $text-muted;
}

#status-tokens {
    width: 2fr;
    height: 1;
    background: $surface;
    content-align: center middle;
    color: $text-muted;
}

#status-context {
    width: 1fr;
    height: 1;
    background: $surface;
    content-align: right middle;
    color: $text-muted;
}
"""


class AgentApp(App):
    CSS = APP_CSS
    TITLE = "llm-agent"
    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=False, priority=True),
        Binding("ctrl+q", "quit", "Quit", show=False),
    ]

    def __init__(self, session):
        super().__init__()
        self._session = session
        self._confirm_mode = False
        self._ask_mode = False
        self._selection_mode = False
        self._busy = False
        self._streaming = False
        self._tui_display = None
        self._history = []
        self._history_index = -1
        self._ctrl_d_pending = False

    def compose(self) -> ComposeResult:
        with Vertical():
            yield RichLog(id="conversation", wrap=True, markup=False)
            yield Rule(line_style="heavy")
            with Horizontal(id="input-row"):
                yield Static(">", id="prompt-marker")
                yield PromptInput(placeholder="Type a question...", id="prompt")
            yield Rule(line_style="heavy")
            with Horizontal(id="status-bar"):
                yield Static(id="status-model")
                yield Static(id="status-tokens")
                yield Static(id="status-context")

    def on_mount(self):
        # Apply light theme
        self.register_theme(LIGHT_THEME)
        self.theme = "agent-light"

        # Load input history
        import os
        history_file = os.path.expanduser("~/.agent_history")
        try:
            with open(history_file) as f:
                self._history = [line.rstrip("\n") for line in f.readlines()]
        except FileNotFoundError:
            pass

        # Set up TUI display
        self._tui_display = TUIDisplay(self)
        set_display(self._tui_display)

        # Show welcome
        mode = "YOLO mode" if self._session.auto_approve else "confirm mode"
        log = self.query_one("#conversation", RichLog)
        log.write(Text.from_ansi(
            f"{bold('Agent ready')} {dim(f'(model: {self._session.model}, {mode})')}"
        ))
        log.write(Text.from_ansi(
            dim("Type a question, /clear, /copy, /mcp, /model, /thinking, /skills, /version, or 'quit'.")
        ))

        self._update_status_bar()
        self._update_title()
        self.query_one("#prompt", PromptInput).focus()

    def _update_title(self):
        """Set terminal title to 'llm-agent — cwd' via OSC escape."""
        from llm_agent.tools.base import shell
        title = f"llm-agent — {shell.cwd}"
        # Write directly to the terminal, bypassing Textual's stdout capture
        try:
            with open("/dev/tty", "w") as tty:
                tty.write(f"\033]0;{title}\007")
                tty.flush()
        except OSError:
            pass

    def _update_status_bar(self, turn_usage=None):
        # Model + mode
        mode = "YOLO" if self._session.auto_approve else "confirm"
        self.query_one("#status-model", Static).update(
            Text.from_ansi(dim(f" {self._session.model} ({mode})"))
        )

        # Token usage: turn + session
        token_parts = []
        if turn_usage and (turn_usage.get("input", 0) > 0 or turn_usage.get("output", 0) > 0):
            cache_info = ""
            if turn_usage.get("cache_read", 0) > 0:
                cache_info = f", {format_tokens(turn_usage['cache_read'])} cached"
            token_parts.append(
                f"turn: {format_tokens(turn_usage['input'])} in, "
                f"{format_tokens(turn_usage['output'])} out{cache_info}"
            )
        su = self._session.session_usage
        if su["input"] > 0 or su["output"] > 0:
            token_parts.append(
                f"session: {format_tokens(su['input'])} in, "
                f"{format_tokens(su['output'])} out"
            )
        self.query_one("#status-tokens", Static).update(
            Text.from_ansi(dim(" | ".join(token_parts))) if token_parts else ""
        )

        # Context remaining
        context_text = ""
        if turn_usage:
            last_input = turn_usage.get("last_input", 0)
            if last_input > 0:
                from llm_agent.cli import CONTEXT_WINDOWS
                window = CONTEXT_WINDOWS.get(self._session.model, 200_000)
                remaining_pct = max(0, (window - last_input) / window * 100)
                context_text = dim(f"context: {remaining_pct:.0f}% remaining ")
        self.query_one("#status-context", Static).update(
            Text.from_ansi(context_text)
        )

    def _save_history(self, text):
        self._history.append(text)
        # Keep last 1000
        self._history = self._history[-1000:]
        self._history_index = -1
        import os
        history_file = os.path.expanduser("~/.agent_history")
        try:
            with open(history_file, "w") as f:
                for line in self._history:
                    f.write(line + "\n")
        except OSError:
            pass

    def _history_prev(self, inp):
        if self._history:
            if self._history_index == -1:
                self._history_index = len(self._history) - 1
            elif self._history_index > 0:
                self._history_index -= 1
            inp.value = self._history[self._history_index]
            inp.cursor_position = len(inp.value)

    def _history_next(self, inp):
        if self._history_index >= 0:
            self._history_index += 1
            if self._history_index >= len(self._history):
                self._history_index = -1
                inp.value = ""
            else:
                inp.value = self._history[self._history_index]
                inp.cursor_position = len(inp.value)

    def on_key(self, event):
        """Handle arrow keys and Ctrl+P/N for input history, Ctrl+D for quit/delete."""
        inp = self.query_one("#prompt", PromptInput)
        if not inp.has_focus:
            return
        if self._confirm_mode or self._ask_mode or self._selection_mode:
            return
        if event.key == "ctrl+d":
            if inp.value == "":
                if self._ctrl_d_pending:
                    self.exit()
                else:
                    self._ctrl_d_pending = True
                    log = self.query_one("#conversation", RichLog)
                    log.write(Text("Press Ctrl-D again to exit."))
            else:
                self._ctrl_d_pending = False
                inp.action_delete_right()
            event.prevent_default()
        elif event.key in ("up", "ctrl+p"):
            # Only use history if on the first line (or single-line input)
            row, _ = inp.cursor_location
            if row == 0:
                self._history_prev(inp)
                event.prevent_default()
        elif event.key in ("down", "ctrl+n"):
            # Only use history if on the last line (or single-line input)
            row, _ = inp.cursor_location
            lines = inp.text.split("\n")
            if row >= len(lines) - 1:
                self._history_next(inp)
                event.prevent_default()

    def on_prompt_input_submitted(self, event: PromptInput.Submitted):
        self._ctrl_d_pending = False
        text = event.value.strip()

        # Handle ask mode
        if self._ask_mode:
            self._ask_mode = False
            inp = self.query_one("#prompt", PromptInput)
            inp.placeholder = "Type a question..."
            log = self.query_one("#conversation", RichLog)
            log.write(Text.from_ansi(dim(f"  → {text}" if text else "  → (no answer provided)")))
            self._tui_display._ask_result = text or "(no answer provided)"
            self._tui_display._ask_event.set()
            return

        if not text:
            return

        self._save_history(text)
        log = self.query_one("#conversation", RichLog)

        # Show user input
        log.write(Text.from_ansi(f"\n{bold('>')} {text}"))

        # Handle quit
        if text.lower() in ("quit", "exit"):
            self.exit()
            return

        # TUI-specific /copy command
        if text == "/copy":
            if self._session.last_response:
                self.copy_to_clipboard(self._session.last_response)
                log.write(Text.from_ansi(dim("(last response copied to clipboard)")))
            else:
                log.write(Text.from_ansi(dim("(no response to copy)")))
            return

        # Handle commands and skills via Session
        result = self._session.handle_command(text)
        if result is not None:
            messages, transformed = result
            if text.strip() == "/clear":
                log.clear()
            for msg in messages:
                log.write(Text.from_ansi(dim(msg)))
            self._update_status_bar()
            if transformed is None:
                return
            text = transformed

        # Run the question in a worker thread
        self._run_agent(text)

    def _exit_choice_list(self):
        """Remove ChoiceList widget and restore PromptInput."""
        choice_list = self.query_one("#choice-list", ChoiceList)
        choice_list.remove()
        inp = self.query_one("#prompt", PromptInput)
        inp.display = True
        inp.focus()

    def on_option_list_option_selected(self, event):
        """Handle arrow-key selection from the ChoiceList widget."""
        if self._confirm_mode:
            self._confirm_mode = False
            approved = str(event.option.prompt) == "Yes"
            log = self.query_one("#conversation", RichLog)
            log.write(Text.from_ansi(dim(f"  → {'yes' if approved else 'no'}")))
            self._exit_choice_list()
            self._tui_display._confirm_result = approved
            self._tui_display._confirm_event.set()
            return

        if not self._selection_mode:
            return
        self._selection_mode = False
        selected_label = str(event.option.prompt)
        # Strip description suffix if present
        if " — " in selected_label:
            selected_label = selected_label.split(" — ", 1)[0]

        log = self.query_one("#conversation", RichLog)
        log.write(Text.from_ansi(dim(f"  → {selected_label}")))
        self._exit_choice_list()

        self._tui_display._selection_result = selected_label
        self._tui_display._selection_event.set()

    def on_choice_list_cancelled(self, event):
        """Handle Escape — reject confirmation or cancel selection."""
        log = self.query_one("#conversation", RichLog)

        if self._confirm_mode:
            self._confirm_mode = False
            log.write(Text.from_ansi(dim("  → no")))
            self._exit_choice_list()
            self._tui_display._confirm_result = False
            self._tui_display._confirm_event.set()
            return

        if not self._selection_mode:
            return
        self._selection_mode = False
        log.write(Text.from_ansi(dim("  → (no answer provided)")))
        self._exit_choice_list()

        self._tui_display._selection_result = "(no answer provided)"
        self._tui_display._selection_event.set()

    def _set_busy(self, active):
        """Update busy state and refresh the prompt marker."""
        self._busy = active
        self._refresh_marker()

    def _refresh_marker(self):
        """Update the prompt marker based on current state.

        States:  >  idle (green)
                 ~  actively streaming (amber)
                 ·  busy, not streaming (gray)
                 ·N busy with N active subagents (gray)
        """
        marker = self.query_one("#prompt-marker", Static)
        if not self._busy:
            marker.update(Text(">", style="bold #2e8b57"))
        elif self._streaming:
            marker.update(Text("~", style="bold italic #d29922"))
        else:
            count = self._tui_display.active_subagents if self._tui_display else 0
            if count > 0:
                marker.update(Text(f"·{count}", style="bold #6a737d"))
            else:
                marker.update(Text("·", style="#6a737d"))

    @work(thread=True)
    def _run_agent(self, user_input):
        """Run the agent in a background thread."""
        self.call_from_thread(self._set_busy, True)
        try:
            success, turn_usage = self._session.run_question(user_input)
        finally:
            self.call_from_thread(self._set_busy, False)

        self.call_from_thread(self._update_status_bar, turn_usage)
        self.call_from_thread(self._update_title)

        if turn_usage.get("trimmed", 0) > 0:
            self._tui_display.status(
                f"  (trimmed {turn_usage['trimmed']} old messages to fit context window)"
            )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_tui(session):
    """Launch the Textual TUI."""
    import os as _os

    from llm_agent.cli import reset_terminal_title
    app = AgentApp(session)
    try:
        app.run()
    finally:
        reset_terminal_title()
        # Shut down MCP servers before force-exit
        try:
            from llm_agent.mcp_client import get_mcp_manager
            get_mcp_manager().stop()
        except Exception:
            pass
        # Force-exit to avoid hanging on Textual's asyncio thread cleanup.
        # Without this, _Py_Finalize waits indefinitely for Textual's
        # non-daemon event loop thread which may be stuck in a file-watching
        # scandir loop, spinning at 100% CPU.
        _os._exit(0)
