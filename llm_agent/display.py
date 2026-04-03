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

from llm_agent.formatting import bold, dim


_DEFAULT_ANSWER = "(no answer provided)"


def _normalize_choice_answer(answer, choices):
    if not isinstance(answer, str):
        return _DEFAULT_ANSWER
    answer = answer.strip() or _DEFAULT_ANSWER
    if not choices:
        return answer
    if answer.isdigit():
        idx = int(answer) - 1
        if 0 <= idx < len(choices):
            return choices[idx].get("label", answer)
    folded = answer.casefold()
    for choice in choices:
        label = choice.get("label", "")
        if label.casefold() == folded:
            return label
    return answer


def _question_heading_and_prompt(question_spec, index, total):
    header = question_spec.get("header", "").strip()
    prompt = question_spec.get("question", "").strip()
    heading_parts = []
    if total > 1:
        heading_parts.append(f"[{index}/{total}]")
    if header:
        heading_parts.append(header)
    if heading_parts:
        return " ".join(heading_parts), prompt
    return prompt, None


def _format_answers_summary_lines(questions, answers):
    lines = ["", f"  {dim('Recorded answers:')}"]
    for question in questions:
        label = question.get("header") or question.get("id") or question.get("question", "")
        answer = answers.get(question.get("id"), _DEFAULT_ANSWER)
        lines.append(f"    {label}: {dim(answer)}")
    return lines


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
        self._subagent_count = 0
        self._subagent_lock = threading.Lock()

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

    def subagent_started(self):
        """Increment the active subagent count."""
        with self._subagent_lock:
            self._subagent_count += 1

    def subagent_finished(self):
        """Decrement the active subagent count."""
        with self._subagent_lock:
            self._subagent_count = max(0, self._subagent_count - 1)

    @property
    def active_subagents(self):
        return self._subagent_count

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

    def _ask_single_question(self, title, prompt=None, choices=None):
        with self._lock:
            print(f"\n  {bold(title)}")
            if prompt:
                print(f"  {prompt}")
            if choices:
                for i, choice in enumerate(choices, 1):
                    label = choice.get("label", "")
                    desc = choice.get("description", "")
                    if desc:
                        print(f"    {dim(str(i)+'.')} {label} — {dim(desc)}")
                    else:
                        print(f"    {dim(str(i)+'.')} {label}")
            try:
                answer = input(f"  {dim('>')} ").strip()
            except EOFError:
                answer = ""
        return answer or _DEFAULT_ANSWER

    def ask_user(self, question, choices=None):
        """Ask the user a clarifying question.

        Args:
            question: the question to display, or a list of structured questions.
            choices: optional list of dicts with 'label' and optional 'description'.

        Returns:
            A string answer for legacy single-question prompts, or a dict of answers
            keyed by question ID for structured prompts.
        """
        if isinstance(question, list):
            answers = {}
            total = len(question)
            for index, question_spec in enumerate(question, 1):
                title, prompt = _question_heading_and_prompt(question_spec, index, total)
                answer = self._ask_single_question(
                    title, prompt, question_spec.get("options")
                )
                answers[question_spec["id"]] = _normalize_choice_answer(
                    answer, question_spec.get("options")
                )
            with self._lock:
                for line in _format_answers_summary_lines(question, answers):
                    print(line)
            return answers

        return self._ask_single_question(question, None, choices)

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
