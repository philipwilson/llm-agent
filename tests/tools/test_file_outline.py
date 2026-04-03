"""Tests for file_outline tool."""

import os

import pytest

from llm_agent.tools.file_outline import handle, _extract_symbols, _PYTHON, _JAVASCRIPT, _GO, _RUST
from llm_agent.tools.base import shell


class TestExtractSymbolsPython:
    def test_class(self):
        lines = ["class MyClass:"]
        symbols = _extract_symbols(lines, _PYTHON)
        assert len(symbols) == 1
        assert symbols[0] == (1, 0, "class MyClass")

    def test_function(self):
        lines = ["def my_func(a, b):"]
        symbols = _extract_symbols(lines, _PYTHON)
        assert len(symbols) == 1
        assert "def my_func(a, b" in symbols[0][2]

    def test_async_function(self):
        lines = ["async def fetch(url):"]
        symbols = _extract_symbols(lines, _PYTHON)
        assert len(symbols) == 1
        assert "async def fetch" in symbols[0][2]

    def test_method_indentation(self):
        lines = ["class Foo:", "    def bar(self):"]
        symbols = _extract_symbols(lines, _PYTHON)
        assert len(symbols) == 2
        assert symbols[0][1] == 0   # class at indent 0
        assert symbols[1][1] == 4   # method at indent 4

    def test_nested_class(self):
        lines = ["class Outer:", "    class Inner:"]
        symbols = _extract_symbols(lines, _PYTHON)
        assert len(symbols) == 2
        assert symbols[1][1] == 4

    def test_no_symbols(self):
        lines = ["x = 1", "y = 2", "# just comments"]
        symbols = _extract_symbols(lines, _PYTHON)
        assert len(symbols) == 0


class TestExtractSymbolsJavaScript:
    def test_function(self):
        lines = ["function greet(name) {"]
        symbols = _extract_symbols(lines, _JAVASCRIPT)
        assert len(symbols) == 1
        assert "function greet" in symbols[0][2]

    def test_class(self):
        lines = ["class Component {"]
        symbols = _extract_symbols(lines, _JAVASCRIPT)
        assert len(symbols) == 1
        assert "class Component" in symbols[0][2]

    def test_export_default(self):
        lines = ["export default class App {"]
        symbols = _extract_symbols(lines, _JAVASCRIPT)
        assert len(symbols) == 1
        assert "class App" in symbols[0][2]

    def test_const_arrow(self):
        lines = ["const handler = (req, res) => {"]
        symbols = _extract_symbols(lines, _JAVASCRIPT)
        assert len(symbols) == 1
        assert "handler" in symbols[0][2]

    def test_interface(self):
        lines = ["export interface Props {"]
        symbols = _extract_symbols(lines, _JAVASCRIPT)
        assert len(symbols) == 1
        assert "interface Props" in symbols[0][2]

    def test_type_alias(self):
        lines = ["type Config = {"]
        symbols = _extract_symbols(lines, _JAVASCRIPT)
        assert len(symbols) == 1
        assert "type Config" in symbols[0][2]

    def test_enum(self):
        lines = ["enum Color {"]
        symbols = _extract_symbols(lines, _JAVASCRIPT)
        assert len(symbols) == 1
        assert "enum Color" in symbols[0][2]


class TestExtractSymbolsGo:
    def test_function(self):
        lines = ["func main() {"]
        symbols = _extract_symbols(lines, _GO)
        assert len(symbols) == 1
        assert "func main()" in symbols[0][2]

    def test_method(self):
        lines = ["func (s *Server) Start() {"]
        symbols = _extract_symbols(lines, _GO)
        assert len(symbols) == 1
        assert "Server" in symbols[0][2]
        assert "Start" in symbols[0][2]

    def test_struct(self):
        lines = ["type Config struct {"]
        symbols = _extract_symbols(lines, _GO)
        assert len(symbols) == 1
        assert "type Config struct" in symbols[0][2]

    def test_interface(self):
        lines = ["type Handler interface {"]
        symbols = _extract_symbols(lines, _GO)
        assert len(symbols) == 1
        assert "type Handler interface" in symbols[0][2]


class TestExtractSymbolsRust:
    def test_struct(self):
        lines = ["pub struct Config {"]
        symbols = _extract_symbols(lines, _RUST)
        assert len(symbols) == 1
        assert "pub struct Config" in symbols[0][2]

    def test_fn(self):
        lines = ["fn process(data: &str) {"]
        symbols = _extract_symbols(lines, _RUST)
        assert len(symbols) == 1
        assert "fn process" in symbols[0][2]

    def test_impl(self):
        lines = ["impl Server {"]
        symbols = _extract_symbols(lines, _RUST)
        assert len(symbols) == 1
        assert "impl Server" in symbols[0][2]

    def test_trait(self):
        lines = ["pub trait Handler {"]
        symbols = _extract_symbols(lines, _RUST)
        assert len(symbols) == 1
        assert "pub trait Handler" in symbols[0][2]

    def test_enum(self):
        lines = ["enum Direction {"]
        symbols = _extract_symbols(lines, _RUST)
        assert len(symbols) == 1
        assert "enum Direction" in symbols[0][2]


class TestHandle:
    def test_python_file(self, tmp_path):
        f = tmp_path / "example.py"
        f.write_text(
            "class MyClass:\n"
            "    def method(self):\n"
            "        pass\n"
            "\n"
            "def standalone():\n"
            "    pass\n"
        )
        shell.cwd = str(tmp_path)
        result = handle({"path": "example.py"})
        assert "3 symbols" in result
        assert "class MyClass" in result
        assert "def method" in result
        assert "def standalone" in result

    def test_javascript_file(self, tmp_path):
        f = tmp_path / "app.js"
        f.write_text(
            "class App {\n"
            "}\n"
            "function init() {\n"
            "}\n"
        )
        shell.cwd = str(tmp_path)
        result = handle({"path": "app.js"})
        assert "class App" in result
        assert "function init" in result

    def test_no_symbols(self, tmp_path):
        f = tmp_path / "data.py"
        f.write_text("x = 1\ny = 2\n")
        shell.cwd = str(tmp_path)
        result = handle({"path": "data.py"})
        assert "no symbols found" in result

    def test_file_not_found(self, tmp_path):
        shell.cwd = str(tmp_path)
        result = handle({"path": "nonexistent.py"})
        assert "error" in result

    def test_directory_error(self, tmp_path):
        shell.cwd = str(tmp_path)
        result = handle({"path": str(tmp_path)})
        assert "directory" in result

    def test_unknown_extension_uses_fallback(self, tmp_path):
        f = tmp_path / "script.lua"
        f.write_text("function hello()\nend\n")
        shell.cwd = str(tmp_path)
        result = handle({"path": "script.lua"})
        assert "function hello" in result

    def test_line_numbers_present(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("# comment\n\ndef func():\n    pass\n")
        shell.cwd = str(tmp_path)
        result = handle({"path": "test.py"})
        # Line 3 should be where def func() is
        assert "3" in result
        assert "def func" in result

    def test_typescript_file(self, tmp_path):
        f = tmp_path / "types.ts"
        f.write_text(
            "interface User {\n"
            "  name: string;\n"
            "}\n"
            "type ID = string;\n"
        )
        shell.cwd = str(tmp_path)
        result = handle({"path": "types.ts"})
        assert "interface User" in result
        assert "type ID" in result

    def test_kind_filter(self, tmp_path):
        f = tmp_path / "example.py"
        f.write_text(
            "class MyClass:\n"
            "    def method(self):\n"
            "        pass\n"
            "\n"
            "def standalone():\n"
            "    pass\n"
        )
        shell.cwd = str(tmp_path)
        result = handle({"path": "example.py", "kinds": ["class"]})
        assert "class MyClass" in result
        assert "def method" not in result
        assert "def standalone" not in result

    def test_max_symbols_truncates(self, tmp_path):
        f = tmp_path / "many.py"
        f.write_text(
            "def one():\n    pass\n"
            "def two():\n    pass\n"
            "def three():\n    pass\n"
        )
        shell.cwd = str(tmp_path)
        result = handle({"path": "many.py", "max_symbols": 2})
        assert "def one" in result
        assert "def two" in result
        assert "def three" not in result
        assert "increase max_symbols from 2" in result

    def test_invalid_kind(self, tmp_path):
        f = tmp_path / "example.py"
        f.write_text("def hello():\n    pass\n")
        shell.cwd = str(tmp_path)
        result = handle({"path": "example.py", "kinds": ["weird"]})
        assert "unsupported symbol kinds" in result
