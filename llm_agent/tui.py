"""Textual TUI for interactive mode."""

import threading

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.theme import Theme
from textual.widgets import RichLog, Rule, Static, TextArea
from rich.text import Text

from llm_agent import VERSION
from llm_agent.display import Display, set_display, get_display
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
        # TextArea has a placeholder reactive, set it directly
        TextArea.placeholder.fset(self, val)

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
        self._app = app
        self._confirm_event = threading.Event()
        self._confirm_result = False
        # Accumulate the full streamed response, write once at stream_end.
        # Each RichLog.write() creates an independent block that wraps at
        # its own width, so writing per-batch produces narrow paragraphs.
        self._stream_buffer = ""
        self._stream_lock = threading.Lock()
        self._stream_char_count = 0

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
        with self._stream_lock:
            self._stream_buffer = ""
            self._stream_char_count = 0
        self._app.call_from_thread(self._app_set_streaming, True)

    def stream_token(self, text):
        with self._stream_lock:
            self._stream_buffer += text
            self._stream_char_count += len(text)

    def stream_end(self):
        with self._stream_lock:
            buf = self._stream_buffer
            self._stream_buffer = ""
            self._stream_char_count = 0
        if buf:
            self._app.call_from_thread(self._app_write, buf)
        self._app.call_from_thread(self._app_set_streaming, False)

    def _app_set_streaming(self, active):
        """Show/hide a streaming indicator in the prompt marker."""
        marker = self._app.query_one("#prompt-marker", Static)
        if active:
            marker.update(Text("~", style="bold italic #d29922"))
        else:
            marker.update(Text(">", style="bold #2e8b57"))

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
        """Switch the input widget to confirmation mode."""
        app = self._app
        app._confirm_mode = True
        inp = app.query_one("#prompt", PromptInput)
        inp.placeholder = prompt_text
        inp.value = ""
        app.query_one("#status-tokens", Static).update(
            Text.from_ansi(dim("awaiting confirmation..."))
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

    def __init__(self, client, model, auto_approve=False, thinking_level=None):
        super().__init__()
        self._client = client
        self._model = model
        self._auto_approve = auto_approve
        self._thinking_level = thinking_level
        self._conversation = []
        self._session_usage = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}
        self._confirm_mode = False
        self._tui_display = None
        self._history = []
        self._history_index = -1
        self._last_response = ""  # last assistant text, for /copy

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

        # Load skills
        from llm_agent.skills import load_all_skills
        self._skills = load_all_skills()

        # Set up delegate and project context
        from llm_agent.cli import setup_delegate
        setup_delegate(self._client, self._model, self._auto_approve,
                       self._thinking_level)
        from llm_agent.agent import refresh_project_context
        refresh_project_context()

        # Show welcome
        mode = "YOLO mode" if self._auto_approve else "confirm mode"
        log = self.query_one("#conversation", RichLog)
        log.write(Text.from_ansi(
            f"{bold('Agent ready')} {dim(f'(model: {self._model}, {mode})')}"
        ))
        log.write(Text.from_ansi(
            dim("Type a question, /clear, /copy, /model, /thinking, /skills, /version, or 'quit'.")
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
        mode = "YOLO" if self._auto_approve else "confirm"
        self.query_one("#status-model", Static).update(
            Text.from_ansi(dim(f" {self._model} ({mode})"))
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
        su = self._session_usage
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
                window = CONTEXT_WINDOWS.get(self._model, 200_000)
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
        if self._confirm_mode:
            return
        if event.key == "ctrl+d":
            if inp.value == "":
                self.exit()
            else:
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
        text = event.value.strip()

        # Handle confirmation mode
        if self._confirm_mode:
            self._confirm_mode = False
            inp = self.query_one("#prompt", PromptInput)
            inp.placeholder = "Type a question..."
            answer = text.lower()
            self._tui_display._confirm_result = answer in ("", "y", "yes")
            log = self.query_one("#conversation", RichLog)
            if self._tui_display._confirm_result:
                log.write(Text.from_ansi(dim("  → yes")))
            else:
                log.write(Text.from_ansi(dim("  → no")))
            self._tui_display._confirm_event.set()
            return

        if not text:
            return

        self._save_history(text)
        log = self.query_one("#conversation", RichLog)

        # Show user input
        log.write(Text.from_ansi(f"\n{bold('>')} {text}"))

        # Handle built-in commands
        if text.lower() in ("quit", "exit"):
            self.exit()
            return

        if text == "/clear":
            self._conversation = []
            self._session_usage = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}
            log.clear()
            log.write(Text.from_ansi(dim("(conversation cleared)")))
            self._update_status_bar()
            return

        if text == "/version":
            log.write(Text.from_ansi(dim(f"llm-agent v{VERSION} (model: {self._model})")))
            return

        if text.startswith("/model"):
            self._handle_model_command(text)
            return

        if text.startswith("/thinking"):
            self._handle_thinking_command(text)
            return

        if text == "/skills":
            self._handle_skills_command()
            return

        if text == "/copy":
            if self._last_response:
                self.copy_to_clipboard(self._last_response)
                log.write(Text.from_ansi(dim("(last response copied to clipboard)")))
            else:
                log.write(Text.from_ansi(dim("(no response to copy)")))
            return

        if text.startswith("/"):
            parts = text.split(None, 1)
            skill_name = parts[0][1:]
            if skill_name in self._skills:
                args_string = parts[1] if len(parts) > 1 else ""
                from llm_agent.skills import render_skill
                text = render_skill(self._skills[skill_name], args_string)
                log.write(Text.from_ansi(dim(f"  (skill: {skill_name})")))
            else:
                log.write(Text.from_ansi(dim(f"(unknown command '/{skill_name}')")))
                return

        # Run the question in a worker thread
        self._run_agent(text)

    @work(thread=True)
    def _run_agent(self, user_input):
        """Run the agent in a background thread."""
        from llm_agent.cli import run_question, trim_conversation, CONTEXT_WINDOWS

        result, turn_usage = run_question(
            self._client, self._model, self._conversation, user_input,
            self._auto_approve, thinking_level=self._thinking_level,
        )

        for key in ("input", "output", "cache_read", "cache_create"):
            self._session_usage[key] += turn_usage[key]

        self.call_from_thread(self._update_status_bar, turn_usage)
        self.call_from_thread(self._update_title)

        if result is None:
            return

        # Extract last assistant text for /copy
        for msg in reversed(result):
            if msg.get("role") == "assistant":
                content = msg.get("content")
                if isinstance(content, str):
                    self._last_response = content
                elif isinstance(content, list):
                    texts = [b["text"] for b in content if b.get("type") == "text" and b.get("text")]
                    if texts:
                        self._last_response = "\n".join(texts)
                break

        self._conversation = result
        last_input = turn_usage.get("last_input", 0)
        old_len = len(self._conversation)
        self._conversation = trim_conversation(
            self._conversation, last_input, self._model, client=self._client
        )
        if len(self._conversation) < old_len:
            removed = old_len - len(self._conversation)
            self._tui_display.status(f"  (trimmed {removed} old messages to fit context window)")

    def _handle_model_command(self, text):
        from llm_agent.cli import MODELS, is_gemini_model, is_openai_model, make_client, setup_delegate, DEFAULT_THINKING
        from llm_agent.skills import load_all_skills
        log = self.query_one("#conversation", RichLog)
        parts = text.strip().split()
        if len(parts) == 1:
            log.write(Text.from_ansi(dim(f"(model: {self._model})")))
            log.write(Text.from_ansi(dim(f"  available: {', '.join(MODELS.keys())}")))
        elif parts[1] in MODELS:
            new_model = MODELS[parts[1]]
            old_provider = ("gemini" if is_gemini_model(self._model)
                            else "openai" if is_openai_model(self._model)
                            else "anthropic")
            new_provider = ("gemini" if is_gemini_model(new_model)
                            else "openai" if is_openai_model(new_model)
                            else "anthropic")
            if new_provider != old_provider:
                self._client = make_client(new_model)
                self._conversation = []
                log.write(Text.from_ansi(dim(f"(switched to {new_model}, conversation cleared)")))
            else:
                log.write(Text.from_ansi(dim(f"(switched to {new_model})")))
            self._model = new_model
            default_thinking = DEFAULT_THINKING.get(new_model)
            if default_thinking and not self._thinking_level:
                self._thinking_level = default_thinking
                log.write(Text.from_ansi(dim(f"(thinking: {self._thinking_level})")))
            setup_delegate(self._client, self._model, self._auto_approve, self._thinking_level)
            self._skills = load_all_skills()
            self._update_status_bar()
        else:
            log.write(Text.from_ansi(dim(f"(unknown model '{parts[1]}', available: {', '.join(MODELS.keys())})")))

    def _handle_thinking_command(self, text):
        from llm_agent.cli import is_gemini_model
        log = self.query_one("#conversation", RichLog)
        parts = text.strip().split()
        if len(parts) == 1:
            level = self._thinking_level or "off (model default)"
            log.write(Text.from_ansi(dim(f"(thinking: {level})")))
        elif parts[1] == "off":
            self._thinking_level = None
            log.write(Text.from_ansi(dim("(thinking: off, model decides)")))
        elif parts[1] in ("low", "medium", "high"):
            if not is_gemini_model(self._model):
                log.write(Text.from_ansi(dim("(warning: --thinking is only supported for Gemini models)")))
            self._thinking_level = parts[1]
            log.write(Text.from_ansi(dim(f"(thinking: {self._thinking_level})")))
        else:
            log.write(Text.from_ansi(dim(f"(unknown thinking level '{parts[1]}', use low/medium/high/off)")))

    def _handle_skills_command(self):
        from llm_agent.skills import load_all_skills, format_skill_list
        log = self.query_one("#conversation", RichLog)
        self._skills = load_all_skills()
        if self._skills:
            log.write(Text.from_ansi(dim("Available skills:")))
            log.write(Text.from_ansi(dim(format_skill_list(self._skills))))
        else:
            log.write(Text.from_ansi(dim("(no skills found — add SKILL.md files in .skills/ or ~/.skills/)")))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_tui(client, model, auto_approve=False, thinking_level=None):
    """Launch the Textual TUI."""
    import os as _os

    from llm_agent.cli import reset_terminal_title
    app = AgentApp(client, model, auto_approve=auto_approve,
                   thinking_level=thinking_level)
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
