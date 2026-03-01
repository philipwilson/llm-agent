"""Shared state and utilities for tool handlers."""

import os
import signal
import subprocess
import sys
import threading

from llm_agent.formatting import truncate


DEFAULT_COMMAND_TIMEOUT = 30
COMMAND_TIMEOUT = DEFAULT_COMMAND_TIMEOUT


class BackgroundTask:
    """A background process with an output-reader thread."""

    def __init__(self, task_id, command, proc):
        self.task_id = task_id
        self.command = command
        self.proc = proc
        self.output = []
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._read_output, daemon=True)
        self._reader.start()

    def _read_output(self):
        """Read stdout/stderr into a buffer so pipes don't fill and block."""
        for line in self.proc.stdout:
            with self._lock:
                self.output.append(line)
        # Also drain stderr after stdout closes
        if self.proc.stderr:
            for line in self.proc.stderr:
                with self._lock:
                    self.output.append(f"[stderr] {line}")

    @property
    def status(self):
        rc = self.proc.poll()
        if rc is None:
            return "running"
        return "completed" if rc == 0 else "failed"

    @property
    def exit_code(self):
        return self.proc.poll()

    def get_output(self):
        with self._lock:
            return "".join(self.output)


class ShellState:
    """Tracks working directory across commands."""

    def __init__(self):
        self.cwd = os.getcwd()
        self._tasks = {}
        self._task_counter = 0

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

    def start_background(self, command):
        """Spawn a background process and return its task ID."""
        self._task_counter += 1
        task_id = f"bg-{self._task_counter}"

        use_session = sys.platform != "win32"
        proc = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=self.cwd,
            start_new_session=use_session,
        )
        self._tasks[task_id] = BackgroundTask(task_id, command, proc)
        return task_id

    def get_task(self, task_id):
        """Return status info for a background task."""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        return {
            "task_id": task.task_id,
            "command": task.command,
            "status": task.status,
            "exit_code": task.exit_code,
            "output": task.get_output(),
        }

    def list_tasks(self):
        """Return a summary of all background tasks."""
        return [
            {
                "task_id": t.task_id,
                "command": t.command,
                "status": t.status,
                "exit_code": t.exit_code,
            }
            for t in self._tasks.values()
        ]

    def stop_all(self):
        """Terminate all running background processes."""
        for task in self._tasks.values():
            if task.proc.poll() is None:
                try:
                    if sys.platform != "win32":
                        os.killpg(task.proc.pid, signal.SIGTERM)
                    else:
                        task.proc.kill()
                except OSError:
                    pass


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
