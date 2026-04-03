"""Shared state and utilities for tool handlers."""

import errno
import os
import pty
import select
import signal
import subprocess
import sys
import threading
import time

from llm_agent.formatting import truncate


DEFAULT_COMMAND_TIMEOUT = 30
COMMAND_TIMEOUT = DEFAULT_COMMAND_TIMEOUT


class BackgroundTask:
    """A background process with concurrent stdout/stderr readers."""

    _READ_CHUNK_SIZE = 4096

    def __init__(self, task_id, command, cwd, proc):
        self.task_id = task_id
        self.command = command
        self.cwd = cwd
        self.proc = proc
        self.pid = proc.pid
        self.started_at = time.time()
        self.finished_at = None
        self.stdout_chunks = []
        self.stderr_chunks = []
        self._lock = threading.Lock()
        self._stdout_reader = threading.Thread(
            target=self._read_stream,
            args=(self.proc.stdout, self.stdout_chunks),
            daemon=True,
        )
        self._stderr_reader = threading.Thread(
            target=self._read_stream,
            args=(self.proc.stderr, self.stderr_chunks),
            daemon=True,
        )
        self._waiter = threading.Thread(target=self._wait_for_exit, daemon=True)
        self._stdout_reader.start()
        self._stderr_reader.start()
        self._waiter.start()

    def _read_stream(self, stream, target):
        """Drain a single stream in chunks so large outputs cannot block."""
        if stream is None:
            return
        try:
            while True:
                chunk = stream.read(self._READ_CHUNK_SIZE)
                if not chunk:
                    break
                with self._lock:
                    target.append(chunk)
        finally:
            stream.close()

    def _wait_for_exit(self):
        self.proc.wait()
        with self._lock:
            if self.finished_at is None:
                self.finished_at = time.time()

    @property
    def status(self):
        rc = self.proc.poll()
        if rc is None:
            return "running"
        return "completed" if rc == 0 else "failed"

    @property
    def exit_code(self):
        return self.proc.poll()

    @property
    def duration_seconds(self):
        end = self.finished_at
        if end is None and self.proc.poll() is not None:
            end = time.time()
        if end is None:
            end = time.time()
        return max(0.0, end - self.started_at)

    def _join_chunks(self, chunks):
        with self._lock:
            return "".join(chunks)

    def get_stdout(self):
        return self._join_chunks(self.stdout_chunks)

    def get_stderr(self):
        return self._join_chunks(self.stderr_chunks)

    def get_output(self, tail_lines=None):
        stdout = self.get_stdout().rstrip("\n")
        stderr = self.get_stderr().rstrip("\n")

        parts = []
        if stdout:
            parts.append(stdout)
        if stderr:
            parts.append(f"[stderr]\n{stderr}")

        output = "\n".join(parts)
        if not output:
            return ""

        if tail_lines is not None:
            lines = output.splitlines()
            return "\n".join(lines[-tail_lines:])
        return output

    def get_output_line_count(self):
        output = self.get_output()
        if not output:
            return 0
        return len(output.splitlines())


def _terminate_process(proc, wait_timeout=1.0):
    """Best-effort termination of a process and its children."""
    if proc.poll() is not None:
        return
    try:
        if sys.platform != "win32":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except OSError:
        return
    try:
        proc.wait(timeout=wait_timeout)
        return
    except subprocess.TimeoutExpired:
        pass

    try:
        if sys.platform != "win32":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except OSError:
        return
    try:
        proc.wait(timeout=wait_timeout)
    except subprocess.TimeoutExpired:
        pass


class PtySession:
    """A PTY-backed interactive process with incremental output reads."""

    _READ_CHUNK_SIZE = 4096

    def __init__(self, session_id, command, cwd, proc, master_fd):
        self.session_id = session_id
        self.command = command
        self.cwd = cwd
        self.proc = proc
        self.master_fd = master_fd
        self.pid = proc.pid
        self.started_at = time.time()
        self.finished_at = None
        self._output_buffer = []
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._read_output, daemon=True)
        self._waiter = threading.Thread(target=self._wait_for_exit, daemon=True)
        self._reader.start()
        self._waiter.start()

    def _read_output(self):
        try:
            while True:
                timeout = 0 if self.proc.poll() is not None else 0.1
                ready, _, _ = select.select([self.master_fd], [], [], timeout)
                if not ready:
                    if self.proc.poll() is not None:
                        break
                    continue
                try:
                    chunk = os.read(self.master_fd, self._READ_CHUNK_SIZE)
                except OSError as e:
                    if e.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                text = chunk.decode(errors="replace").replace("\r\n", "\n")
                with self._lock:
                    self._output_buffer.append(text)
        finally:
            try:
                os.close(self.master_fd)
            except OSError:
                pass

    def _wait_for_exit(self):
        self.proc.wait()
        with self._lock:
            if self.finished_at is None:
                self.finished_at = time.time()

    @property
    def status(self):
        rc = self.proc.poll()
        if rc is None:
            return "running"
        return "completed" if rc == 0 else "failed"

    @property
    def exit_code(self):
        return self.proc.poll()

    @property
    def duration_seconds(self):
        end = self.finished_at
        if end is None and self.proc.poll() is not None:
            end = time.time()
        if end is None:
            end = time.time()
        return max(0.0, end - self.started_at)

    def write(self, chars):
        if self.proc.poll() is not None:
            return False
        os.write(self.master_fd, chars.encode())
        return True

    def drain_output(self, max_chars=None):
        with self._lock:
            output = "".join(self._output_buffer)
            if not output:
                return ""
            if max_chars is None or len(output) <= max_chars:
                self._output_buffer.clear()
                return output
            chunk = output[:max_chars]
            remaining = output[max_chars:]
            self._output_buffer = [remaining] if remaining else []
            return chunk

    def terminate(self):
        _terminate_process(self.proc)

    def snapshot(self):
        with self._lock:
            buffered_chars = sum(len(chunk) for chunk in self._output_buffer)
        return {
            "session_id": self.session_id,
            "command": self.command,
            "cwd": self.cwd,
            "pid": self.pid,
            "status": self.status,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "buffered_chars": buffered_chars,
        }


class ShellState:
    """Tracks working directory across commands."""

    def __init__(self):
        self.cwd = os.getcwd()
        self._tasks = {}
        self._task_counter = 0
        self._sessions = {}
        self._session_counter = 0

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
                _terminate_process(proc)
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
        self._tasks[task_id] = BackgroundTask(task_id, command, self.cwd, proc)
        return task_id

    def start_session(self, command):
        """Spawn a PTY-backed interactive process and return its session ID."""
        if sys.platform == "win32":
            raise RuntimeError("PTY sessions are unsupported on Windows")

        self._session_counter += 1
        session_id = f"pty-{self._session_counter}"
        master_fd, slave_fd = pty.openpty()
        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=self.cwd,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            os.close(slave_fd)
        self._sessions[session_id] = PtySession(
            session_id,
            command,
            self.cwd,
            proc,
            master_fd,
        )
        return session_id

    def get_task(self, task_id, tail_lines=None):
        """Return status info for a background task."""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        return {
            "task_id": task.task_id,
            "command": task.command,
            "cwd": task.cwd,
            "pid": task.pid,
            "status": task.status,
            "exit_code": task.exit_code,
            "started_at": task.started_at,
            "finished_at": task.finished_at,
            "duration_seconds": task.duration_seconds,
            "stdout": task.get_stdout(),
            "stderr": task.get_stderr(),
            "output": task.get_output(tail_lines=tail_lines),
            "output_line_count": task.get_output_line_count(),
        }

    def list_tasks(self):
        """Return a summary of all background tasks."""
        return [
            {
                "task_id": t.task_id,
                "command": t.command,
                "cwd": t.cwd,
                "pid": t.pid,
                "status": t.status,
                "exit_code": t.exit_code,
                "started_at": t.started_at,
                "finished_at": t.finished_at,
                "duration_seconds": t.duration_seconds,
            }
            for t in self._tasks.values()
        ]

    def get_session(self, session_id):
        """Return metadata for an interactive session."""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        return session.snapshot()

    def list_sessions(self):
        """Return a summary of all interactive sessions."""
        return [session.snapshot() for session in self._sessions.values()]

    def write_session(self, session_id, chars="", wait_ms=200, max_output_chars=None):
        """Write to a PTY session and return its new output."""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if chars:
            try:
                session.write(chars)
            except OSError:
                pass
        if wait_ms > 0:
            time.sleep(wait_ms / 1000.0)
        return {
            **session.snapshot(),
            "output": session.drain_output(max_output_chars),
        }

    def terminate_session(self, session_id, wait_ms=200, max_output_chars=None):
        """Terminate a PTY session and return its final output snapshot."""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        session.terminate()
        if wait_ms > 0:
            time.sleep(wait_ms / 1000.0)
        return {
            **session.snapshot(),
            "output": session.drain_output(max_output_chars),
        }

    def stop_all(self):
        """Terminate all running background processes and PTY sessions."""
        for task in self._tasks.values():
            _terminate_process(task.proc)
        for session in self._sessions.values():
            session.terminate()


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
