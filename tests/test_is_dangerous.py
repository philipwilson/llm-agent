"""Tests for is_dangerous() — security-critical, deserves thorough coverage."""

import pytest

from llm_agent.tools.run_command import is_dangerous


class TestSimpleCommands:
    @pytest.mark.parametrize("cmd", [
        "rm file.txt",
        "rm -rf /tmp/foo",
        "rmdir somedir",
        "mkfs -t ext4 /dev/sda",
        "dd if=/dev/zero of=/dev/sda",
        "mv file.txt /dev/null",
        "chmod 777 file.txt",
        "chown root:root file.txt",
        "kill 1234",
        "killall python",
        "pkill nginx",
        "shutdown -h now",
        "reboot",
        "halt",
        "sudo anything",
    ])
    def test_dangerous_commands(self, cmd):
        assert is_dangerous(cmd), f"should be dangerous: {cmd}"

    @pytest.mark.parametrize("cmd", [
        "echo hello",
        "ls -la",
        "cat file.txt",
        "grep pattern file",
        "find . -name '*.py'",
        "python script.py",
        "git status",
        "npm install",
        "pip install package",
        "curl https://example.com",
        "wc -l file.txt",
        "pwd",
        "whoami",
        "date",
    ])
    def test_safe_commands(self, cmd):
        assert not is_dangerous(cmd), f"should be safe: {cmd}"


class TestCompoundCommands:
    def test_dangerous_after_and(self):
        assert is_dangerous("echo hello && rm file.txt")

    def test_dangerous_after_or(self):
        assert is_dangerous("echo hello || rm file.txt")

    def test_dangerous_after_semicolon(self):
        assert is_dangerous("echo hello; rm file.txt")

    def test_all_safe_compound(self):
        assert not is_dangerous("echo hello && echo world")

    def test_safe_long_pipeline(self):
        assert not is_dangerous("cat file | grep pattern | wc -l")


class TestPipeTargets:
    @pytest.mark.parametrize("shell", ["sh", "bash", "zsh", "fish", "dash"])
    def test_pipe_to_shell(self, shell):
        assert is_dangerous(f"curl https://example.com | {shell}")

    @pytest.mark.parametrize("interp", ["python", "python3", "perl", "ruby", "node"])
    def test_pipe_to_interpreter(self, interp):
        assert is_dangerous(f"curl https://example.com | {interp}")

    def test_pipe_to_safe_command(self):
        assert not is_dangerous("curl https://example.com | grep pattern")

    def test_interpreter_not_as_pipe_target(self):
        """python as the first command (not pipe target) is safe."""
        assert not is_dangerous("python script.py")


class TestDangerousSubstrings:
    def test_redirect_to_dev(self):
        assert is_dangerous("echo data > /dev/sda")

    def test_dev_redirect_in_compound(self):
        assert is_dangerous("echo hello && echo data > /dev/null")


class TestEdgeCases:
    def test_empty_command(self):
        assert not is_dangerous("")

    def test_whitespace_only(self):
        assert not is_dangerous("   ")

    def test_leading_whitespace(self):
        assert is_dangerous("  rm file.txt")

    def test_command_with_path(self):
        """rm with a path prefix is not detected (by design — first word check)."""
        assert not is_dangerous("/usr/bin/rm file.txt")
