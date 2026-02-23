"""Textual TUI for interactive mode."""

import threading

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.theme import Theme
from textual.widgets import Input, RichLog, Rule, Static
from rich.text import Text

from llm_agent import VERSION
from llm_agent.display import Display, set_display, get_display
from llm_agent.formatting import bold, dim, format_tokens


# ---------------------------------------------------------------------------
# ReadlineInput — Input widget with Emacs/readline keybindings
# ---------------------------------------------------------------------------

class ReadlineInput(Input):
    """Input widget with Emacs-style keybindings."""

    BINDINGS = [
        Binding("ctrl+a", "home", "Home", show=False),
        Binding("ctrl+e", "end", "End", show=False),
        Binding("ctrl+f", "cursor_right", "Forward char", show=False),
        Binding("ctrl+b", "cursor_left", "Back char", show=False),
        Binding("ctrl+d", "delete_right", "Delete char", show=False),
        Binding("ctrl+k", "delete_right_all", "Kill to end", show=False),
        Binding("ctrl+u", "delete_left_all", "Kill to start", show=False),
        Binding("ctrl+w", "delete_left_word", "Delete word back", show=False),
        Binding("ctrl+h", "delete_left", "Backspace", show=False),
    ]


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
        inp = app.query_one("#prompt", ReadlineInput)
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
    height: 1;
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
    height: 1;
    background: $background;
    border: none;
    padding: 0;
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
                yield ReadlineInput(placeholder="Type a question...", id="prompt")
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

        # Set up delegate
        from llm_agent.cli import setup_delegate
        setup_delegate(self._client, self._model, self._auto_approve,
                       self._thinking_level)

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
        self.query_one("#prompt", ReadlineInput).focus()

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
        """Handle arrow keys and Ctrl+P/N for input history."""
        inp = self.query_one("#prompt", ReadlineInput)
        if not inp.has_focus:
            return
        if self._confirm_mode:
            return
        if event.key in ("up", "ctrl+p"):
            self._history_prev(inp)
            event.prevent_default()
        elif event.key in ("down", "ctrl+n"):
            self._history_next(inp)
            event.prevent_default()

    def on_input_submitted(self, event: Input.Submitted):
        text = event.value.strip()
        inp = self.query_one("#prompt", ReadlineInput)
        inp.value = ""

        # Handle confirmation mode
        if self._confirm_mode:
            self._confirm_mode = False
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
        self._conversation = trim_conversation(self._conversation, last_input, self._model)
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
    app = AgentApp(client, model, auto_approve=auto_approve,
                   thinking_level=thinking_level)
    app.run()
