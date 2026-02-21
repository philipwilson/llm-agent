"""Colour helpers, output formatting, and token display."""

import os
import sys


# --- Colour helpers (ANSI) ---

def _supports_color():
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

USE_COLOR = _supports_color()

def _ansi(code):
    def wrap(text):
        if not USE_COLOR:
            return text
        return f"\033[{code}m{text}\033[0m"
    return wrap

bold    = _ansi("1")
dim     = _ansi("2")
red     = _ansi("31")
green   = _ansi("32")
yellow  = _ansi("33")
cyan    = _ansi("36")


# --- Output truncation ---

MAX_OUTPUT_LINES = 200

def truncate(text, max_lines=MAX_OUTPUT_LINES):
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    half = max_lines // 2
    kept = lines[:half] + [f"\n... ({len(lines) - max_lines} lines omitted) ...\n"] + lines[-half:]
    return "\n".join(kept)


# --- Token formatting ---

def format_tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)
