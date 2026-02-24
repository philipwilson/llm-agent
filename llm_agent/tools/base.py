"""Shared state and utilities for tool handlers."""

import os
import signal
import subprocess
import sys

from llm_agent.formatting import truncate


DEFAULT_COMMAND_TIMEOUT = 30
COMMAND_TIMEOUT = DEFAULT_COMMAND_TIMEOUT


class ShellState:
    """Tracks working directory across commands."""

    def __init__(self):
        self.cwd = os.getcwd()

    def run(self, command):
        try:
            # Run the command, then capture the shell's cwd afterwards
            # so that cd (and pushd, popd, etc.) are reflected.
            wrapped = f'{command}\necho "__CWD__:$(pwd)"'

            # Use a new session on Unix so we can kill the entire process
            # group on timeout (prevents orphaned child processes).
            use_session = sys.platform != "win32"

            proc = subprocess.Popen(
                wrapped,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=self.cwd,
                start_new_session=use_session,
            )
            try:
                stdout, stderr = proc.communicate(timeout=COMMAND_TIMEOUT)
            except subprocess.TimeoutExpired:
                # Kill the entire process group to clean up child processes
                if use_session:
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except OSError:
                        proc.kill()
                else:
                    proc.kill()
                proc.wait()
                return f"(command timed out after {COMMAND_TIMEOUT}s)"

            # Extract and update cwd from the trailing __CWD__ line
            lines = stdout.splitlines()
            if lines and lines[-1].startswith("__CWD__:"):
                new_cwd = lines[-1].split(":", 1)[1]
                if os.path.isdir(new_cwd):
                    self.cwd = new_cwd
                stdout = "\n".join(lines[:-1])

            output = ""
            if stdout:
                output += stdout
            if stderr:
                if output:
                    output += "\n"
                output += f"[stderr]\n{stderr}"
            if not output:
                output = "(no output)"
            return truncate(output)
        except Exception as e:
            return f"(error running command: {e})"


shell = ShellState()


def _resolve(path):
    """Resolve a path relative to the shell's working directory."""
    if os.path.isabs(path):
        return path
    return os.path.join(shell.cwd, path)


def confirm_edit(prompt_lines):
    """Show a preview and ask for Y/n confirmation."""
    from llm_agent.display import get_display
    return get_display().confirm(prompt_lines, "Apply? [Y/n]")
