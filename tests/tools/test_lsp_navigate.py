"""Tests for lsp_navigate tool."""

from llm_agent.tools.base import shell
from llm_agent.tools.lsp_navigate import (
    LspError,
    LspManager,
    _find_executable,
    handle,
)


class FakeSession:
    def __init__(self, config, workspace_root):
        self.config = config
        self.workspace_root = workspace_root
        self.calls = []
        self.responses = {}

    def run_request(self, method, path, language_id, text, params):
        self.calls.append(
            {
                "method": method,
                "path": path,
                "language_id": language_id,
                "text": text,
                "params": params,
            }
        )
        return self.responses.get(method)

    def close(self):
        pass


class FakeManager:
    def __init__(self, session, config=None, error=None):
        self.session = session
        self.config = config or {"language_id": "python"}
        self.error = error

    def get_session(self, path):
        if self.error:
            raise self.error
        return self.session, self.config


class TestLspNavigateHandle:
    def test_document_symbols(self, tmp_path, monkeypatch):
        file_path = tmp_path / "example.py"
        file_path.write_text("class MyClass:\n    def method(self):\n        pass\n")
        shell.cwd = str(tmp_path)

        session = FakeSession({"language_id": "python"}, str(tmp_path))
        session.responses["textDocument/documentSymbol"] = [
            {
                "name": "MyClass",
                "kind": 5,
                "selectionRange": {"start": {"line": 0, "character": 0}},
                "children": [
                    {
                        "name": "method",
                        "kind": 6,
                        "selectionRange": {"start": {"line": 1, "character": 4}},
                    }
                ],
            }
        ]
        monkeypatch.setattr(
            "llm_agent.tools.lsp_navigate.get_lsp_manager",
            lambda: FakeManager(session),
        )

        result = handle({"action": "document_symbols", "path": "example.py"})

        assert "document symbols" in result
        assert "class MyClass" in result
        assert "method method" in result

    def test_definition_requires_position(self, tmp_path, monkeypatch):
        file_path = tmp_path / "example.py"
        file_path.write_text("value = other\n")
        shell.cwd = str(tmp_path)
        monkeypatch.setattr(
            "llm_agent.tools.lsp_navigate.get_lsp_manager",
            lambda: FakeManager(FakeSession({"language_id": "python"}, str(tmp_path))),
        )

        result = handle({"action": "definition", "path": "example.py"})

        assert "line and column are required" in result

    def test_definition_formats_locations(self, tmp_path, monkeypatch):
        source = tmp_path / "example.py"
        target = tmp_path / "target.py"
        source.write_text("value = target\n")
        target.write_text("def target():\n    pass\n")
        shell.cwd = str(tmp_path)

        session = FakeSession({"language_id": "python"}, str(tmp_path))
        session.responses["textDocument/definition"] = {
            "uri": target.as_uri(),
            "range": {"start": {"line": 0, "character": 4}},
        }
        monkeypatch.setattr(
            "llm_agent.tools.lsp_navigate.get_lsp_manager",
            lambda: FakeManager(session),
        )

        result = handle({"action": "definition", "path": "example.py", "line": 1, "column": 9})

        assert str(target) in result
        assert "def target():" in result

    def test_references_respect_max_results(self, tmp_path, monkeypatch):
        source = tmp_path / "example.py"
        source.write_text("value = item\nitem = 1\nprint(item)\n")
        shell.cwd = str(tmp_path)

        session = FakeSession({"language_id": "python"}, str(tmp_path))
        session.responses["textDocument/references"] = [
            {"uri": source.as_uri(), "range": {"start": {"line": 0, "character": 8}}},
            {"uri": source.as_uri(), "range": {"start": {"line": 1, "character": 0}}},
            {"uri": source.as_uri(), "range": {"start": {"line": 2, "character": 6}}},
        ]
        monkeypatch.setattr(
            "llm_agent.tools.lsp_navigate.get_lsp_manager",
            lambda: FakeManager(session),
        )

        result = handle(
            {
                "action": "references",
                "path": "example.py",
                "line": 1,
                "column": 9,
                "max_results": 2,
            }
        )

        assert "[2 references]" in result
        assert "more references not shown" in result

    def test_hover_formats_markup_content(self, tmp_path, monkeypatch):
        source = tmp_path / "example.py"
        source.write_text("value = 1\n")
        shell.cwd = str(tmp_path)

        session = FakeSession({"language_id": "python"}, str(tmp_path))
        session.responses["textDocument/hover"] = {
            "contents": {"kind": "markdown", "value": "```python\nint\n```"}
        }
        monkeypatch.setattr(
            "llm_agent.tools.lsp_navigate.get_lsp_manager",
            lambda: FakeManager(session),
        )

        result = handle({"action": "hover", "path": "example.py", "line": 1, "column": 1})

        assert "[hover" in result
        assert "int" in result

    def test_reports_missing_server(self, tmp_path, monkeypatch):
        source = tmp_path / "example.py"
        source.write_text("value = 1\n")
        shell.cwd = str(tmp_path)
        monkeypatch.setattr(
            "llm_agent.tools.lsp_navigate.get_lsp_manager",
            lambda: FakeManager(None, error=LspError("no supported language server found")),
        )

        result = handle({"action": "document_symbols", "path": "example.py"})

        assert "no supported language server found" in result

    def test_invalid_action(self, tmp_path):
        source = tmp_path / "example.py"
        source.write_text("value = 1\n")
        shell.cwd = str(tmp_path)

        result = handle({"action": "weird", "path": "example.py"})

        assert "unsupported action" in result


class TestLspManager:
    def test_caches_sessions_for_same_workspace(self, tmp_path, monkeypatch):
        source = tmp_path / "example.py"
        source.write_text("value = 1\n")

        created = []

        def factory(config, workspace_root):
            session = FakeSession(config, workspace_root)
            created.append(session)
            return session

        monkeypatch.setattr("llm_agent.tools.lsp_navigate.shutil.which", lambda name: "/usr/bin/" + name)
        manager = LspManager(session_factory=factory)

        first, _ = manager.get_session(str(source))
        second, _ = manager.get_session(str(source))

        assert first is second
        assert len(created) == 1


class TestExecutableDiscovery:
    def test_finds_userbase_script_when_not_on_path(self, tmp_path, monkeypatch):
        userbase = tmp_path / "userbase"
        bin_dir = userbase / "bin"
        bin_dir.mkdir(parents=True)
        script = bin_dir / "pylsp"
        script.write_text("#!/bin/sh\n")
        script.chmod(0o755)

        monkeypatch.setattr("llm_agent.tools.lsp_navigate.shutil.which", lambda name: None)
        monkeypatch.setattr("llm_agent.tools.lsp_navigate.site.getuserbase", lambda: str(userbase))
        monkeypatch.setattr("llm_agent.tools.lsp_navigate.sysconfig.get_path", lambda *args, **kwargs: None)

        result = _find_executable("pylsp")

        assert result == str(script)
