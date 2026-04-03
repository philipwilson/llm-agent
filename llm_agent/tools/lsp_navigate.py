"""lsp_navigate tool: optional LSP-backed code navigation."""

import atexit
import json
import os
import shutil
import site
import subprocess
import sysconfig
import threading
import urllib.parse

from llm_agent.formatting import bold, cyan
from llm_agent.tools.base import _resolve, COMMAND_TIMEOUT, read_text_file


SCHEMA = {
    "name": "lsp_navigate",
    "description": (
        "Use a local language server for semantic code navigation. Supports "
        "document_symbols, definition, references, and hover. Requires a compatible "
        "language server to be installed locally for the file type."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "One of: document_symbols, definition, references, hover.",
            },
            "path": {
                "type": "string",
                "description": "File path to navigate within.",
            },
            "line": {
                "type": "integer",
                "description": "1-based line number for definition/references/hover.",
            },
            "column": {
                "type": "integer",
                "description": "1-based column number for definition/references/hover.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return for document_symbols or references (default: 50).",
            },
            "include_declaration": {
                "type": "boolean",
                "description": "For references, whether to include the symbol declaration (default: false).",
            },
        },
        "required": ["action", "path"],
    },
}


def log(params):
    from llm_agent.display import get_display

    action = params.get("action", "")
    path = params.get("path", "")
    get_display().tool_log(f"  {bold('lsp_navigate')}: {action} {cyan(path)}")


LOG = log


_DOCUMENT_SYMBOLS = "document_symbols"
_DEFINITION = "definition"
_REFERENCES = "references"
_HOVER = "hover"
_VALID_ACTIONS = {_DOCUMENT_SYMBOLS, _DEFINITION, _REFERENCES, _HOVER}
_DEFAULT_MAX_RESULTS = 50

_SYMBOL_KIND_LABELS = {
    1: "file",
    2: "module",
    3: "namespace",
    4: "package",
    5: "class",
    6: "method",
    7: "property",
    8: "field",
    9: "constructor",
    10: "enum",
    11: "interface",
    12: "function",
    13: "variable",
    14: "constant",
    15: "string",
    16: "number",
    17: "boolean",
    18: "array",
    19: "object",
    20: "key",
    21: "null",
    22: "enum_member",
    23: "struct",
    24: "event",
    25: "operator",
    26: "type_parameter",
}

_SERVER_CANDIDATES = {
    ".py": [
        {"command": ["pyright-langserver", "--stdio"], "language_id": "python", "name": "pyright-langserver"},
        {"command": ["pylsp"], "language_id": "python", "name": "pylsp"},
    ],
    ".js": [
        {"command": ["typescript-language-server", "--stdio"], "language_id": "javascript", "name": "typescript-language-server"},
    ],
    ".jsx": [
        {"command": ["typescript-language-server", "--stdio"], "language_id": "javascriptreact", "name": "typescript-language-server"},
    ],
    ".mjs": [
        {"command": ["typescript-language-server", "--stdio"], "language_id": "javascript", "name": "typescript-language-server"},
    ],
    ".ts": [
        {"command": ["typescript-language-server", "--stdio"], "language_id": "typescript", "name": "typescript-language-server"},
    ],
    ".tsx": [
        {"command": ["typescript-language-server", "--stdio"], "language_id": "typescriptreact", "name": "typescript-language-server"},
    ],
    ".go": [
        {"command": ["gopls"], "language_id": "go", "name": "gopls"},
    ],
    ".rs": [
        {"command": ["rust-analyzer"], "language_id": "rust", "name": "rust-analyzer"},
    ],
}

_WORKSPACE_MARKERS = {
    ".py": ["pyproject.toml", "setup.py", "requirements.txt"],
    ".js": ["package.json", "tsconfig.json", "jsconfig.json"],
    ".jsx": ["package.json", "tsconfig.json", "jsconfig.json"],
    ".mjs": ["package.json", "tsconfig.json", "jsconfig.json"],
    ".ts": ["package.json", "tsconfig.json", "jsconfig.json"],
    ".tsx": ["package.json", "tsconfig.json", "jsconfig.json"],
    ".go": ["go.mod"],
    ".rs": ["Cargo.toml"],
}


class LspError(RuntimeError):
    """Raised when LSP navigation cannot be performed."""


def _path_to_uri(path):
    return "file://" + urllib.parse.quote(os.path.abspath(path))


def _uri_to_path(uri):
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme != "file":
        raise LspError(f"unsupported URI scheme from language server: {uri}")
    return os.path.abspath(urllib.parse.unquote(parsed.path))


def _find_workspace_root(path):
    path = os.path.abspath(path)
    current = os.path.dirname(path)
    ext = os.path.splitext(path)[1].lower()
    markers = _WORKSPACE_MARKERS.get(ext, [])

    while True:
        if any(os.path.exists(os.path.join(current, marker)) for marker in markers):
            return current
        if os.path.isdir(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return os.path.dirname(path)
        current = parent


def _find_executable(executable):
    found = shutil.which(executable)
    if found:
        return found

    candidate_dirs = []
    try:
        candidate_dirs.append(os.path.join(site.getuserbase(), "bin"))
    except Exception:
        pass

    for scheme in ("posix_user", "nt_user", "posix_prefix", "nt"):
        try:
            scripts_dir = sysconfig.get_path("scripts", scheme=scheme)
        except Exception:
            continue
        if scripts_dir:
            candidate_dirs.append(scripts_dir)

    candidate_dirs.extend(
        directory for directory in (
            os.path.expanduser("~/.local/bin"),
            os.path.expanduser(f"~/Library/Python/{sysconfig.get_python_version()}/bin"),
        )
        if directory
    )

    seen = set()
    for directory in candidate_dirs:
        normalized = os.path.normpath(directory)
        if normalized in seen:
            continue
        seen.add(normalized)
        candidate = os.path.join(normalized, executable)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _select_server_config(path):
    ext = os.path.splitext(path)[1].lower()
    candidates = _SERVER_CANDIDATES.get(ext)
    if not candidates:
        supported = ", ".join(sorted(_SERVER_CANDIDATES.keys()))
        raise LspError(
            f"no LSP configuration for {ext or 'files without an extension'}; "
            f"supported extensions: {supported}"
        )

    for candidate in candidates:
        executable = candidate["command"][0]
        resolved = _find_executable(executable)
        if resolved:
            return {
                **candidate,
                "command": [resolved] + candidate["command"][1:],
            }

    names = ", ".join(candidate["name"] for candidate in candidates)
    raise LspError(
        f"no supported language server found for {ext} files; install one of: {names}"
    )


def _get_line_text(text, line_number):
    lines = text.splitlines()
    if line_number < 1 or line_number > max(len(lines), 1):
        raise LspError(
            f"line ({line_number}) is out of range for the current file ({len(lines)} lines)"
        )
    if not lines:
        return ""
    return lines[line_number - 1]


def _to_lsp_character(line_text, column):
    if column < 1:
        raise LspError(f"column must be >= 1, got {column}")
    if column - 1 > len(line_text):
        raise LspError(
            f"column ({column}) exceeds line length ({len(line_text)} characters)"
        )
    prefix = line_text[: column - 1]
    return len(prefix.encode("utf-16-le")) // 2


def _from_lsp_character(line_text, character):
    utf16_units = 0
    codepoint_index = 0
    for codepoint_index, ch in enumerate(line_text):
        if utf16_units >= character:
            return codepoint_index + 1
        utf16_units += len(ch.encode("utf-16-le")) // 2
    return len(line_text) + 1


def _to_lsp_position(text, line, column):
    line_text = _get_line_text(text, line)
    return {
        "line": line - 1,
        "character": _to_lsp_character(line_text, column),
    }


def _preview_for_location(path, line):
    try:
        file_info = read_text_file(path)
    except Exception:
        return ""
    lines = file_info["content"].splitlines()
    if 1 <= line <= len(lines):
        return lines[line - 1].strip()
    return ""


def _normalize_hover_contents(contents):
    if contents is None:
        return ""
    if isinstance(contents, str):
        return contents
    if isinstance(contents, dict):
        if "value" in contents:
            return contents.get("value", "")
        if "language" in contents and "value" in contents:
            return f"{contents['language']}: {contents['value']}"
    if isinstance(contents, list):
        parts = [_normalize_hover_contents(item) for item in contents]
        return "\n\n".join(part for part in parts if part)
    return str(contents)


def _location_from_lsp(location):
    if "targetUri" in location:
        uri = location["targetUri"]
        start = location.get("targetSelectionRange", {}).get("start") or location.get("targetRange", {}).get("start")
    else:
        uri = location["uri"]
        start = location.get("range", {}).get("start")
    if not uri or start is None:
        raise LspError("language server returned a location without a target range")
    path = _uri_to_path(uri)

    try:
        file_info = read_text_file(path)
        lines = file_info["content"].splitlines()
        line_text = lines[start["line"]] if start["line"] < len(lines) else ""
    except Exception:
        line_text = ""

    return {
        "path": path,
        "line": start["line"] + 1,
        "column": _from_lsp_character(line_text, start.get("character", 0)),
        "preview": _preview_for_location(path, start["line"] + 1),
    }


def _flatten_document_symbols(symbols, depth=0):
    for symbol in symbols or []:
        if "location" in symbol:
            start = symbol["location"]["range"]["start"]
            kind = symbol.get("kind", 13)
            name = symbol.get("name", "<unnamed>")
            yield {
                "line": start["line"] + 1,
                "column": start.get("character", 0) + 1,
                "kind": _SYMBOL_KIND_LABELS.get(kind, f"kind_{kind}"),
                "name": name,
                "depth": depth,
            }
            continue

        selection_start = symbol.get("selectionRange", {}).get("start") or symbol.get("range", {}).get("start")
        if selection_start is None:
            continue
        kind = symbol.get("kind", 13)
        yield {
            "line": selection_start["line"] + 1,
            "column": selection_start.get("character", 0) + 1,
            "kind": _SYMBOL_KIND_LABELS.get(kind, f"kind_{kind}"),
            "name": symbol.get("name", "<unnamed>"),
            "depth": depth,
        }
        children = symbol.get("children") or []
        yield from _flatten_document_symbols(children, depth + 1)


def _format_locations(label, locations, max_results):
    if not locations:
        return f"(no {label} found)"

    total = len(locations)
    visible = locations[:max_results]
    output = [f"[{min(total, max_results)} {label}]"]
    for entry in visible:
        preview = f"  {entry['preview']}" if entry["preview"] else ""
        output.append(f"{entry['path']}:{entry['line']}:{entry['column']}{preview}")
    if total > max_results:
        output.append(
            f"... ({total - max_results} more {label} not shown; increase max_results from {max_results})"
        )
    return "\n".join(output)


class LspSession:
    """A cached stdio LSP session."""

    def __init__(self, config, workspace_root):
        self.config = config
        self.workspace_root = workspace_root
        self.workspace_uri = _path_to_uri(workspace_root)
        self._lock = threading.Lock()
        self._request_id = 0
        self._proc = subprocess.Popen(
            config["command"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._initialize()

    def _send(self, payload):
        if self._proc.stdin is None:
            raise LspError("language server stdin is not available")
        body = json.dumps(payload).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._proc.stdin.write(header)
        self._proc.stdin.write(body)
        self._proc.stdin.flush()

    def _read_message(self):
        if self._proc.stdout is None:
            raise LspError("language server stdout is not available")
        headers = {}
        while True:
            line = self._proc.stdout.readline()
            if not line:
                raise LspError("language server exited unexpectedly")
            if line in (b"\r\n", b"\n"):
                break
            key, _, value = line.decode("ascii", errors="replace").partition(":")
            headers[key.strip().lower()] = value.strip()

        length = int(headers.get("content-length", "0"))
        body = self._proc.stdout.read(length)
        return json.loads(body.decode("utf-8"))

    def _request(self, method, params):
        self._request_id += 1
        request_id = self._request_id
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        while True:
            message = self._read_message()
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"]
                raise LspError(f"{method} failed: {error.get('message', error)}")
            return message.get("result")

    def _notify(self, method, params):
        self._send(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }
        )

    def _initialize(self):
        self._request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": self.workspace_uri,
                "capabilities": {
                    "textDocument": {
                        "definition": {"linkSupport": True},
                        "hover": {"contentFormat": ["markdown", "plaintext"]},
                        "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                    }
                },
            },
        )
        self._notify("initialized", {})

    def run_request(self, method, path, language_id, text, params):
        uri = _path_to_uri(path)
        with self._lock:
            self._notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": language_id,
                        "version": 1,
                        "text": text,
                    }
                },
            )
            try:
                return self._request(method, params)
            finally:
                self._notify(
                    "textDocument/didClose",
                    {
                        "textDocument": {"uri": uri},
                    },
                )

    def close(self):
        with self._lock:
            try:
                self._request("shutdown", {})
            except Exception:
                pass
            try:
                self._notify("exit", {})
            except Exception:
                pass
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self._proc.kill()


class LspManager:
    """Caches LSP sessions per workspace/language server."""

    def __init__(self, session_factory=LspSession):
        self._session_factory = session_factory
        self._sessions = {}
        self._lock = threading.Lock()

    def get_session(self, path):
        config = _select_server_config(path)
        workspace_root = _find_workspace_root(path)
        key = (workspace_root, tuple(config["command"]))
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                session = self._session_factory(config, workspace_root)
                self._sessions[key] = session
            return session, config

    def close_all(self):
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions = {}
        for session in sessions:
            try:
                session.close()
            except Exception:
                pass


_LSP_MANAGER = LspManager()
atexit.register(_LSP_MANAGER.close_all)


def get_lsp_manager():
    return _LSP_MANAGER


def handle(params):
    action = params.get("action", "")
    path = _resolve(params.get("path", ""))
    max_results = params.get("max_results", _DEFAULT_MAX_RESULTS)

    if action not in _VALID_ACTIONS:
        return f"(error: unsupported action '{action}', expected one of: {', '.join(sorted(_VALID_ACTIONS))})"
    if max_results < 1:
        return f"(error: max_results must be >= 1, got {max_results})"
    if not os.path.isfile(path):
        return f"(error: file not found: {path})"

    try:
        file_info = read_text_file(path)
    except IsADirectoryError:
        return f"(error: {path} is a directory, use list_directory instead)"
    except Exception as e:
        return f"(error reading {path}: {e})"

    text = file_info["content"]

    try:
        session, config = get_lsp_manager().get_session(path)
    except LspError as e:
        return f"(error: {e})"

    if action == _DOCUMENT_SYMBOLS:
        method = "textDocument/documentSymbol"
        request_params = {"textDocument": {"uri": _path_to_uri(path)}}
    else:
        line = params.get("line")
        column = params.get("column")
        if line is None or column is None:
            return f"(error: line and column are required for {action})"
        try:
            position = _to_lsp_position(text, line, column)
        except LspError as e:
            return f"(error: {e})"
        request_params = {
            "textDocument": {"uri": _path_to_uri(path)},
            "position": position,
        }
        if action == _DEFINITION:
            method = "textDocument/definition"
        elif action == _REFERENCES:
            method = "textDocument/references"
            request_params["context"] = {
                "includeDeclaration": bool(params.get("include_declaration", False))
            }
        else:
            method = "textDocument/hover"

    try:
        result = session.run_request(method, path, config["language_id"], text, request_params)
    except LspError as e:
        return f"(error: {e})"
    except Exception as e:
        return f"(error running LSP request: {e})"

    if action == _DOCUMENT_SYMBOLS:
        symbols = list(_flatten_document_symbols(result or []))
        if not symbols:
            return f"(no document symbols found in {path})"
        total = len(symbols)
        visible = symbols[:max_results]
        output = [f"[{min(total, max_results)} document symbols in {path}]"]
        for symbol in visible:
            indent = "  " * symbol["depth"]
            output.append(
                f"{symbol['line']:6}:{symbol['column']:<4} {indent}{symbol['kind']} {symbol['name']}"
            )
        if total > max_results:
            output.append(
                f"(truncated; increase max_results from {max_results} to see more symbols)"
            )
        return "\n".join(output)

    if action == _DEFINITION:
        if not result:
            return "(no definition found)"
        locations = result if isinstance(result, list) else [result]
        normalized = [_location_from_lsp(location) for location in locations]
        return _format_locations("definitions", normalized, max_results)

    if action == _REFERENCES:
        normalized = [_location_from_lsp(location) for location in (result or [])]
        return _format_locations("references", normalized, max_results)

    hover_text = _normalize_hover_contents((result or {}).get("contents"))
    if not hover_text.strip():
        return "(no hover information found)"
    return f"[hover {path}:{params['line']}:{params['column']}]\n{hover_text.strip()}"
