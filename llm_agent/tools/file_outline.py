"""file_outline tool: show file structure (classes, functions, methods) with line numbers."""

import os
import re

from llm_agent.formatting import bold, cyan
from llm_agent.tools.base import _resolve


SCHEMA = {
    "name": "file_outline",
    "description": (
        "Show the structure of a file — classes, functions, methods, and other "
        "top-level definitions with line numbers — without reading the full content. "
        "Use this to understand large files quickly before diving in with read_file."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to outline.",
            },
        },
        "required": ["path"],
    },
}


def log(params):
    from llm_agent.display import get_display
    get_display().tool_log(f"  {bold('file_outline')}: {cyan(params.get('path', ''))}")

LOG = log


# ---------------------------------------------------------------------------
# Language-specific regex patterns
# ---------------------------------------------------------------------------

# Each pattern list contains (regex, label_template) tuples.
# The regex should match at the start of a logical line.
# Groups are used to extract the symbol name.

_PYTHON = [
    (re.compile(r"^( *)class\s+(\w+)"), lambda m: (len(m.group(1)), f"class {m.group(2)}")),
    (re.compile(r"^( *)async\s+def\s+(\w+)\s*\((.*)"), lambda m: (len(m.group(1)), f"async def {m.group(2)}({m.group(3).split(')')[0]})")),
    (re.compile(r"^( *)def\s+(\w+)\s*\((.*)"), lambda m: (len(m.group(1)), f"def {m.group(2)}({m.group(3).split(')')[0]})")),
]

_JAVASCRIPT = [
    (re.compile(r"^(\s*)export\s+(?:default\s+)?class\s+(\w+)"), lambda m: (len(m.group(1)), f"class {m.group(2)}")),
    (re.compile(r"^(\s*)class\s+(\w+)"), lambda m: (len(m.group(1)), f"class {m.group(2)}")),
    (re.compile(r"^(\s*)export\s+(?:default\s+)?(?:async\s+)?function\s+(\w+)"), lambda m: (len(m.group(1)), f"function {m.group(2)}")),
    (re.compile(r"^(\s*)(?:async\s+)?function\s+(\w+)"), lambda m: (len(m.group(1)), f"function {m.group(2)}")),
    (re.compile(r"^(\s*)(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\("), lambda m: (len(m.group(1)), f"const {m.group(2)} = (...) =>")),
    (re.compile(r"^(\s*)(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?function"), lambda m: (len(m.group(1)), f"const {m.group(2)} = function")),
    (re.compile(r"^(\s*)(?:export\s+)?interface\s+(\w+)"), lambda m: (len(m.group(1)), f"interface {m.group(2)}")),
    (re.compile(r"^(\s*)(?:export\s+)?type\s+(\w+)\s*="), lambda m: (len(m.group(1)), f"type {m.group(2)}")),
    (re.compile(r"^(\s*)(?:export\s+)?enum\s+(\w+)"), lambda m: (len(m.group(1)), f"enum {m.group(2)}")),
]

_GO = [
    (re.compile(r"^func\s+\((\w+)\s+\*?(\w+)\)\s+(\w+)\("), lambda m: (0, f"func ({m.group(2)}) {m.group(3)}()")),
    (re.compile(r"^func\s+(\w+)\("), lambda m: (0, f"func {m.group(1)}()")),
    (re.compile(r"^type\s+(\w+)\s+struct\b"), lambda m: (0, f"type {m.group(1)} struct")),
    (re.compile(r"^type\s+(\w+)\s+interface\b"), lambda m: (0, f"type {m.group(1)} interface")),
    (re.compile(r"^type\s+(\w+)\s+"), lambda m: (0, f"type {m.group(1)}")),
]

_RUST = [
    (re.compile(r"^(\s*)pub\s+struct\s+(\w+)"), lambda m: (len(m.group(1)), f"pub struct {m.group(2)}")),
    (re.compile(r"^(\s*)struct\s+(\w+)"), lambda m: (len(m.group(1)), f"struct {m.group(2)}")),
    (re.compile(r"^(\s*)pub\s+enum\s+(\w+)"), lambda m: (len(m.group(1)), f"pub enum {m.group(2)}")),
    (re.compile(r"^(\s*)enum\s+(\w+)"), lambda m: (len(m.group(1)), f"enum {m.group(2)}")),
    (re.compile(r"^(\s*)pub\s+trait\s+(\w+)"), lambda m: (len(m.group(1)), f"pub trait {m.group(2)}")),
    (re.compile(r"^(\s*)trait\s+(\w+)"), lambda m: (len(m.group(1)), f"trait {m.group(2)}")),
    (re.compile(r"^(\s*)impl(?:<[^>]*>)?\s+(\w+)"), lambda m: (len(m.group(1)), f"impl {m.group(2)}")),
    (re.compile(r"^(\s*)pub(?:\(crate\))?\s+(?:async\s+)?fn\s+(\w+)"), lambda m: (len(m.group(1)), f"pub fn {m.group(2)}")),
    (re.compile(r"^(\s*)(?:async\s+)?fn\s+(\w+)"), lambda m: (len(m.group(1)), f"fn {m.group(2)}")),
]

_JAVA = [
    (re.compile(r"^(\s*)(?:public|private|protected)?\s*(?:static\s+)?(?:abstract\s+)?class\s+(\w+)"), lambda m: (len(m.group(1)), f"class {m.group(2)}")),
    (re.compile(r"^(\s*)(?:public|private|protected)?\s*interface\s+(\w+)"), lambda m: (len(m.group(1)), f"interface {m.group(2)}")),
    (re.compile(r"^(\s*)(?:public|private|protected)?\s*enum\s+(\w+)"), lambda m: (len(m.group(1)), f"enum {m.group(2)}")),
    (re.compile(r"^(\s*)(?:public|private|protected)\s+(?:static\s+)?(?:final\s+)?(?:abstract\s+)?(?:synchronized\s+)?\w[\w<>\[\],\s]*\s+(\w+)\s*\("), lambda m: (len(m.group(1)), f"method {m.group(2)}()")),
]

_RUBY = [
    (re.compile(r"^(\s*)class\s+(\w+)"), lambda m: (len(m.group(1)), f"class {m.group(2)}")),
    (re.compile(r"^(\s*)module\s+(\w+)"), lambda m: (len(m.group(1)), f"module {m.group(2)}")),
    (re.compile(r"^(\s*)def\s+(\w+[\?\!]?)"), lambda m: (len(m.group(1)), f"def {m.group(2)}")),
]

_C_CPP = [
    (re.compile(r"^(\s*)class\s+(\w+)"), lambda m: (len(m.group(1)), f"class {m.group(2)}")),
    (re.compile(r"^(\s*)struct\s+(\w+)\s*\{"), lambda m: (len(m.group(1)), f"struct {m.group(2)}")),
    (re.compile(r"^(\s*)enum\s+(?:class\s+)?(\w+)"), lambda m: (len(m.group(1)), f"enum {m.group(2)}")),
    (re.compile(r"^(\s*)namespace\s+(\w+)"), lambda m: (len(m.group(1)), f"namespace {m.group(2)}")),
    # Function definitions (type name(...) {)
    (re.compile(r"^(\s*)(?:static\s+)?(?:inline\s+)?(?:virtual\s+)?(?:const\s+)?\w[\w:*&<>\s]*\s+(\w+)\s*\([^;]*$"), lambda m: (len(m.group(1)), f"{m.group(2)}()")),
]

_FALLBACK = [
    (re.compile(r"^(\s*)(?:class|struct|interface|enum|module)\s+(\w+)"), lambda m: (len(m.group(1)), f"{m.group(0).strip().split()[0]} {m.group(2)}")),
    (re.compile(r"^(\s*)(?:def|func|function|fn|sub)\s+(\w+)"), lambda m: (len(m.group(1)), f"{m.group(0).strip().split()[0]} {m.group(2)}")),
]

_LANG_PATTERNS = {
    ".py": _PYTHON,
    ".js": _JAVASCRIPT,
    ".jsx": _JAVASCRIPT,
    ".ts": _JAVASCRIPT,
    ".tsx": _JAVASCRIPT,
    ".mjs": _JAVASCRIPT,
    ".go": _GO,
    ".rs": _RUST,
    ".java": _JAVA,
    ".cs": _JAVA,  # C# is similar enough
    ".rb": _RUBY,
    ".c": _C_CPP,
    ".cpp": _C_CPP,
    ".cc": _C_CPP,
    ".cxx": _C_CPP,
    ".h": _C_CPP,
    ".hpp": _C_CPP,
    ".hxx": _C_CPP,
}


def _extract_symbols(lines, patterns):
    """Extract symbols from lines using regex patterns.

    Returns list of (line_number, indent_level, label) tuples.
    """
    symbols = []
    for i, line in enumerate(lines, start=1):
        for pattern, extractor in patterns:
            m = pattern.match(line)
            if m:
                indent, label = extractor(m)
                symbols.append((i, indent, label))
                break  # first match wins per line
    return symbols


def handle(params):
    path = _resolve(params.get("path", ""))

    try:
        with open(path, "r", errors="replace") as f:
            lines = f.readlines()
    except IsADirectoryError:
        return f"(error: {path} is a directory, use list_directory instead)"
    except Exception as e:
        return f"(error reading {path}: {e})"

    total = len(lines)
    size = os.path.getsize(path)
    _, ext = os.path.splitext(path)
    patterns = _LANG_PATTERNS.get(ext.lower(), _FALLBACK)

    raw_lines = [line.rstrip("\n") for line in lines]
    symbols = _extract_symbols(raw_lines, patterns)

    if not symbols:
        return f"[{path}: {total} lines, {size} bytes — no symbols found]"

    # Format output with indentation showing nesting
    header = f"[{path}: {len(symbols)} symbols, {total} lines]"
    result = [header]

    # For Python (and similar), convert absolute indent to relative nesting
    if ext.lower() in (".py", ".rb"):
        # Use indent level directly — each 4 spaces = 1 nesting level
        for lineno, indent, label in symbols:
            nest = indent // 4
            result.append(f"{lineno:6}  {'  ' * nest}{label}")
    else:
        for lineno, indent, label in symbols:
            nest = indent // 4 if indent > 0 else 0
            result.append(f"{lineno:6}  {'  ' * nest}{label}")

    return "\n".join(result)
